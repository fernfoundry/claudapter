'use strict';
// Run only a temporary server copy with a fake backend. No API credentials/network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');

async function main() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gateway-server-test-'));
  let child;
  try {
    for (const file of ['server.js', 'anthropic-stream.js']) {
      fs.copyFileSync(path.join(__dirname, '../src', file), path.join(dir, file));
    }
    fs.writeFileSync(path.join(dir, 'responses-backend.js'), `
      exports.run = async (body, model, effort, writer) => {
        if (body.test_case === 'error') throw new Error('fake backend failure');
        writer.usage = {input_tokens: 12, output_tokens: 7};
        if (body.test_case === 'tool') {
          writer.text('Before tool.');
          writer.toolStart('key', 'call_fake', 'lookup');
          writer.toolArgs('key', '{"value":');
          writer.toolArgs('key', '"ok"}');
          return 'tool_use';
        }
        writer.text('I cannot ');
        writer.text('help with that request.'); // refusal deltas use the same writer.text API
        return body.test_case === 'max_tokens' ? 'max_tokens' : null;
      };
    `);
    // Port 0 asks the OS for a new temporary port; never touch configured gateway ports.
    child = spawn(process.execPath, [path.join(dir, 'server.js')], {
      cwd: dir,
      env: { PATH: process.env.PATH || '', GPT_PROXY_PORT: '0',
        GPT_PROXY_MODEL: 'gpt-test', GPT_PROXY_TOKEN: 'offline-test-token',
        GPT_PROXY_INSTANCE: 'offline-instance' },
      stdio: ['ignore', 'ignore', 'pipe'],
    });
    // Read the OS-assigned port from the server's ordinary startup line.
    const port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('temporary server startup timeout')), 5000);
      let log = '';
      child.stderr.on('data', chunk => {
        log += chunk;
        const match = log.match(/127\.0\.0\.1:(\d+)/);
        if (match) { clearTimeout(timer); resolve(Number(match[1])); }
      });
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('exit', code => { clearTimeout(timer); reject(new Error(`server exited ${code}: ${log}`)); });
    });
    const request = (method, route, headers = {}, body = '') => new Promise((resolve, reject) => {
      const req = http.request({ host: '127.0.0.1', port, path: route, method,
        headers, agent: false, timeout: 1500 }, res => {
        let text = '';
        res.setEncoding('utf8');
        res.on('data', chunk => { text += chunk; });
        res.on('end', () => resolve({status: res.statusCode, text, headers: res.headers}));
      });
      req.on('timeout', () => req.destroy(new Error('test request timeout')));
      req.on('error', reject);
      req.end(body);
    });
    const auth = {authorization: 'Bearer offline-test-token', 'content-type': 'application/json'};
    for (const headers of [{}, {authorization: 'Bearer wrong'}, {'x-api-key': 'wrong'}]) {
      const response = await request('GET', '/health', headers);
      assert.equal(response.status, 401);
      assert.ok(!response.text.includes('offline-instance'));
      assert.ok(!response.text.includes('gpt-test'));
    }
    for (const headers of [auth, {'x-api-key': 'offline-test-token'}]) {
      const response = await request('GET', '/health', headers);
      assert.equal(response.status, 200);
      const health = JSON.parse(response.text);
      assert.equal(health.ok, true);
      assert.equal(health.service, 'claude-openai-gateway');
      assert.equal(health.pid, child.pid);
      assert.equal(health.instance_id, 'offline-instance');
      assert.equal(health.backend, 'openai-responses');
    }
    for (const route of ['/v1/messages', '/v1/messages/count_tokens']) {
      assert.equal((await request('POST', route, {}, '{broken')).status, 401);
      assert.equal((await request('POST', route, {authorization: 'Bearer wrong'}, '{}')).status, 401);
    }
    assert.equal((await request('POST', '/v1/messages', auth, '{broken')).status, 500);
    assert.equal((await request('GET', '/unknown', auth)).status, 404);
    const post = async (test_case, stream = false) => request('POST', '/v1/messages', auth,
      JSON.stringify({model: 'gpt-test', test_case, stream, messages: []}));
    const refusal = await post('refusal');
    assert.equal(refusal.status, 200);
    const message = JSON.parse(refusal.text);
    assert.deepEqual(message.content, [{type: 'text', text: 'I cannot help with that request.'}]);
    assert.equal(message.stop_reason, 'end_turn');
    assert.deepEqual(message.usage, {input_tokens: 12, output_tokens: 7});
    const tool = JSON.parse((await post('tool')).text);
    assert.deepEqual(tool.content, [
      {type: 'text', text: 'Before tool.'},
      {type: 'tool_use', id: 'call_fake', name: 'lookup', input: {value: 'ok'}},
    ]);
    assert.equal(tool.stop_reason, 'tool_use');
    assert.equal(JSON.parse((await post('max_tokens')).text).stop_reason, 'max_tokens');
    const failure = await post('error');
    assert.equal(failure.status, 500);
    assert.equal(JSON.parse(failure.text).error.message, 'fake backend failure');
    const streamed = await post('refusal', true);
    assert.equal(streamed.status, 200);
    assert.ok(streamed.headers['content-type'].includes('text/event-stream'));
    assert.ok(streamed.text.includes('event: message_stop'));
    assert.ok(streamed.text.includes('help with that request.'));
    const streamFailure = await post('error', true);
    assert.ok(streamFailure.text.includes('event: error'));
    assert.ok(!streamFailure.text.includes('event: message_stop'));
    const count = await request('POST', '/v1/messages/count_tokens', auth,
      JSON.stringify({messages: [{role: 'user', content: 'offline'}]}));
    assert.equal(count.status, 200);
    assert.ok(JSON.parse(count.text).input_tokens > 0);
    console.log('PASS server: authenticated identity, POST auth, Collector refusal/tools/usage, SSE and errors (fake backend only)');
  } finally {
    if (child && child.exitCode === null && child.signalCode === null) {
      await new Promise(resolve => {
        const timer = setTimeout(() => child.kill('SIGKILL'), 2000);
        child.once('exit', () => {clearTimeout(timer); resolve();});
        child.kill('SIGTERM');
      });
    }
    fs.rmSync(dir, {recursive: true, force: true});
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
