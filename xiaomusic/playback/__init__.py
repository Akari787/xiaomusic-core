"""Shared playback strategy utilities."""

from xiaomusic.playback.command_arbiter import (
    ArbiterClosedError,
    DeviceCommandArbiter,
    IntentKind,
    IntentReceipt,
    PlaybackIntent,
)
from xiaomusic.playback.link_strategy import NormalizedLink
from xiaomusic.playback.runtime_state import (
    DeviceObservation,
    FailureState,
    LifecycleToken,
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
    TransitionError,
)
from xiaomusic.playback.task_registry import (
    ATTEMPT_SCOPED_KINDS,
    SESSION_SCOPED_KINDS,
    PlaybackTaskRegistry,
    TaskGeneration,
    TaskKind,
    TaskSnapshot,
)

__all__ = [
    "ArbiterClosedError",
    "DeviceCommandArbiter",
    "IntentKind",
    "IntentReceipt",
    "NormalizedLink",
    "PlaybackIntent",
    "DeviceObservation",
    "FailureState",
    "LifecycleToken",
    "PlaybackPhase",
    "PlaybackRuntimeState",
    "TrackReference",
    "TransitionError",
    "ATTEMPT_SCOPED_KINDS",
    "PlaybackTaskRegistry",
    "SESSION_SCOPED_KINDS",
    "TaskGeneration",
    "TaskKind",
    "TaskSnapshot",
]
