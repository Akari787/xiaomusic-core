"""Pure completion policy module (T05-A).

ObservationKind: what the confirmer observed.
ConfirmationObservation: frozen observation data.
map_to_observation: pure function mapping raw bool|None|exception → observation.

No I/O. No tasks. No device_player references. No time (caller passes it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FailureAction(str, Enum):
    RETRY_SAME = "retry_same"
    RETRY_NEXT = "retry_next"
    STOP = "stop"
    DEGRADED = "degraded"
    NONE = "none"


@dataclass(frozen=True)
class FailureDecision:
    action: FailureAction
    delay: float = 0.0
    failure_count: int = 0
    reason: str = ""


class ObservationKind(str, Enum):
    STARTED = "started"
    NOT_STARTED = "not_started"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConfirmationObservation:
    """Frozen observation produced by the background confirmer.

    kind: what was observed about playback start.
    observed_at: monotonic timestamp provided by the caller.
    raw_value: the original bool|None|Exception value that produced this.
    source: label describing how the observation was formed
            (e.g. "first_probe", "grace_retry", "exception").
    """

    kind: ObservationKind
    observed_at: float
    raw_value: bool | None | Exception | None = field(
        default=None, compare=False
    )
    source: str = ""

    def __repr__(self) -> str:
        raw_repr: str
        if isinstance(self.raw_value, Exception):
            raw_repr = type(self.raw_value).__name__
        else:
            raw_repr = repr(self.raw_value)
        return (
            f"ConfirmationObservation("
            f"kind={self.kind.value}, "
            f"at={self.observed_at:.3f}, "
            f"raw={raw_repr}, "
            f"source={self.source!r})"
        )


def decide_failure_action(
    failure_count: int,
    total_elapsed: float,
    single_play: bool = False,
    *,
    reason: str = "",
) -> FailureDecision:
    """Pure playback-failure policy; callers own time, I/O, and tasks."""
    count = max(int(failure_count), 0)
    elapsed = max(float(total_elapsed), 0.0)
    if count >= 5 or elapsed >= 60:
        return FailureDecision(FailureAction.DEGRADED, 0.0, count, reason)
    if single_play:
        return FailureDecision(FailureAction.STOP, 0.0, count, reason)
    if count <= 2:
        action = FailureAction.RETRY_SAME
    elif count <= 4:
        action = FailureAction.RETRY_NEXT
    else:
        action = FailureAction.NONE
    delay = min(2 ** max(count - 1, 0), 8)
    return FailureDecision(action, float(delay), count, reason)


def map_to_observation(
    raw: bool | None | Exception,
    observed_at: float,
    source: str = "",
) -> ConfirmationObservation:
    """Pure mapping from confirmer result to observation.

    - True  → STARTED
    - False → NOT_STARTED
    - None  → UNKNOWN
    - Exception → UNKNOWN
    """
    if raw is True:
        kind = ObservationKind.STARTED
    elif raw is False:
        kind = ObservationKind.NOT_STARTED
    else:
        kind = ObservationKind.UNKNOWN

    return ConfirmationObservation(
        kind=kind,
        observed_at=observed_at,
        raw_value=None if isinstance(raw, Exception) else raw,
        source=source,
    )
