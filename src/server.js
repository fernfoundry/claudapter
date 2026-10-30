// Adapted from hanjun-lin/claude-gpt (MIT), official OpenAI API only.
const http = require('http');
const { AnthropicStreamWriter } = require('./anthropic-stream');

const PORT = Number(process.env.GPT_PROXY_PORT || 8787);
const BACKEND_NAME = 'openai-responses';
const BIG = process.env.GPT_PROXY_MODEL || 'gpt-6.1-sol';
const SMALL = process.env.GPT_PROXY_SMALL_MODEL || BIG;
const EFFORT = process.env.GPT_PROXY_REASONING || 'medium';
const DEBUG = process.env.GPT_PROXY_DEBUG === '1';

const backend = require('./responses-backend');
const log = (...a) => DEBUG && console.error('[proxy]', ...a);

// Routing is by requested model name, so `/model gpt-5.5` switches live:
// GPT model IDs select the requested OpenAI model. Other IDs are local
// compatibility aliases for BIG; this gateway never forwards to Anthropic.
const isGptModel = (m) => /^(gpt|o\d|codex)/i.test(String(m || '').trim());

// Aliases so `/model gpt` and `/model gpt-mini` work as shorthand.
const ALIASES = { gpt: () => BIG, 'gpt-mini': () => SMALL, 'gpt-small': () => SMALL };

// Only called for GPT-routed requests; Claude names never reach here.
// Point ANTHROPIC_SMALL_FAST_MODEL at a GPT slug to move the background
// model slot onto GPT too.
function resolveModel(requested) {
  const m = String(requested || '').trim();
  const alias = ALIASES[m.toLowerCase()];
  return alias ? alias() : (m || BIG);
}

// Mimics AnthropicStreamWriter so a backend can serve a non-streaming request
// without knowing the difference.
class Collector {
  constructor() {
    this.blocks = [];
    this.usage = { input_tokens: 0, output_tokens: 0 };
    this.sawToolCall = false;
    this._tools = new Map();
    this._text = null;
  }
  text(delta) {
    if (!delta) return;
    if (!this._text) { this._text = { type: 'text', text: '' }; this.blocks.push(this._text); }
    this._text.text += delta;
  }
  toolStart(key, id, name) {
    if (this._tools.has(key)) return;
    this._text = null;
    this.sawToolCall = true;
    const block = { type: 'tool_use', id, name, input: {}, _args: '' };
    this._tools.set(key, block);
    this.blocks.push(block);
  }
  toolArgs(key, partial) {
    const b = this._tools.get(key);
    if (b && partial) b._args += partial;
  }
  content() {
    return this.blocks.map((b) => {
      if (b.type !== 'tool_use') return b;
      let input = {};
      try { input = JSON.parse(b._args || '{}'); } catch { input = {}; }
      return { type: 'tool_use', id: b.id, name: b.name, input };
    });
  }
}

function anthropicError(res, status, message) {
  const payload = JSON.stringify({ type: 'error', error: { type: 'api_error', message } });
  if (!res.headersSent) res.writeHead(status, { 'content-type': 'application/json' });
  res.end(payload);
}

async function handleMessages(body, res) {
  const model = resolveModel(body.model);
  log(body.model, '->', model, `${(body.messages || []).length} msgs`, `stream=${!!body.stream}`);

  if (body.stream) {
    const writer = new AnthropicStreamWriter(res, body.model);
    let started = false;
    try {
      writer.start();
      started = true;
      const stop = await backend.run(body, model, EFFORT, writer, log);
      writer.finish(stop);
    } catch (e) {
      log('stream failed:', e.message);
      if (!started) anthropicError(res, e.status || 500, e.message);
      else {
        // Surface the failure in-band; the stream headers are already sent.
        writer.send('error', {type:'error',error:{type:'api_error',message:e.message}});
        res.end();
      }
    }
    return;
  }

  const collector = new Collector();
  const stop = await backend.run(body, model, EFFORT, collector, log);
  res.writeHead(200, { 'content-type': 'application/json' });
  res.end(JSON.stringify({
    id: 'msg_proxy',
    type: 'message',
    role: 'assistant',
    model: body.model,
    content: collector.content(),
    stop_reason: stop || (collector.sawToolCall ? 'tool_use' : 'end_turn'),
    stop_sequence: null,
    usage: collector.usage,
  }));
}

// Rough estimate; Claude Code uses this only for context accounting.
function handleCountTokens(body, res) {
  const raw = JSON.stringify(body.messages || [])
    + JSON.stringify(body.system || '')
    + JSON.stringify(body.tools || []);
  res.writeHead(200, { 'content-type': 'application/json' });
  res.end(JSON.stringify({ input_tokens: Math.ceil(raw.length / 4) }));
}

const server = http.createServer((req, res) => {
  const chunks = [];
  let size = 0;
  req.on('data', (c) => { size += c.length; if (size > 32*1024*1024) { res.writeHead(413); res.end(); req.destroy(); } else chunks.push(c); });
  req.on('end', async () => {
    const path = req.url.split('?')[0];
    try {
      if (req.method === 'GET' && path === '/health') {
        res.writeHead(200, { 'content-type': 'application/json' });
        return res.end(JSON.stringify({ ok: true, backend: BACKEND_NAME, big: BIG, small: SMALL }));
      }
      if (req.method !== 'POST') return anthropicError(res, 404, `no route ${path}`);

      const raw = Buffer.concat(chunks).toString('utf8') || '{}';
      const body = JSON.parse(raw);

      const token = process.env.GPT_PROXY_TOKEN;
      if (!token || (req.headers.authorization !== `Bearer ${token}` && req.headers['x-api-key'] !== token)) {
        return anthropicError(res, 401, 'Invalid local gateway token');
      }
      if (!isGptModel(body.model)) body.model = BIG;
      if (path.endsWith('/count_tokens')) return handleCountTokens(body, res);
      if (!path.endsWith('/messages')) return anthropicError(res, 404, `no route ${path}`);
      await handleMessages(body, res);
    } catch (e) {
      log('error', e.stack, e.cause ? `\n  cause: ${e.cause.stack || e.cause}` : '');
      anthropicError(res, e.status || 500, e.message);
    }
  });
});

server.listen(PORT, '127.0.0.1', () => {
  console.error(`gpt-proxy on http://127.0.0.1:${PORT}  backend=${BACKEND_NAME}  main=${BIG}  small=${SMALL}  effort=${EFFORT}`);
});
