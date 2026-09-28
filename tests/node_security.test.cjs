const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const test = require('node:test');

const axios = require('axios');
const FormData = require('form-data');
const follow = require('follow-redirects');
const qs = require('qs');
const undici = require('undici');

const searchFixture = `
module.exports = {
  async search(query, page, type) {
    return {
      isEnd: true,
      data: [{
        id: query + '-' + page + '-' + type,
        title: 'fixture result',
        url: 'https://media.example.test/song?token=fixture-secret',
      }],
    };
  },
};
`;

function requestRunner(child, message) {
  return new Promise((resolve, reject) => {
    const id = `test-${Date.now()}-${Math.random()}`;
    let buffer = '';
    const onData = data => {
      buffer += data.toString();
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        const response = JSON.parse(line);
        if (response.id !== id) continue;
        child.stdout.off('data', onData);
        response.success ? resolve(response.result) : reject(new Error(response.error));
      }
    };
    child.stdout.on('data', onData);
    child.stdin.write(`${JSON.stringify({ ...message, id })}\n`);
  });
}

function versionAtLeast(version, major, minor, patch) {
  const actual = version.split('.').map(Number);
  const required = [major, minor, patch];
  for (let index = 0; index < required.length; index += 1) {
    const current = actual[index] ?? 0;
    if (current !== required[index]) return current > required[index];
  }
  return true;
}

test('versionAtLeast compares numeric version components at boundaries', () => {
  assert.equal(versionAtLeast('7.9.0', 7, 29, 0), false);
  assert.equal(versionAtLeast('7.28.9', 7, 29, 0), false);
  assert.equal(versionAtLeast('7.29.0', 7, 29, 0), true);
  assert.equal(versionAtLeast('7.29.1', 7, 29, 0), true);
  assert.equal(versionAtLeast('8.0.0', 7, 29, 0), true);
});

