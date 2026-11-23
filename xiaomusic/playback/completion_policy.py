"""Pure completion policy module (T05-A).

decide_failure_action: what to do after a failed play attempt.
(The start-confirmation observations were removed with status polling in
ADR-0007 step 4: a start is confirmed by the speaker's fetch.)

No I/O. No tasks. No device_player references. No time (caller passes it).
"""

from __future__ import annotations

from dataclasses import dataclass
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
