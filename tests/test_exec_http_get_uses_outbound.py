import pytest


@pytest.mark.asyncio
async def test_exec_http_get_uses_outbound(monkeypatch):
    import requests

    from xiaomusic.security.exec_plugin import ExecPluginEngine
    from xiaomusic.security.errors import ExecNotAllowedError

    class DummyConfig:
        enable_exec_plugin = True
        allowed_exec_commands = ["http_get"]
        outbound_allowlist_domains = ["example.com"]
        allowlist_domains = []

    class DummyPM:
        def get_func(self, name):
            return None

    called = {"n": 0, "url": None}

    async def fake_fetch_text(url, *, policy, timeout_s=0, max_bytes=0, max_redirects=0, user_agent=None, rebind_check=True):
        called["n"] += 1
        called["url"] = url
        return "ok"

    # If the old requests path is ever used, fail the test.
    def boom(*a, **k):
        raise AssertionError("requests.get should not be called")

    monkeypatch.setattr(requests, "get", boom)

    import xiaomusic.security.exec_plugin as exec_plugin

    monkeypatch.setattr(exec_plugin, "fetch_text", fake_fetch_text)

    engine = ExecPluginEngine(DummyConfig(), log=None, plugin_manager=DummyPM())
    out = await engine.execute('http_get("https://example.com/")')
    assert out == "ok"
    assert called["n"] == 1
    assert called["url"].startswith("https://example.com")


@pytest.mark.asyncio
async def test_exec_http_get_blocked_when_allowlist_empty():
    from xiaomusic.security.exec_plugin import ExecPluginEngine
    from xiaomusic.security.errors import ExecNotAllowedError

    class DummyConfig:
        enable_exec_plugin = True
        allowed_exec_commands = ["http_get"]
        outbound_allowlist_domains = []
        allowlist_domains = []

    engine = ExecPluginEngine(DummyConfig(), log=None, plugin_manager=None)
    with pytest.raises(ExecNotAllowedError):
        await engine.execute('http_get("https://example.com/")')
