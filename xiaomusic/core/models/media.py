from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from xiaomusic.core.models.payload_keys import (
    OPT_CONFIRM_START,
    OPT_CONFIRM_START_DELAY_MS,
    OPT_CONFIRM_START_INTERVAL_MS,
    OPT_CONFIRM_START_RETRIES,
    OPT_LOOP,
    OPT_ID,
    OPT_MEDIA_ID,
    OPT_NO_CACHE,
    OPT_PREFER_PROXY,
    OPT_RESOLVE_TIMEOUT_SECONDS,
    OPT_SHUFFLE,
    OPT_SOURCE_PAYLOAD,
    OPT_START_POSITION,
    OPT_TIMEOUT,
    OPT_TITLE,
    OPT_VOLUME,
    PAYLOAD_ID,
    PAYLOAD_SOURCE,
    PAYLOAD_TITLE,
    PAYLOAD_URL,
)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _as_int(value: Any, default: int, *, min_value: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    if min_value is not None and parsed < min_value:
        return int(min_value)
    return parsed


@dataclass(slots=True)
class PlayOptions:
    start_position: int = 0
    shuffle: bool = False
    loop: bool = False
    volume: int | None = None
    timeout: float | None = None
    resolve_timeout_seconds: float | None = None
    no_cache: bool = False
    prefer_proxy: bool = False
    confirm_start: bool = True
    confirm_start_delay_ms: int = 1200
    confirm_start_retries: int = 2
    confirm_start_interval_ms: int = 600
    source_payload: dict[str, Any] | None = None
    media_id: str = ""
    title: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "PlayOptions":
        if not isinstance(payload, Mapping):
            return cls()

        source_payload = payload.get(OPT_SOURCE_PAYLOAD)
        normalized_payload = source_payload if isinstance(source_payload, dict) else None
        media_id = str(payload.get(OPT_MEDIA_ID) or payload.get(OPT_ID) or "").strip()
        title = str(payload.get(OPT_TITLE) or "").strip()
        volume: int | None = None
        if OPT_VOLUME in payload and payload.get(OPT_VOLUME) is not None:
            volume_raw = _as_int(payload.get(OPT_VOLUME), 0, min_value=0)
            volume = volume_raw if volume_raw <= 100 else 100
        return cls(
            start_position=_as_int(payload.get(OPT_START_POSITION), 0, min_value=0),
            shuffle=_as_bool(payload.get(OPT_SHUFFLE), False),
            loop=_as_bool(payload.get(OPT_LOOP), False),
            volume=volume,
            timeout=_as_float(payload.get(OPT_TIMEOUT), None),
            resolve_timeout_seconds=_as_float(payload.get(OPT_RESOLVE_TIMEOUT_SECONDS), None),
            no_cache=_as_bool(payload.get(OPT_NO_CACHE), False),
            prefer_proxy=_as_bool(payload.get(OPT_PREFER_PROXY), False),
            confirm_start=_as_bool(payload.get(OPT_CONFIRM_START), True),
            confirm_start_delay_ms=_as_int(payload.get(OPT_CONFIRM_START_DELAY_MS), 1200, min_value=0),
            confirm_start_retries=_as_int(payload.get(OPT_CONFIRM_START_RETRIES), 2, min_value=0),
            confirm_start_interval_ms=_as_int(payload.get(OPT_CONFIRM_START_INTERVAL_MS), 600, min_value=100),
            source_payload=normalized_payload,
            media_id=media_id,
            title=title,
        )

    def to_context(
        self,
        *,
        query: str,
        source_hint: str | None,
        include_prefer_proxy: bool,
    ) -> dict[str, Any]:
        default_resolve_timeout = 15.0 if source_hint == "site_media" else 8.0
        context: dict[str, Any] = {
            OPT_RESOLVE_TIMEOUT_SECONDS: float(self.resolve_timeout_seconds or default_resolve_timeout),
            OPT_NO_CACHE: bool(self.no_cache),
            OPT_START_POSITION: int(self.start_position),
            OPT_SHUFFLE: bool(self.shuffle),
            OPT_LOOP: bool(self.loop),
        }
        if self.volume is not None:
            context[OPT_VOLUME] = int(self.volume)
        if self.timeout is not None:
            context[OPT_TIMEOUT] = float(self.timeout)
        if include_prefer_proxy:
            context[OPT_PREFER_PROXY] = bool(self.prefer_proxy)
            context[OPT_CONFIRM_START] = bool(self.confirm_start)
            context[OPT_CONFIRM_START_DELAY_MS] = int(self.confirm_start_delay_ms)
            context[OPT_CONFIRM_START_RETRIES] = int(self.confirm_start_retries)
            context[OPT_CONFIRM_START_INTERVAL_MS] = int(self.confirm_start_interval_ms)

        if source_hint == "jellyfin":
            payload = self.source_payload
            if not isinstance(payload, dict):
                payload = {
                    PAYLOAD_SOURCE: "jellyfin",
                    PAYLOAD_URL: query,
                    PAYLOAD_ID: self.media_id,
                    PAYLOAD_TITLE: self.title,
                }
            context[OPT_SOURCE_PAYLOAD] = payload
            payload_title = str(payload.get(PAYLOAD_TITLE) or "").strip()
            if payload_title:
                context[OPT_TITLE] = payload_title

        if source_hint == "local_library" and self.title:
            context[OPT_TITLE] = self.title

        return context


@dataclass(slots=True)
class MediaRequest:
    request_id: str
    query: str
    source_hint: str | None = None
    device_id: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(
        cls,
        *,
        request_id: str,
        query: str,
        source_hint: str | None,
        device_id: str | None,
        options: PlayOptions,
        include_prefer_proxy: bool,
    ) -> "MediaRequest":
        return cls(
            request_id=str(request_id),
            source_hint=source_hint,
            query=str(query),
            device_id=device_id,
            context=options.to_context(
                query=query,
                source_hint=source_hint,
                include_prefer_proxy=include_prefer_proxy,
            ),
        )


@dataclass(slots=True)
class ResolvedMedia:
    media_id: str
    source: str
    title: str
    stream_url: str
    headers: dict[str, str] = field(default_factory=dict)
    expires_at: int | None = None
    is_live: bool = False


@dataclass(slots=True)
class PreparedStream:
    final_url: str
    headers: dict[str, str] = field(default_factory=dict)
    expires_at: int | None = None
    is_proxy: bool = False
    source: str = ""


@dataclass(slots=True)
class DeliveryPlan:
    primary: PreparedStream
    fallback: PreparedStream | None = None
    strategy: str = "direct_only"
    decision_reason: str = ""


@dataclass(slots=True)
class PlaybackAttempt:
    path: str
    transport: str
    url: str
    accepted: bool
    started: bool | None = None


@dataclass(slots=True)
class PlaybackOutcome:
    accepted: bool
    started: bool | None
    final_path: str
    fallback_triggered: bool
    attempts: list[PlaybackAttempt] = field(default_factory=list)