test('JS plugin search works from a read-only-style cwd without raw-result debug logging', async t => {
  const runnerPath = path.join(__dirname, '..', 'xiaomusic', 'js_plugin_runner.js');
  const source = fs.readFileSync(runnerPath, 'utf8');
  assert.doesNotMatch(source, /00-plugin_debug\.log/);
  assert.doesNotMatch(source, /appendFileSync|writeFileSync|createWriteStream/);
  assert.doesNotMatch(source, /require\(['"](?:node:)?fs['"]\)/);

  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'xiaomusic-js-runner-'));
  const child = spawn(process.execPath, [runnerPath], { cwd, stdio: ['pipe', 'pipe', 'pipe'] });
  t.after(async () => {
    if (child.exitCode === null) {
      await new Promise(resolve => {
        child.once('exit', resolve);
        child.kill();
      });
    }
    fs.rmSync(cwd, { recursive: true, force: true });
  });

  const originalMode = process.platform === 'win32' ? null : fs.statSync(cwd).mode;
  if (originalMode !== null) fs.chmodSync(cwd, 0o555);
  try {
    assert.equal(await requestRunner(child, { action: 'load', name: 'fixture', code: searchFixture }), true);
    const result = await requestRunner(child, {
      action: 'search',
      pluginName: 'fixture',
      params: { keywords: 'hello', page: 2, type: 'music' },
    });
    assert.deepEqual(result.data, [{
      id: 'hello-2-music',
      title: 'fixture result',
      url: 'https://media.example.test/song?token=fixture-secret',
      platform: 'fixture',
    }]);
    assert.equal(result.isEnd, true);
    assert.equal(fs.existsSync(path.join(cwd, '00-plugin_debug.log')), false);
  } finally {
    if (originalMode !== null) fs.chmodSync(cwd, originalMode & 0o777);
  }
});

test('runtime security packages resolve and can be required', () => {
  for (const value of [axios, FormData, follow, qs, undici]) assert.ok(value);
  assert.ok(versionAtLeast(require('undici/package.json').version, 7, 29, 0));
  assert.ok(process.versions.node.split('.')[0] >= 20);
});

async function requestAcrossRedirect(options, client = 'follow') {
  const seen = {};
  const target = http.createServer((req, res) => {
    seen.apiKey = req.headers['x-api-key'];
    seen.authorization = req.headers.authorization;
    res.end('ok');
  });
  const redirect = http.createServer((req, res) => {
    res.writeHead(302, { location: `http://127.0.0.1:${target.address().port}/target` });
    res.end();
  });
  await new Promise(resolve => target.listen(0, '127.0.0.1', resolve));
  await new Promise(resolve => redirect.listen(0, '127.0.0.1', resolve));
  try {
    const url = `http://127.0.0.1:${redirect.address().port}/redirect`;
    if (client === 'axios') {
      await axios.get(url, options);
    } else {
      await new Promise((resolve, reject) => {
        follow.http.get(
          url,
          options,
          response => { response.resume(); response.on('end', resolve); },
        ).on('error', reject);
      });
    }
    return seen;
  } finally {
    await new Promise(resolve => redirect.close(resolve));
    await new Promise(resolve => target.close(resolve));
  }
}

test('follow-redirects default policy is precise for cross-origin redirects', async () => {
  const seen = await requestAcrossRedirect({
    headers: {
      authorization: 'Basic built-in-sensitive',
      'x-api-key': 'caller-header',
    },
  });
  // 1.16.0 keeps its built-in sensitive-header policy, but does not guess
  // that arbitrary caller headers such as X-API-Key are sensitive.
  assert.equal(seen.authorization, undefined);
  assert.equal(seen.apiKey, 'caller-header');
});

test('follow-redirects honors explicit sensitiveHeaders across cross-origin redirects', async () => {
  const seen = await requestAcrossRedirect({
    headers: { 'x-api-key': 'caller-header' },
    sensitiveHeaders: ['X-API-Key'],
  });
  assert.equal(seen.apiKey, undefined);
});

test('axios http adapter applies redirect header policy by default', async () => {
  const seen = await requestAcrossRedirect({
    headers: {
      authorization: 'Basic built-in-sensitive',
      'x-api-key': 'caller-header',
    },
  }, 'axios');
  assert.equal(seen.authorization, undefined);
  assert.equal(seen.apiKey, 'caller-header');
});

test('axios http adapter honors explicit sensitiveHeaders', async () => {
  const seen = await requestAcrossRedirect({
    headers: { 'x-api-key': 'caller-header' },
    sensitiveHeaders: ['X-API-Key'],
  }, 'axios');
  assert.equal(seen.apiKey, undefined);
});

test('form-data percent-encodes CRLF in field names and filenames', () => {
  const field = new FormData();
  field.append('field\r\nInjected: yes', 'value');
  const fieldBody = field.getBuffer().toString();
  assert.match(fieldBody, /name=\"field%0D%0AInjected: yes\"/);
  assert.doesNotMatch(fieldBody, /\r\nInjected:/);

  const file = new FormData();
  file.append('field', Buffer.from('x'), { filename: 'x\r\nInjected: yes' });
  const fileBody = file.getBuffer().toString();
  assert.match(fileBody, /filename=\"x%0D%0AInjected: yes\"/);
  assert.doesNotMatch(fileBody, /\r\nInjected:/);
});

test('qs handles the official GHSA-4mjr constructor/isBuffer PoC', () => {
  const parsed = qs.parse('x%5Bconstructor%5D%5BisBuffer%5D=y', { plainObjects: true });
  assert.doesNotThrow(() => qs.stringify(parsed));
});

test('qs parse/stringify handles the additional advisory PoC shape', () => {
  const parsed = qs.parse('a[0]=x&a[1]=y&comma=1,2', { comma: true });
  assert.doesNotThrow(() => qs.stringify(parsed, { comma: true, encodeValuesOnly: true }));
});

test('axios prototype gadget input cannot hijack a local request', async () => {
  let hits = 0;
  const server = http.createServer((req, res) => { hits += 1; res.end('ok'); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const url = `http://127.0.0.1:${server.address().port}/safe`;
    const response = await axios.get(url, { __proto__: { proxy: { host: '127.0.0.2' } } });
    assert.equal(response.data, 'ok');
    assert.equal(hits, 1);
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});
