import pytest
from pydantic import ValidationError

from xiaomusic.core.settings import Settings, get_settings


def test_settings_require_secret_env(monkeypatch):
    monkeypatch.delenv("API_SECRET", raising=False)
    monkeypatch.delenv("HTTP_AUTH_HASH", raising=False)
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        Settings()


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("API_SECRET", "abc-secret")
    monkeypatch.setenv("HTTP_AUTH_HASH", "$2b$12$hashplaceholder")
    get_settings.cache_clear()
    s = get_settings()
    assert s.API_SECRET == "abc-secret"
    assert s.HTTP_AUTH_HASH == "$2b$12$hashplaceholder"
