from xiaomusic.core.settings import get_analytics_settings, get_auth_settings


def test_auth_settings_allow_missing_hash_when_basic_auth_is_opt_in(monkeypatch):
    monkeypatch.delenv("API_SECRET", raising=False)
    monkeypatch.delenv("HTTP_AUTH_HASH", raising=False)
    get_auth_settings.cache_clear()
    assert get_auth_settings().HTTP_AUTH_HASH == ""


def test_auth_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("HTTP_AUTH_HASH", "$2b$12$hashplaceholder")
    get_auth_settings.cache_clear()
    s = get_auth_settings()
    assert s.HTTP_AUTH_HASH == "$2b$12$hashplaceholder"


def test_analytics_settings_allow_missing_secret(monkeypatch):
    monkeypatch.delenv("API_SECRET", raising=False)
    get_analytics_settings.cache_clear()
    s = get_analytics_settings()
    assert s.API_SECRET is None


def test_analytics_settings_load_secret_from_env(monkeypatch):
    monkeypatch.setenv("API_SECRET", "abc-secret")
    get_analytics_settings.cache_clear()
    s = get_analytics_settings()
    assert s.API_SECRET == "abc-secret"
