from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from xiaomusic.constants.api_fields import (
    DEVICE_ID,
    OPTIONS,
    QUERY,
    REQUEST_ID,
    SOURCE_HINT,
)

SourceHint = Literal["auto", "direct_url", "site_media", "jellyfin", "local_library"]


class PlayOptionsModel(BaseModel):
    """Structured options payload for play/resolve requests."""

    model_config = ConfigDict(extra="forbid")

    shuffle: bool = False
    loop: bool = False
    volume: int | None = Field(default=None, ge=0, le=100)
    timeout: float | None = None
    resolve_timeout_seconds: float | None = None
    no_cache: bool = False
    prefer_proxy: bool = False
    confirm_start: bool = True
    confirm_start_delay_ms: int = Field(default=1200, ge=0)
    confirm_start_retries: int = Field(default=2, ge=0)
    confirm_start_interval_ms: int = Field(default=600, ge=100)
    source_payload: dict[str, Any] | None = None
    context_hint: dict[str, Any] | None = None
    media_id: str = ""
    id: str = ""  # alias for media_id
    title: str = ""
    start_position: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _require_strict_bool(cls, data: Any) -> Any:
        """Reject non-boolean values for boolean fields."""
        if not isinstance(data, dict):
            return data
        for field_name in ("shuffle", "loop", "no_cache", "prefer_proxy", "confirm_start"):
            if field_name in data and not isinstance(data[field_name], bool):
                raise ValueError(
                    f"{field_name} must be a boolean, got {type(data[field_name]).__name__}"
                )
        return data


class PlayRequest(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    device_id: str = Field(alias=DEVICE_ID, min_length=1)
    query: str = Field(alias=QUERY, min_length=1)
    source_hint: SourceHint = Field(default="auto", alias=SOURCE_HINT)
    options: PlayOptionsModel = Field(default_factory=PlayOptionsModel, alias=OPTIONS)
    request_id: str | None = Field(default=None, alias=REQUEST_ID)

    @model_validator(mode="after")
    def _check_query_not_blank(self) -> PlayRequest:
        if not self.query.strip():
            raise ValueError("query must not be blank")
        return self


class ResolveRequest(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    query: str = Field(alias=QUERY, min_length=1)
    source_hint: SourceHint = Field(default="auto", alias=SOURCE_HINT)
    options: PlayOptionsModel = Field(default_factory=PlayOptionsModel, alias=OPTIONS)
    request_id: str | None = Field(default=None, alias=REQUEST_ID)

    @model_validator(mode="after")
    def _check_query_not_blank(self) -> ResolveRequest:
        if not self.query.strip():
            raise ValueError("query must not be blank")
        return self


class ControlRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(alias=DEVICE_ID, min_length=1)
    request_id: str | None = Field(default=None, alias=REQUEST_ID)


class PlayModeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(alias=DEVICE_ID, min_length=1)
    play_mode: str
    request_id: str | None = Field(default=None, alias=REQUEST_ID)


class ShutdownTimerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(alias=DEVICE_ID, min_length=1)
    minutes: int = Field(ge=0)
    request_id: str | None = Field(default=None, alias=REQUEST_ID)


class FavoritesRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(alias=DEVICE_ID, min_length=1)
    track_name: str = ""
    entity_id: str = ""
    request_id: str | None = Field(default=None, alias=REQUEST_ID)


class LibraryRefreshRequest(BaseModel):
    request_id: str | None = Field(default=None, alias=REQUEST_ID)


class SystemSettingsSaveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    settings: dict[str, Any] = Field(default_factory=dict)
    device_ids: list[str] = Field(default_factory=list)
    request_id: str | None = Field(default=None, alias=REQUEST_ID)


class SystemSettingItemUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str = ""
    value: Any
    request_id: str | None = Field(default=None, alias=REQUEST_ID)


class TtsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(alias=DEVICE_ID, min_length=1)
    text: str = Field(min_length=1)
    request_id: str | None = Field(default=None, alias=REQUEST_ID)


class VolumeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(alias=DEVICE_ID, min_length=1)
    volume: int = Field(ge=0, le=100)
    request_id: str | None = Field(default=None, alias=REQUEST_ID)
