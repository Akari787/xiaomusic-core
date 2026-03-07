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
