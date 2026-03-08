from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MediaRequest:
    request_id: str
    query: str
    source_hint: str | None = None
    device_id: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


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
