import asyncio
import types
from urllib.parse import urlparse

import pytest

from xiaomusic.security.outbound import OutboundBlockedError, OutboundPolicy, fetch_text


class _FakeContent:
    def __init__(self, body: bytes):
        self._body = body

    async def iter_chunked(self, size: int):
        # Single chunk is enough for tests.
        yield self._body


class _FakeResponse:
    def __init__(self, status: int, body: bytes = b"ok", headers: dict | None = None):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeContent(body)
        self.charset = "utf-8"


class _ReqCtx:
    def __init__(self, resp: _FakeResponse):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, responses_by_url: dict[str, _FakeResponse]):
        self._responses_by_url = responses_by_url
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str, *args, **kwargs):
        self.calls.append(url)
        resp = self._responses_by_url[url]
        return _ReqCtx(resp)


@pytest.mark.asyncio
async def test_http_get_allowlist_and_block_ip_literal(monkeypatch):
    policy = OutboundPolicy(("example.com",))

    with pytest.raises(OutboundBlockedError):
        policy.validate_url("http://127.0.0.1")

    with pytest.raises(OutboundBlockedError):
        policy.validate_url("http://[::1]")

    # allowlisted hostname is ok (actual connect mocked)
    policy.validate_url("https://example.com/")


@pytest.mark.asyncio
async def test_http_get_blocks_private_resolution(monkeypatch):
    policy = OutboundPolicy(("example.com",))
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(host, port, *args, **kwargs):
        # Resolve to private IP -> must be blocked.
        return [(2, 1, 6, "", ("192.168.7.10", port))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)

    # Patch aiohttp to avoid real network.
    import xiaomusic.security.outbound as outbound

    monkeypatch.setattr(outbound.aiohttp, "TCPConnector", lambda **kw: object())
    monkeypatch.setattr(outbound.aiohttp, "ClientSession", lambda **kw: _FakeSession({}))

    with pytest.raises(OutboundBlockedError):
        await fetch_text("http://example.com/", policy=policy)


@pytest.mark.asyncio
async def test_dns_rebinding_is_pinned(monkeypatch):
    policy = OutboundPolicy(("example.com",))
    loop = asyncio.get_running_loop()

    calls = {"n": 0}

    async def fake_getaddrinfo(host, port, *args, **kwargs):
        calls["n"] += 1
        # First call: public IP; any later call simulates rebinding to private.
        if calls["n"] == 1:
            return [(2, 1, 6, "", ("93.184.216.34", port))]
        return [(2, 1, 6, "", ("192.168.7.10", port))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)

    import xiaomusic.security.outbound as outbound

    monkeypatch.setattr(outbound.aiohttp, "TCPConnector", lambda **kw: object())

    sess = _FakeSession({"http://example.com/": _FakeResponse(200, b"ok")})
    monkeypatch.setattr(outbound.aiohttp, "ClientSession", lambda **kw: sess)

    out = await fetch_text("http://example.com/", policy=policy)
    assert out == "ok"
    # Only resolved once (validation); connect is pinned via fixed resolver.
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_redirect_revalidated_domain(monkeypatch):
    policy = OutboundPolicy(("example.com",))
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)

    import xiaomusic.security.outbound as outbound

    monkeypatch.setattr(outbound.aiohttp, "TCPConnector", lambda **kw: object())

    sess = _FakeSession(
        {
            "http://example.com/a": _FakeResponse(
                302, b"", headers={"Location": "http://evil.com/b"}
            ),
        }
    )
    monkeypatch.setattr(outbound.aiohttp, "ClientSession", lambda **kw: sess)

    with pytest.raises(OutboundBlockedError):
        await fetch_text("http://example.com/a", policy=policy)

    # Only first request attempted; redirect target blocked before request.
    assert sess.calls == ["http://example.com/a"]


@pytest.mark.asyncio
async def test_redirect_revalidated_private_resolution(monkeypatch):
    policy = OutboundPolicy(("example.com",))
    loop = asyncio.get_running_loop()

    calls = {"n": 0}

    async def fake_getaddrinfo(host, port, *args, **kwargs):
        calls["n"] += 1
        # First hop resolves public, second hop resolves private.
        if calls["n"] == 1:
            return [(2, 1, 6, "", ("93.184.216.34", port))]
        return [(2, 1, 6, "", ("10.0.0.1", port))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)

    import xiaomusic.security.outbound as outbound

    monkeypatch.setattr(outbound.aiohttp, "TCPConnector", lambda **kw: object())

    sess = _FakeSession(
        {
            "http://example.com/a": _FakeResponse(
                302, b"", headers={"Location": "http://example.com/b"}
            ),
            "http://example.com/b": _FakeResponse(200, b"ok"),
        }
    )
    monkeypatch.setattr(outbound.aiohttp, "ClientSession", lambda **kw: sess)

    with pytest.raises(OutboundBlockedError):
        await fetch_text("http://example.com/a", policy=policy)
