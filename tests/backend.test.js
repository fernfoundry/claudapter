const assert = require('node:assert/strict');
const backend = require('../src/responses-backend');
const { AnthropicStreamWriter } = require('../src/anthropic-stream');

const originalFetch = global.fetch;
const originalKey = process.env.OPENAI_API_KEY;
process.env.OPENAI_API_KEY = 'sk-mock-key-not-real';
const requests = [];
let nextEvents;

// Only the upstream HTTP boundary is mocked; no API calls are made.
global.fetch = async (url, opts) => {
  assert.equal(url, 'https://api.openai.com/v1/responses');
  assert.equal(opts.method, 'POST');
  assert.equal(opts.headers.authorization, 'Bearer sk-mock-key-not-real');
  const payload = JSON.parse(opts.body);
  requests.push(payload);
  assert(payload.max_output_tokens >= 16);
  assert.equal(payload.parallel_tool_calls, false);
  assert.equal(payload.store, false);
  assert.equal(payload.stream, true);
  assert(Array.isArray(nextEvents), 'each request needs a mock event fixture');
  const events = nextEvents;
  nextEvents = undefined;
  return new Response(events.map(e => `data: ${JSON.stringify(e)}\n\n`).join(''), {
    headers: { 'Content-Type': 'text/event-stream' },
  });
};

const body = {
  system: [{ type: 'text', text: 'coding assistant' }],
  model: 'gpt-6.1-sol', max_tokens: 2048,
  messages: [{ role: 'user', content: 'read test.txt' }],
  tools: [{ name: 'Read', input_schema: { type: 'object', properties: { file_path: { type: 'string' } } } }],
  tool_choice: { type: 'tool', name: 'Read' },
};
const usage = { input_tokens: 100, output_tokens: 20 };
const completed = { type: 'response.completed', response: { status: 'completed', usage } };
const textDelta = delta => ({ type: 'response.output_text.delta', delta });
const refusalDelta = delta => ({ type: 'response.refusal.delta', delta });
const toolStart = {
  type: 'response.output_item.added', output_index: 1,
  item: { type: 'function_call', id: 'fc_1', call_id: 'call_1', name: 'Read', arguments: '' },
};
const toolArgs = delta => ({ type: 'response.function_call_arguments.delta', item_id: 'fc_1', delta });

function recordingWriter() {
  return {
    calls: [],
    text(...args) { this.calls.push(['text', ...args]); },
    toolStart(...args) { this.calls.push(['start', ...args]); },
    toolArgs(...args) { this.calls.push(['args', ...args]); },
  };
}

// A non-streaming consumer of the backend's writer interface, like server.js's
// Collector. It deliberately has no start/send/finish methods.
class NonStreamingWriter {
  constructor() {
    this.blocks = [];
    this.tools = new Map();
    this.currentText = null;
    this.sawToolCall = false;
    this.usage = { input_tokens: 0, output_tokens: 0 };
  }
  text(delta) {
    if (!delta) return;
    if (!this.currentText) {
      this.currentText = { type: 'text', text: '' };
      this.blocks.push(this.currentText);
    }
    this.currentText.text += delta;
  }
  toolStart(key, id, name) {
    if (this.tools.has(key)) return;
    this.currentText = null;
    this.sawToolCall = true;
    const block = { type: 'tool_use', id, name, arguments: '' };
    this.tools.set(key, block);
    this.blocks.push(block);
  }
  toolArgs(key, delta) {
    const block = this.tools.get(key);
    if (block && delta) block.arguments += delta;
  }
  content() {
    return this.blocks.map(block => block.type === 'tool_use'
      ? { type: 'tool_use', id: block.id, name: block.name, input: JSON.parse(block.arguments || '{}') }
      : block);
  }
}

async function run(events, request = body, writer = recordingWriter()) {
  nextEvents = events;
  const stop = await backend.run(request, request.model, 'medium', writer, () => {});
  return { stop, writer, payload: requests.at(-1) };
}

