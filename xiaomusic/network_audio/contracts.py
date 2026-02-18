"""Unified contracts for network audio streaming pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ERROR_CODES: dict[str, str] = {
    "E_URL_UNSUPPORTED": "Unsupported URL site or format",
    "E_RESOLVE_TIMEOUT": "Resolver timed out",
    "E_RESOLVE_NONZERO_EXIT": "Resolver process exited with non-zero status",
    "E_STREAM_START_FAILED": "Failed to start stream pipeline",
    "E_STREAM_NOT_FOUND": "Stream session not found",
    "E_STREAM_SINGLE_CLIENT_ONLY": "Only one client is allowed per session stream",
    "E_XIAOMI_PLAY_FAILED": "Xiaomi playback adapter failed to accept stream",
    "E_INTERNAL": "Unexpected internal error",
}


@dataclass(slots=True)
class UrlInfo:
    site: str
    kind_hint: str
    normalized_url: str
    original_url: str

    @staticmethod
    def sample() -> "UrlInfo":
        return UrlInfo(
            site="youtube",
            kind_hint="vod",
            normalized_url="https://www.youtube.com/watch?v=abc123",
            original_url="https://youtu.be/abc123?t=9",
        )


@dataclass(slots=True)
class ResolveResult:
    ok: bool
    source_url: str
    title: str
    is_live: bool
    container_hint: str
    error_code: str | None = None
    error_message: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def sample() -> "ResolveResult":
        return ResolveResult(
            ok=True,
            source_url="https://media.example.local/audio.m4a",
            title="sample title",
            is_live=False,
            container_hint="m4a",
            error_code=None,
            error_message=None,
            meta={"site": "youtube"},
        )


@dataclass(slots=True)
class Session:
    sid: str
    state: str
    input_url: str
    stream_url: str
    source_url: str
    reconnect_count: int
    created_at: str
    updated_at: str
    meta: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def sample() -> "Session":
        return Session(
            sid="s_0001",
            state="running",
            input_url="https://www.youtube.com/watch?v=abc123",
            stream_url="http://127.0.0.1:18090/stream/s_0001",
            source_url="https://media.example.local/audio.m4a",
            reconnect_count=0,
            created_at="2026-02-16T12:00:00Z",
            updated_at="2026-02-16T12:00:01Z",
            meta={"speaker_id": "981257654"},
        )


@dataclass(slots=True)
class Event:
    sid: str
    ts: str
    type: str
    level: str
    detail: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def sample() -> "Event":
        return Event(
            sid="s_0001",
            ts="2026-02-16T12:00:02Z",
            type="ClientConnected",
            level="info",
            detail={"remote_addr": "192.168.7.50"},
        )
