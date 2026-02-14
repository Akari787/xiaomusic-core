import logging
import socket

import pytest

from xiaomusic.security.exec_plugin import ExecPluginEngine
from xiaomusic.security.errors import ExecNotAllowedError


class DummyConfig:
    enable_exec_plugin = True
    allowed_exec_commands = ["http_get"]
    allowlist_domains = ["example.com"]


class DummyPluginManager:
    def get_func(self, name):
        return None


class DummyResponse:
    def __init__(self, status_code=200, body=b"ok"):
        self.status_code = status_code
        self.headers = {}
        self._body = body

    @property
    def is_redirect(self):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def iter_content(self, chunk_size=8192):
        yield self._body


def test_http_get_domain_allowlist_and_block_private(monkeypatch):
    cfg = DummyConfig()

    def fake_getaddrinfo(host, port, proto=0, *args, **kwargs):
        # Return a public IP for example.com
        if host == "example.com":
            return [(socket.AF_INET, None, None, None, ("93.184.216.34", port))]
        # localhost/private should not be queried in this test
        return [(socket.AF_INET, None, None, None, ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: DummyResponse())

    engine = ExecPluginEngine(cfg, logging.getLogger("t"), plugin_manager=DummyPluginManager())

    async def run():
        # allowed
        out = await engine.execute('http_get("https://example.com/a")')
        assert "ok" in out

        # blocked: ip literal
        with pytest.raises(ExecNotAllowedError):
            await engine.execute('http_get("http://127.0.0.1")')

        with pytest.raises(ExecNotAllowedError):
            await engine.execute('http_get("http://192.168.1.1")')

        with pytest.raises(ExecNotAllowedError):
            await engine.execute('http_get("http://10.0.0.1")')

        with pytest.raises(ExecNotAllowedError):
            await engine.execute('http_get("http://[::1]")')

    import asyncio

    asyncio.run(run())