async function stream(events) {
  // An in-memory HTTP response sink, exercising the real SSE encoder/writer.
  const response = {
    chunks: [], ended: false,
    writeHead(status, headers) { this.status = status; this.headers = headers; },
    write(chunk) { this.chunks.push(chunk); },
    end() { this.ended = true; },
  };
  const writer = new AnthropicStreamWriter(response, body.model);
  writer.start();
  const { stop } = await run(events, { ...body, stream: true }, writer);
  writer.finish(stop);
  assert.equal(response.status, 200);
  assert.equal(response.headers['content-type'], 'text/event-stream');
  assert.equal(response.ended, true);
  const output = response.chunks.join('').trim().split('\n\n').map(frame => {
    const [event, data] = frame.split('\n');
    const value = JSON.parse(data.slice(6));
    assert.equal(event, `event: ${value.type}`);
    return value;
  });
  return output;
}

async function testExistingBehavior() {
  const { writer, payload, stop } = await run([
    toolStart, toolArgs('{"file_path":"test.txt"}'), completed,
  ]);
  assert.equal(stop, null);
  assert.deepEqual(writer.calls, [
    ['start', 'fc_1', 'call_1', 'Read'], ['args', 'fc_1', '{"file_path":"test.txt"}'],
  ]);
  assert.deepEqual(writer.usage, usage);
  assert.equal(payload.tools[0].strict, false);
  assert.equal(payload.max_output_tokens, 2048);
  assert.equal(payload.instructions, 'coding assistant');
  assert.deepEqual(payload.tool_choice, { type: 'function', name: 'Read' });
  assert.equal((await run([completed], { ...body, max_tokens: 1 })).payload.max_output_tokens, 16);

  const history = {
    ...body,
    messages: [...body.messages,
      { role: 'assistant', content: [{ type: 'tool_use', id: 'call_1', name: 'Read', input: { file_path: 'test.txt' } }] },
      { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'call_1', content: 'hello' }] },
    ],
  };
  const result = await run([textDelta('OK'), completed], history);
  assert.equal(result.payload.input[1].type, 'function_call');
  assert.deepEqual(result.payload.input[2], { type: 'function_call_output', call_id: 'call_1', output: 'hello' });
  assert.deepEqual(result.writer.calls, [['text', 'OK']]);
  await assert.rejects(run([textDelta('partial')]), /before completion/);
}

async function testSchemaContexts() {
  const literal = { cache_control: { type: 'ephemeral' }, $schema: 'literal', properties: { cache_control: 42 } };
  const leaf = { type: 'string', cache_control: { type: 'ephemeral' }, $schema: 'remove-me' };
  const schema = {
    type: 'object', $schema: 'https://json-schema.org/draft/2020-12/schema', cache_control: { type: 'ephemeral' },
    properties: {
      cache_control: { ...leaf, default: literal },
      $schema: { ...leaf, enum: [literal, '$schema', 'cache_control'] },
      nested: { type: 'object', properties: { cache_control: leaf, $schema: leaf } },
    },
    $defs: { cache_control: leaf, $schema: leaf },
    definitions: { cache_control: leaf, $schema: leaf },
    patternProperties: { cache_control: leaf, $schema: leaf },
    dependentSchemas: { cache_control: { properties: { $schema: leaf }, cache_control: 'remove' } },
    dependencies: { $schema: ['$schema', 'cache_control'], cache_control: { properties: { cache_control: leaf } } },
    required: ['cache_control', '$schema'],
    default: literal, const: literal, enum: [literal], examples: [literal],
    'x-custom': literal,
    allOf: [leaf, true], anyOf: [leaf, false], oneOf: [leaf], prefixItems: [leaf],
    items: { properties: { $schema: leaf }, cache_control: 'remove' },
    additionalProperties: leaf, unevaluatedProperties: leaf, propertyNames: leaf,
    additionalItems: leaf, unevaluatedItems: leaf, contains: leaf,
    not: leaf, if: leaf, then: leaf, else: leaf, contentSchema: leaf,
  };
  const snapshot = JSON.stringify(schema);
  const { payload } = await run([completed], { ...body, tools: [{ name: 'Schema', input_schema: schema }] });
  const cleaned = payload.tools[0].parameters;
  assert.equal(JSON.stringify(schema), snapshot, 'request schema must not be mutated');
  assert.equal(Object.hasOwn(cleaned, '$schema'), false);
  assert.equal(Object.hasOwn(cleaned, 'cache_control'), false);
  assert.deepEqual(cleaned.properties, {
    cache_control: { type: 'string', default: literal },
    $schema: { type: 'string', enum: [literal, '$schema', 'cache_control'] },
    nested: { type: 'object', properties: { cache_control: { type: 'string' }, $schema: { type: 'string' } } },
  });
  for (const keyword of ['$defs', 'definitions', 'patternProperties']) {
    assert.deepEqual(cleaned[keyword], { cache_control: { type: 'string' }, $schema: { type: 'string' } });
  }
  assert.deepEqual(cleaned.dependentSchemas, { cache_control: { properties: { $schema: { type: 'string' } } } });
  assert.deepEqual(cleaned.dependencies, {
    $schema: ['$schema', 'cache_control'], cache_control: { properties: { cache_control: { type: 'string' } } },
  });
  for (const keyword of ['default', 'const', 'enum', 'examples', 'required', 'x-custom']) {
    assert.deepEqual(cleaned[keyword], schema[keyword], `${keyword} must remain literal data`);
  }
  for (const keyword of ['allOf', 'anyOf', 'oneOf', 'prefixItems']) {
    assert.deepEqual(cleaned[keyword], schema[keyword].map(value => typeof value === 'boolean' ? value : { type: 'string' }));
  }
  assert.deepEqual(cleaned.items, { properties: { $schema: { type: 'string' } } });
  for (const keyword of ['additionalProperties', 'unevaluatedProperties', 'propertyNames', 'additionalItems',
    'unevaluatedItems', 'contains', 'not', 'if', 'then', 'else', 'contentSchema']) {
    assert.deepEqual(cleaned[keyword], { type: 'string' }, keyword);
  }
  const tuple = await run([completed], { ...body, tools: [{ name: 'Tuple', input_schema: { items: [leaf, false] } }] });
  assert.deepEqual(tuple.payload.tools[0].parameters.items, [{ type: 'string' }, false]);
}

