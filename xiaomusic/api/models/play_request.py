from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from xiaomusic.constants.api_fields import DEVICE_ID, OPTIONS, QUERY, REQUEST_ID, SOURCE_HINT


class PlayRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(alias=DEVICE_ID)
    query: str = Field(alias=QUERY)
    source_hint: str = Field(default="auto", alias=SOURCE_HINT)
    options: dict[str, Any] = Field(default_factory=dict, alias=OPTIONS)
    request_id: str | None = Field(default=None, alias=REQUEST_ID)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(alias=QUERY)
    source_hint: str = Field(default="auto", alias=SOURCE_HINT)
    options: dict[str, Any] = Field(default_factory=dict, alias=OPTIONS)
    request_id: str | None = Field(default=None, alias=REQUEST_ID)


class ControlRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(alias=DEVICE_ID)
    request_id: str | None = Field(default=None, alias=REQUEST_ID)


class TtsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(alias=DEVICE_ID)
    text: str
    request_id: str | None = Field(default=None, alias=REQUEST_ID)


class VolumeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    device_id: str = Field(alias=DEVICE_ID)
    volume: int
    request_id: str | None = Field(default=None, alias=REQUEST_ID)
