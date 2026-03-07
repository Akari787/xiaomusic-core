from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TransportCapabilityMatrix:
    play: list[str] = field(default_factory=list)
    tts: list[str] = field(default_factory=list)
    volume: list[str] = field(default_factory=list)
    stop: list[str] = field(default_factory=list)
    pause: list[str] = field(default_factory=list)
    probe: list[str] = field(default_factory=list)

    def by_action(self, action: str) -> list[str]:
        return list(getattr(self, action, []))


@dataclass(slots=True)
class TransportDispatchResult:
    ok: bool
    action: str
    transport: str
    data: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
