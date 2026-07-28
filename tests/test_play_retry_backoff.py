"""T05-A pure completion-policy boundary tests.

Owner lifecycle tests moved to tests/unit/test_t05b_failure_retry_owner.py.
"""

import pytest


@pytest.mark.parametrize(
    ("count", "action", "delay"),
    [
        (1, "retry_same", 1.0),
        (2, "retry_same", 2.0),
        (3, "retry_next", 4.0),
        (4, "retry_next", 8.0),
        (5, "degraded", 0.0),
    ],
)
def test_failure_policy_boundaries(count, action, delay):
    from xiaomusic.playback.completion_policy import decide_failure_action

    decision = decide_failure_action(count, 0)
    assert decision.action.value == action
    assert decision.delay == delay


def test_failure_policy_elapsed_and_single_play():
    from xiaomusic.playback.completion_policy import (
        FailureAction,
        decide_failure_action,
    )

    assert decide_failure_action(1, 60).action is FailureAction.DEGRADED
    assert decide_failure_action(1, 0, True).action is FailureAction.STOP


def test_failure_policy_delay_cap():
    from xiaomusic.playback.completion_policy import decide_failure_action

    assert decide_failure_action(4, 0).delay == 8.0
