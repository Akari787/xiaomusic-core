const assert = require('node:assert/strict');
const http = require('node:http');
const test = require('node:test');

const axios = require('axios');
const FormData = require('form-data');
const follow = require('follow-redirects');
const qs = require('qs');
const undici = require('undici');

function versionAtLeast(version, major, minor, patch) {
  const [a, b, c] = version.split('.').map(Number);
  return [a, b, c].join('.') >= [major, minor, patch].join('.') ||
    (a > major || (a === major && (b > minor || (b === minor && c >= patch))));
}

test('runtime security packages resolve and can be required', () => {
  for (const value of [axios, FormData, follow, qs, undici]) assert.ok(value);
  assert.ok(versionAtLeast(require('undici/package.json').version, 7, 29, 0));
  assert.ok(process.versions.node.split('.')[0] >= 20);
});

test('follow-redirects drops custom auth across a cross-origin redirect', async () => {
  let seen;
  const target = http.createServer((req, res) => {
    seen = req.headers.authorization;
    res.end('ok');
  });
  const redirect = http.createServer((req, res) => {
    res.writeHead(302, { location: `http://127.0.0.1:${target.address().port}/target` });
    res.end();
  });
  await new Promise(resolve => target.listen(0, '127.0.0.1', resolve));
  await new Promise(resolve => redirect.listen(0, '127.0.0.1', resolve));
  try {
    await new Promise((resolve, reject) => {
      follow.http.get(
        `http://127.0.0.1:${redirect.address().port}/redirect`,
        { headers: { authorization: 'Basic should-not-leak' } },
        response => { response.resume(); response.on('end', resolve); },
      ).on('error', reject);
    });
    assert.equal(seen, undefined);
  } finally {
    await new Promise(resolve => redirect.close(resolve));
    await new Promise(resolve => target.close(resolve));
  }
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

test('qs parse/stringify handles the advisory PoC shape', () => {
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