async function testAssistantOrder() {
  const text = text => ({ type: 'text', text });
  const tool = (id, input) => ({ type: 'tool_use', id, name: 'Read', input });
  const request = { ...body, messages: [{ role: 'assistant', content: [
    text('before '), text('call'), tool('call_a', { file_path: 'a' }),
    text('between '), text('calls'), tool('call_b', { file_path: 'b' }),
    text('after '), text('calls'), { type: 'thinking', thinking: 'not visible' }, text('separate'),
  ] }] };
  const message = text => ({ type: 'message', role: 'assistant', content: [{ type: 'output_text', text }] });
  const call = (id, path) => ({ type: 'function_call', call_id: id, name: 'Read', arguments: JSON.stringify({ file_path: path }) });
  assert.deepEqual((await run([completed], request)).payload.input, [
    message('before call'), call('call_a', 'a'), message('between calls'), call('call_b', 'b'),
    message('after calls'), message('separate'),
  ]);
  const edge = { ...body, messages: [
    { role: 'assistant', content: [tool('first', {}), text(''), tool('second', {}), text('tail')] },
    { role: 'assistant', content: 'plain string' },
  ] };
  assert.deepEqual((await run([completed], edge)).payload.input, [
    { type: 'function_call', call_id: 'first', name: 'Read', arguments: '{}' },
    { type: 'function_call', call_id: 'second', name: 'Read', arguments: '{}' },
    message('tail'), message('plain string'),
  ]);
}

const refusalEvents = [
  refusalDelta('I cannot '), refusalDelta('help with that.'),
  { type: 'response.refusal.done', refusal: 'I cannot help with that.' }, completed,
];
const mixedEvents = [
  textDelta('Checking '), textDelta('now.'), toolStart,
  toolArgs('{"file_path":'), toolArgs('"test.txt"}'),
  textDelta('Result: '), refusalDelta('cannot comply.'), completed,
];

