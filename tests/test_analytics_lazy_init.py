from types import SimpleNamespace

import pytest

from xiaomusic import analytics as analytics_mod


@pytest.mark.asyncio
async def test_analytics_disabled_does_not_load_settings(monkeypatch):
    cfg = SimpleNamespace(enable_analytics=False, hostname="http://localhost")
    log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)

    def _boom():
        raise AssertionError("settings should not be loaded when analytics disabled")

    monkeypatch.setattr(analytics_mod, "get_settings", _boom)

    a = analytics_mod.Analytics(log, cfg)
    a.init()
    await a.send_startup_event()
    await a.send_daily_event()
    await a.send_play_event("demo", 1, "device")
    assert a.gtag is None


def test_analytics_enabled_initializes_lazily(monkeypatch):
    cfg = SimpleNamespace(enable_analytics=True, hostname="http://localhost")
    log = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)

    calls = {"settings": 0}

    class _FakeEvent:
        def set_event_param(self, **kwargs):
            return None

        def get_event_params(self):
            return {}

        def get_event_name(self):
            return "x"

    class _FakeStore:
        def set_user_property(self, **kwargs):
            return None

    class _FakeGtag:
        def __init__(self, **kwargs):  # noqa: ARG002
            self.store = _FakeStore()

        def random_client_id(self):
            return "cid"

        def create_new_event(self, **kwargs):  # noqa: ARG002
            return _FakeEvent()

        def send(self, events):  # noqa: ARG002
            return None

    def _settings():
        calls["settings"] += 1
        return SimpleNamespace(API_SECRET="secret")

    monkeypatch.setattr(analytics_mod, "get_settings", _settings)
    monkeypatch.setattr(analytics_mod, "GtagMP", _FakeGtag)

    a = analytics_mod.Analytics(log, cfg)
    assert calls["settings"] == 0
    a.init()
    assert calls["settings"] == 1
    assert a.gtag is not None
