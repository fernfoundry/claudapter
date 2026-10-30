// Adapted from hanjun-lin/claude-gpt (MIT), official OpenAI API only.
const { sseLines } = require('./anthropic-stream');

const URL_ = 'https://api.openai.com/v1';

function textOf(content) {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content.filter((b) => b && b.type === 'text').map((b) => b.text).join('\n');
}

function cleanSchema(node) {
  if (Array.isArray(node)) return node.map(cleanSchema);
  if (!node || typeof node !== 'object') return node;
  const out = {};
  for (const [k, v] of Object.entries(node)) {
    if (k === '$schema' || k === 'cache_control') continue;
    out[k] = cleanSchema(v);
  }
  return out;
}

function toolResultText(block) {
  let out;
  if (typeof block.content === 'string') out = block.content;
  else if (Array.isArray(block.content)) {
    out = block.content
      .map((b) => (b.type === 'text' ? b.text : b.type === 'image' ? '[image omitted]' : ''))
      .filter(Boolean)
      .join('\n');
  } else out = '';
  return block.is_error ? `Error: ${out}` : out || '(no output)';
}

// Anthropic messages -> Responses API `input` items.
// The top-level system prompt maps to instructions. Conversation messages
// preserve assistant/user roles; tool results become function_call_output.
function toInput(messages) {
  const input = [];
  for (const m of messages || []) {
    const role = m.role === 'assistant' ? 'assistant' : 'user';

    if (typeof m.content === 'string') {
      input.push({
        type: 'message',
        role,
        content: [{ type: role === 'assistant' ? 'output_text' : 'input_text', text: m.content }],
      });
      continue;
    }
    const blocks = Array.isArray(m.content) ? m.content : [];

    if (role === 'user') {
      for (const b of blocks) {
        if (b.type !== 'tool_result') continue;
        input.push({ type: 'function_call_output', call_id: b.tool_use_id, output: toolResultText(b) });
      }
      const parts = [];
      for (const b of blocks) {
        if (b.type === 'text' && b.text) parts.push({ type: 'input_text', text: b.text });
        else if (b.type === 'image' && b.source) {
          const url = b.source.type === 'base64'
            ? `data:${b.source.media_type};base64,${b.source.data}`
            : b.source.url;
          if (url) parts.push({ type: 'input_image', image_url: url });
        }
      }
      if (parts.length) input.push({ type: 'message', role: 'user', content: parts });
      continue;
    }

    const text = blocks.filter((b) => b.type === 'text').map((b) => b.text).join('');
    if (text) {
      input.push({ type: 'message', role: 'assistant', content: [{ type: 'output_text', text }] });
    }
    for (const b of blocks) {
      if (b.type !== 'tool_use') continue;
      input.push({
        type: 'function_call',
        call_id: b.id,
        name: b.name,
        arguments: JSON.stringify(b.input ?? {}),
      });
    }
  }
  return input;
}

function toTools(tools) {
  if (!Array.isArray(tools) || !tools.length) return undefined;
  return tools.filter((t) => t && t.name).map((t) => ({
    type: 'function',
    name: t.name,
    description: t.description || '',
    strict: false,
    parameters: cleanSchema(t.input_schema || { type: 'object', properties: {} }),
  }));
}

function toToolChoice(tc) {
  if (!tc) return undefined;
  if (tc.type === 'any') return 'required';
  if (tc.type === 'auto') return 'auto';
  if (tc.type === 'none') return 'none';
  if (tc.type === 'tool') return { type: 'function', name: tc.name };
  return undefined;
}

function buildPayload(body, model, effort) {
  const payload = {
    model,
    instructions: textOf(body.system) || 'You are a helpful coding assistant.',
    input: toInput(body.messages),
    // Stateless official Responses requests.
    stream: true,
    store: false,
    parallel_tool_calls: false,
    reasoning: { effort },
  };
  const tools = toTools(body.tools);
  if (tools) {
    payload.tools = tools;
    const choice = toToolChoice(body.tool_choice);
    if (choice) payload.tool_choice = choice;
  }
  if (body.max_tokens) payload.max_output_tokens = Math.max(16, body.max_tokens);
  return payload;
}

async function post(payload) {
  const key = process.env.OPENAI_API_KEY;
  if (!key) throw new Error('OPENAI_API_KEY is required');
  return fetch(`${URL_}/responses`, {
    method: 'POST',
    headers: {'content-type': 'application/json', authorization: `Bearer ${key}`},
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(600000),
  });
}

// Streams the OpenAI response into `writer`. Returns the Anthropic stop_reason.
async function run(body, model, effort, writer, log) {
  const payload = buildPayload(body, model, effort);


  const res = await post(payload);
  if (!res.ok) {
    const err = await res.text();
    throw Object.assign(new Error(err), { status: res.status });
  }

  let stopReason = null;
  let completed = false;
  let eventName = null;

  for await (const line of sseLines(res.body)) {
    if (line.startsWith('event:')) { eventName = line.slice(6).trim(); continue; }
    if (!line.startsWith('data:')) continue;
    const raw = line.slice(5).trim();
    if (!raw || raw === '[DONE]') continue;

    let ev;
    try { ev = JSON.parse(raw); } catch { continue; }
    const type = ev.type || eventName;

    switch (type) {
      case 'response.output_text.delta':
        writer.text(ev.delta);
        break;

      case 'response.output_item.added': {
        const item = ev.item || {};
        if (item.type === 'function_call') {
          writer.toolStart(item.id || ev.output_index, item.call_id || item.id, item.name);
          if (item.arguments) writer.toolArgs(item.id || ev.output_index, item.arguments);
        }
        break;
      }

      case 'response.function_call_arguments.delta':
        writer.toolArgs(ev.item_id ?? ev.output_index, ev.delta);
        break;

      case 'response.incomplete':
      case 'response.completed': {
        completed = true;
        const u = ev.response?.usage;
        if (u) {
          writer.usage = {
            input_tokens: u.input_tokens ?? 0,
            output_tokens: u.output_tokens ?? 0,
          };
        }
        if (ev.response?.status === 'incomplete') stopReason = 'max_tokens';
        break;
      }

      case 'response.failed':
      case 'error':
        throw new Error(ev.response?.error?.message || ev.error?.message || 'OpenAI API error');

      default:
        break; // reasoning summaries and lifecycle events are not surfaced
    }
  }
  if (!completed) throw new Error('OpenAI stream ended before completion');
  return stopReason;
}

module.exports = { run };
