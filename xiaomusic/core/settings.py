from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    HTTP_AUTH_HASH: str = Field(default="", min_length=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class AnalyticsSettings(BaseSettings):
    API_SECRET: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_auth_settings() -> AuthSettings:
    return AuthSettings()


@lru_cache(maxsize=1)
def get_analytics_settings() -> AnalyticsSettings:
    return AnalyticsSettings()


def get_settings() -> AuthSettings:
    """Backward-compatible alias for auth-only settings."""
    return get_auth_settings()
