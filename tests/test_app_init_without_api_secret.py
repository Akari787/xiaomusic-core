import importlib

from xiaomusic.core import settings as settings_mod


def test_app_import_without_api_secret_when_analytics_disabled(monkeypatch):
    monkeypatch.setenv("XIAOMUSIC_ENABLE_ANALYTICS", "false")
    monkeypatch.delenv("API_SECRET", raising=False)
    monkeypatch.setenv("HTTP_AUTH_HASH", "$2b$12$hashplaceholder")

    settings_mod.get_auth_settings.cache_clear()
    settings_mod.get_analytics_settings.cache_clear()

    app_mod = importlib.import_module("xiaomusic.api.app")
    app_mod = importlib.reload(app_mod)
    assert app_mod.app is not None