async function testRealStreamingWriter() {
  const refusal = await stream(refusalEvents);
  assert.deepEqual(refusal.map(e => e.type), [
    'message_start', 'content_block_start', 'content_block_delta', 'content_block_delta',
    'content_block_stop', 'message_delta', 'message_stop',
  ]);
  assert.deepEqual(refusal[1].content_block, { type: 'text', text: '' });
  assert.equal(refusal.filter(e => e.delta?.type === 'text_delta').map(e => e.delta.text).join(''), 'I cannot help with that.');
  assert.equal(refusal.at(-2).delta.stop_reason, 'end_turn');
  assert.deepEqual(refusal.at(-2).usage, usage);

  const mixed = await stream(mixedEvents);
  assert.deepEqual(mixed.filter(e => e.type.startsWith('content_block')).map(e => [
    e.type, e.index, e.content_block || e.delta || null,
  ]), [
    ['content_block_start', 0, { type: 'text', text: '' }],
    ['content_block_delta', 0, { type: 'text_delta', text: 'Checking ' }],
    ['content_block_delta', 0, { type: 'text_delta', text: 'now.' }],
    ['content_block_stop', 0, null],
    ['content_block_start', 1, { type: 'tool_use', id: 'call_1', name: 'Read', input: {} }],
    ['content_block_delta', 1, { type: 'input_json_delta', partial_json: '{"file_path":' }],
    ['content_block_delta', 1, { type: 'input_json_delta', partial_json: '"test.txt"}' }],
    ['content_block_stop', 1, null],
    ['content_block_start', 2, { type: 'text', text: '' }],
    ['content_block_delta', 2, { type: 'text_delta', text: 'Result: ' }],
    ['content_block_delta', 2, { type: 'text_delta', text: 'cannot comply.' }],
    ['content_block_stop', 2, null],
  ]);
  assert.equal(mixed.at(-2).delta.stop_reason, 'tool_use');
  assert.deepEqual(mixed.at(-2).usage, usage);
  assert.equal(mixed.at(-1).type, 'message_stop');
}

async function testNonStreamingWriter() {
  const refusal = await run(refusalEvents, { ...body, stream: false }, new NonStreamingWriter());
  assert.equal(refusal.stop, null);
  assert.deepEqual(refusal.writer.content(), [{ type: 'text', text: 'I cannot help with that.' }]);
  assert.deepEqual(refusal.writer.usage, usage);
  assert.equal(refusal.writer.sawToolCall, false);
  const mixed = await run(mixedEvents, { ...body, stream: false }, new NonStreamingWriter());
  assert.deepEqual(mixed.writer.content(), [
    { type: 'text', text: 'Checking now.' },
    { type: 'tool_use', id: 'call_1', name: 'Read', input: { file_path: 'test.txt' } },
    { type: 'text', text: 'Result: cannot comply.' },
  ]);
  assert.equal(mixed.writer.sawToolCall, true);
  assert.deepEqual(mixed.writer.usage, usage);
}

async function testIncompleteReasons() {
  for (const type of ['response.incomplete', 'response.completed']) {
    const terminal = reason => ({
      type, response: { status: 'incomplete', incomplete_details: { reason }, usage },
    });
    const limited = await run([textDelta('partial'), terminal('max_output_tokens')]);
    assert.equal(limited.stop, 'max_tokens');
    assert.deepEqual(limited.writer.usage, usage);
    for (const reason of ['content_filter', 'other_reason', null, undefined]) {
      await assert.rejects(run([refusalDelta('partial refusal'), terminal(reason)]), error => {
        assert.match(error.message, /OpenAI response incomplete/);
        assert(error.message.includes(reason || 'unknown'));
        assert.doesNotMatch(error.message, /max_tokens|max_output_tokens/);
        return true;
      });
    }
    await assert.rejects(run([{ type, response: { status: 'incomplete' } }]), /reason: unknown/);
  }
  // The event itself is sufficient even if status is omitted upstream.
  assert.equal((await run([{
    type: 'response.incomplete', response: { incomplete_details: { reason: 'max_output_tokens' } },
  }])).stop, 'max_tokens');
  await assert.rejects(run([{ type: 'response.incomplete' }]), /reason: unknown/);
  const output = await stream([{
    type: 'response.incomplete', response: { status: 'incomplete', incomplete_details: { reason: 'max_output_tokens' }, usage },
  }]);
  assert.equal(output.at(-2).delta.stop_reason, 'max_tokens');
  const nonStreaming = await run([{
    type: 'response.incomplete', response: { status: 'incomplete', incomplete_details: { reason: 'max_output_tokens' }, usage },
  }], { ...body, stream: false }, new NonStreamingWriter());
  assert.equal(nonStreaming.stop, 'max_tokens');
}

(async () => {
  for (const test of [testExistingBehavior, testSchemaContexts, testAssistantOrder,
    testRealStreamingWriter, testNonStreamingWriter, testIncompleteReasons]) {
    await test();
    console.log(`PASS: ${test.name}`);
  }
  console.log('PASS: all backend regressions (mock fetch only; no real API calls)');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
}).finally(() => {
  global.fetch = originalFetch;
  if (originalKey === undefined) delete process.env.OPENAI_API_KEY;
  else process.env.OPENAI_API_KEY = originalKey;
});
