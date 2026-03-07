from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PlayRequest(BaseModel):
    device_id: str
    query: str
    source_hint: str = "auto"
    options: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ResolveRequest(BaseModel):
    query: str
    source_hint: str = "auto"
    options: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ControlRequest(BaseModel):
    device_id: str
    request_id: str | None = None


class TtsRequest(BaseModel):
    device_id: str
    text: str
    request_id: str | None = None


class VolumeRequest(BaseModel):
    device_id: str
    volume: int
    request_id: str | None = None
