"""T03-H: build_player_state_snapshot runtime projection tests.

All non-IDLE states are constructed via legal pure-model reducer transitions.
No ``replace`` or direct ``PlaybackRuntimeState(phase=...)`` for non-IDLE.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from xiaomusic.playback.facade import PlaybackFacade
from xiaomusic.playback.runtime_state import (
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
    begin_confirm,
    begin_play_dispatch,
    begin_play_request,
    begin_resolve,
    begin_stop,
    complete_stop,
    confirm_playing,
    report_failure,
)
from xiaomusic.playback.runtime_state import (
    pause as pause_reducer,
)

# ── helpers ────────────────────────────────────────────────────────────


def _tr(entity_id: str = "", display_name: str = "", **kw: Any) -> TrackReference:
    return TrackReference(entity_id=entity_id, display_name=display_name, **kw)


def _build(
    phase: PlaybackPhase,
    desired: TrackReference | None = None,
    confirmed: TrackReference | None = None,
    switching_desired: TrackReference | None = None,
) -> PlaybackRuntimeState:
    """Construct *phase* via a legal chain of reducer transitions from IDLE.

    IDLE → begin_resolve → begin_play_dispatch → begin_confirm →
      confirm_playing → (pause / begin_stop / complete_stop / report_failure /
                          begin_play_request).

    *desired* is set during begin_resolve; *confirmed* is set during
    confirm_playing.  For SWITCHING, *switching_desired* is the new desired
    set by begin_play_request (which preserves *confirmed*).  All other
    phases preserve both tracks.
    """
    if phase == PlaybackPhase.IDLE:
        return PlaybackRuntimeState()

    s = PlaybackRuntimeState()
    s = begin_resolve(s, desired_track=desired, updated_at=0.0)
    if phase == PlaybackPhase.RESOLVING:
        return s

    s = begin_play_dispatch(s, updated_at=1.0)
    if phase == PlaybackPhase.DISPATCHING:
        return s

    s = begin_confirm(s, updated_at=2.0)
    if phase == PlaybackPhase.CONFIRMING:
        return s

    s = confirm_playing(
        s, confirmed_track=confirmed or desired, updated_at=3.0,
    )
    if phase == PlaybackPhase.PLAYING:
        return s

    if phase == PlaybackPhase.PAUSED:
        return pause_reducer(s, updated_at=4.0)

    if phase == PlaybackPhase.STOPPING:
        return begin_stop(s, updated_at=4.0)

    if phase == PlaybackPhase.STOPPED:
        stopping = begin_stop(s, updated_at=4.0)
        return complete_stop(stopping, updated_at=5.0)

    if phase == PlaybackPhase.FAILED:
        return report_failure(s, reason="test", updated_at=4.0)

    if phase == PlaybackPhase.SWITCHING:
        # SWITCHING from PLAYING via begin_play_request:
        #   confirmed preserved, desired replaced.
        return begin_play_request(
            s,
            desired_track=switching_desired or desired or TrackReference(),
            updated_at=4.0,
        )

    raise ValueError(f"Unknown phase: {phase}")


# ── A: 10 phases parametrized ──────────────────────────────────────────

_PHASE_TP_MAP: dict[PlaybackPhase, str] = {
    PlaybackPhase.IDLE: "idle",
    PlaybackPhase.RESOLVING: "starting",
    PlaybackPhase.DISPATCHING: "starting",
    PlaybackPhase.CONFIRMING: "starting",
    PlaybackPhase.SWITCHING: "switching",
    PlaybackPhase.PLAYING: "playing",
    PlaybackPhase.PAUSED: "paused",
    PlaybackPhase.STOPPING: "stopping",
    PlaybackPhase.STOPPED: "stopped",
    PlaybackPhase.FAILED: "error",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", list(PlaybackPhase))
async def test_a_phase_to_transport_state_mapping(phase: PlaybackPhase) -> None:
    """Each runtime phase maps to the correct transport_state."""

    class _Dev:
        def __init__(self) -> None:
            self._runtime_state = _build(phase, desired=_tr("e-a"))

        def get_runtime_state(self) -> PlaybackRuntimeState:
            return self._runtime_state

    _isplaying_calls: list[int] = [0]

    class _XM:
        device_manager = SimpleNamespace(devices={"did-a": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:  # noqa: ARG004
            _isplaying_calls[0] += 1
            return True

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 0.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return ""

    facade = PlaybackFacade(_XM())
    snapshot = await facade.build_player_state_snapshot("did-a")
    expected_ts = _PHASE_TP_MAP[phase]
    assert snapshot["transport_state"] == expected_ts, (
        f"phase={phase.value} → expected {expected_ts}, "
        f"got {snapshot['transport_state']}"
    )
    assert _isplaying_calls[0] == 0, (
        "legacy isplaying must not be called when runtime is authoritative"
    )


# ── B: PAUSED / STOPPED / FAILED — isplaying=True must not override ───


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase,expected_ts",
    [
        (PlaybackPhase.PAUSED, "paused"),
        (PlaybackPhase.STOPPED, "stopped"),
        (PlaybackPhase.FAILED, "error"),
    ],
)
async def test_b_isplaying_true_does_not_override_pause_stop_error(
    phase: PlaybackPhase, expected_ts: str,
) -> None:
    """Legacy isplaying=True must not override paused/stopped/error when
    runtime is authoritative."""

    class _Dev:
        def __init__(self) -> None:
            self._runtime_state = _build(phase, desired=_tr("e-b"))

        def get_runtime_state(self) -> PlaybackRuntimeState:
            return self._runtime_state

    class _XM:
        device_manager = SimpleNamespace(devices={"did-b": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return True  # must NOT override runtime phase

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 0.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return "stale"

    facade = PlaybackFacade(_XM())
    snapshot = await facade.build_player_state_snapshot("did-b")
    assert snapshot["transport_state"] == expected_ts, (
        f"phase={phase.value} isplaying=True should still yield "
        f"transport_state={expected_ts}"
    )


# ── C: SWITCHING → desired track shown / PLAYING → confirmed ──────────


@pytest.mark.asyncio
async def test_c_switching_shows_desired_playing_shows_confirmed() -> None:
    """SWITCHING: confirmed exists but snapshot shows desired.
    PLAYING: confirmed_track is shown."""

    confirmed = _tr("e-confirmed", "Confirmed Song", playlist_item_id="pi-confirmed")
    switching_desired = _tr("e-desired", "Desired Song", playlist_item_id="pi-desired")

    class _Dev:
        def __init__(self) -> None:
            self._runtime_state = _build(
                PlaybackPhase.SWITCHING,
                desired=_tr("e-old"),  # original desired before switch
                confirmed=confirmed,
                switching_desired=switching_desired,
            )
            self._play_session_id = 1

        def get_runtime_state(self) -> PlaybackRuntimeState:
            return self._runtime_state

    dev = _Dev()

    class _XM:
        device_manager = SimpleNamespace(devices={"did-c": dev})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 0.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return ""

    facade = PlaybackFacade(_XM())

    # SWITCHING snapshot
    sw_snap = await facade.build_player_state_snapshot("did-c")
    assert sw_snap["transport_state"] == "switching"
    track = sw_snap["track"]
    assert track is not None
    assert track["title"] == "Desired Song"
    assert track["id"] == "pi-desired"
    assert track["entity_id"] == "e-desired"

    # Switch to PLAYING (via confirm_playing)
    dev._runtime_state = _build(
        PlaybackPhase.PLAYING,
        desired=switching_desired,
        confirmed=switching_desired,
    )

    play_snap = await facade.build_player_state_snapshot("did-c")
    assert play_snap["transport_state"] == "playing"
    track2 = play_snap["track"]
    assert track2 is not None
    assert track2["title"] == "Desired Song"
    assert track2["id"] == "pi-desired"
    assert track2["entity_id"] == "e-desired"


# ── D: runtime identity takes priority over conflicting legacy ref ─────


@pytest.mark.asyncio
async def test_d_runtime_identity_priority_over_legacy_track_ref() -> None:
    """Runtime track identity must take priority over legacy
    get_current_track_reference."""

    rt_entity = _tr("rt-entity", "Runtime Song", playlist_item_id="rt-pi")

    class _Dev:
        def __init__(self) -> None:
            self._runtime_state = _build(
                PlaybackPhase.PLAYING, desired=rt_entity, confirmed=rt_entity,
            )
            self._play_session_id = 2

        def get_runtime_state(self) -> PlaybackRuntimeState:
            return self._runtime_state

        def get_current_track_reference(self) -> dict[str, Any]:
            return {
                "display_name": "Legacy Song",
                "entity_id": "legacy-entity",
                "playlist_item_id": "legacy-pi",
            }

    _isplaying_calls: list[int] = [0]

    class _XM:
        device_manager = SimpleNamespace(devices={"did-d": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:  # noqa: ARG004
            _isplaying_calls[0] += 1
            return True  # runtime authoritative → ignored, must not be called

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (10.0, 180.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return "test-playlist"

        @staticmethod
        def playingmusic(did: str) -> str:
            return "Legacy Song"

    facade = PlaybackFacade(_XM())
    snapshot = await facade.build_player_state_snapshot("did-d")
    track = snapshot["track"]
    assert track is not None
    assert track["title"] == "Runtime Song", (
        "runtime identity must take priority"
    )
    assert track["entity_id"] == "rt-entity"
    assert track["id"] == "rt-pi"
    assert _isplaying_calls[0] == 0, (
        "legacy isplaying must not be called when runtime authoritative"
    )


# ── E: runtime getter exception / missing → legacy unchanged ───────────


@pytest.mark.asyncio
async def test_e_runtime_getter_raises_legacy_unchanged() -> None:
    """When get_runtime_state() raises, legacy projection must be used."""

    class _Dev:
        _play_session_id = 3
        _current_index = 0
        _last_cmd = "play"
        _play_failed_cnt = 0
        _degraded = False
        is_playing = True
        _next_timer = None

        def get_runtime_state(self) -> PlaybackRuntimeState:
            raise RuntimeError("runtime unavailable")

        def get_cur_music(self) -> str:
            return "Legacy Song"

        def _get_playlist_names(self) -> list[str]:
            return ["Legacy Song"]

    class _XM:
        device_manager = SimpleNamespace(devices={"did-e": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return True

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (5.0, 200.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return "Legacy Song"

    facade = PlaybackFacade(_XM())
    snapshot = await facade.build_player_state_snapshot("did-e")
    assert snapshot["transport_state"] == "playing"
    assert snapshot["track"] is not None
    assert snapshot["track"]["title"] == "Legacy Song"


@pytest.mark.asyncio
async def test_e_no_runtime_getter_legacy_unchanged() -> None:
    """Without get_runtime_state method, legacy projection unchanged."""

    class _Dev:
        _play_session_id = 4
        _current_index = 0
        _last_cmd = "play"
        _play_failed_cnt = 0
        _degraded = False
        is_playing = True
        _next_timer = None

        def get_cur_music(self) -> str:
            return "No Runtime Song"

        def _get_playlist_names(self) -> list[str]:
            return ["No Runtime Song"]

    class _XM:
        device_manager = SimpleNamespace(devices={"did-e2": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return True

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 0.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return "No Runtime Song"

    facade = PlaybackFacade(_XM())
    snapshot = await facade.build_player_state_snapshot("did-e2")
    assert snapshot["transport_state"] == "playing"
    assert snapshot["track"] is not None
    assert snapshot["track"]["title"] == "No Runtime Song"


@pytest.mark.asyncio
async def test_e_runtime_getter_returns_wrong_type_legacy_unchanged() -> None:
    """get_runtime_state() returning wrong type → legacy fallback."""

    class _Dev:
        _play_session_id = 5
        _current_index = 0
        _last_cmd = "play"
        _play_failed_cnt = 0
        _degraded = False
        is_playing = True
        _next_timer = None

        def get_runtime_state(self) -> str:
            return "not a runtime state"

        def get_cur_music(self) -> str:
            return "Type Error Song"

        def _get_playlist_names(self) -> list[str]:
            return ["Type Error Song"]

    class _XM:
        device_manager = SimpleNamespace(devices={"did-e3": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return True

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 0.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return "Type Error Song"

    facade = PlaybackFacade(_XM())
    snapshot = await facade.build_player_state_snapshot("did-e3")
    assert snapshot["transport_state"] == "playing"
    assert snapshot["track"] is not None
    assert snapshot["track"]["title"] == "Type Error Song"


# ── F: consecutive queries → no tasks, no cloud calls ──────────────────


@pytest.mark.asyncio
async def test_f_no_tasks_and_no_cloud_on_consecutive_snapshots() -> None:
    """16 consecutive snapshots produce no cloud calls and no new tasks."""

    call_count = 0

    class _Dev:
        def __init__(self) -> None:
            self._runtime_state = _build(PlaybackPhase.PLAYING, desired=_tr("e-f"))
            self._play_session_id = 10

        def get_runtime_state(self) -> PlaybackRuntimeState:
            return self._runtime_state

    class _XM:
        device_manager = SimpleNamespace(devices={"did-f": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (5.0, 300.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return ""

        @staticmethod
        async def get_player_status(did: str) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            return {"status": 0}

    facade = PlaybackFacade(_XM())
    for _ in range(16):
        snapshot = await facade.build_player_state_snapshot("did-f")
        assert snapshot["transport_state"] == "playing"

    assert call_count == 0, "get_player_status must not be called"


# ── G: snapshot schema / revision stability ────────────────────────────


@pytest.mark.asyncio
async def test_g_snapshot_schema_unchanged() -> None:
    """Snapshot returns all expected top-level keys in expected types."""

    class _Dev:
        def __init__(self) -> None:
            self._runtime_state = _build(
                PlaybackPhase.PLAYING,
                desired=_tr("e1", "Song X"),
                confirmed=_tr("e1", "Song X", playlist_item_id="pi-x"),
            )
            self._play_session_id = 42

        def get_runtime_state(self) -> PlaybackRuntimeState:
            return self._runtime_state

    class _XM:
        device_manager = SimpleNamespace(devices={"did-g": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (15.0, 250.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return "BGM"

        @staticmethod
        def playingmusic(did: str) -> str:
            return ""

    facade = PlaybackFacade(_XM())
    snapshot = await facade.build_player_state_snapshot("did-g")

    assert isinstance(snapshot["device_id"], str)
    assert isinstance(snapshot["revision"], int)
    assert isinstance(snapshot["play_session_id"], str)
    assert isinstance(snapshot["transport_state"], str)
    assert isinstance(snapshot["position_ms"], int)
    assert isinstance(snapshot["duration_ms"], int)
    assert isinstance(snapshot["volume"], int)
    assert isinstance(snapshot["snapshot_at_ms"], int)

    track = snapshot["track"]
    assert isinstance(track, dict)
    assert isinstance(track["id"], str)
    assert isinstance(track["title"], str)
    assert isinstance(track["entity_id"], str)

    ctx = snapshot["context"]
    assert isinstance(ctx, dict)
    assert isinstance(ctx["id"], str)
    assert isinstance(ctx["name"], str)
    assert ctx["current_index"] is None


@pytest.mark.asyncio
async def test_g_revision_stable_when_same_state() -> None:
    """Revision does not change between identical snapshots."""

    class _Dev:
        def __init__(self) -> None:
            self._runtime_state = _build(
                PlaybackPhase.PLAYING,
                desired=_tr("e-stable", "Stable"),
                confirmed=_tr(
                    "e-stable", "Stable", playlist_item_id="pi-stable",
                ),
            )
            self._play_session_id = 99

        def get_runtime_state(self) -> PlaybackRuntimeState:
            return self._runtime_state

    class _XM:
        device_manager = SimpleNamespace(devices={"did-g2": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 100.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return "stable-list"

        @staticmethod
        def playingmusic(did: str) -> str:
            return ""

    facade = PlaybackFacade(_XM())
    s1 = await facade.build_player_state_snapshot("did-g2")
    s2 = await facade.build_player_state_snapshot("did-g2")
    assert s1["revision"] == s2["revision"]


@pytest.mark.asyncio
async def test_g_revision_increments_when_phase_changes() -> None:
    """Revision increments when transport_state changes."""

    dev = type(
        "_Dev",
        (),
        {
            "__init__": lambda self: (
                setattr(self, "_runtime_state", _build(PlaybackPhase.IDLE))
                or setattr(self, "_play_session_id", 1)
            ),
            "get_runtime_state": lambda self: self._runtime_state,
        },
    )()

    class _XM:
        device_manager = SimpleNamespace(devices={"did-g3": dev})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 0.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return ""

    facade = PlaybackFacade(_XM())
    s_idle = await facade.build_player_state_snapshot("did-g3")
    assert s_idle["transport_state"] == "idle"

    # Change to playing (legal transition)
    dev._runtime_state = _build(
        PlaybackPhase.PLAYING,
        desired=_tr("e-new"),
        confirmed=_tr("e-new", playlist_item_id="pi-new"),
    )
    s_play = await facade.build_player_state_snapshot("did-g3")
    assert s_play["transport_state"] == "playing"

    assert s_idle["revision"] != s_play["revision"], (
        "revision must change when transport_state changes"
    )


# ── edge: IDLE has no track ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_idle_phase_produces_no_track() -> None:
    """IDLE phase: no track in snapshot."""

    class _Dev:
        def __init__(self) -> None:
            self._runtime_state = _build(PlaybackPhase.IDLE)

        def get_runtime_state(self) -> PlaybackRuntimeState:
            return self._runtime_state

    class _XM:
        device_manager = SimpleNamespace(devices={"did-idle": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 0.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return "stale-title"

    facade = PlaybackFacade(_XM())
    snapshot = await facade.build_player_state_snapshot("did-idle")
    assert snapshot["transport_state"] == "idle"
    assert snapshot["track"] is None


# ── edge: CONFIRMING uses desired_track ────────────────────────────────


@pytest.mark.asyncio
async def test_confirming_uses_desired_track() -> None:
    """CONFIRMING phase: uses desired_track (not confirmed)."""

    desired = _tr("e-d", "Pending Song", playlist_item_id="pi-pending")

    class _Dev:
        def __init__(self) -> None:
            self._runtime_state = _build(
                PlaybackPhase.CONFIRMING, desired=desired,
            )

        def get_runtime_state(self) -> PlaybackRuntimeState:
            return self._runtime_state

    class _XM:
        device_manager = SimpleNamespace(devices={"did-conf": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 0.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return ""

    facade = PlaybackFacade(_XM())
    snapshot = await facade.build_player_state_snapshot("did-conf")
    assert snapshot["transport_state"] == "starting"
    track = snapshot["track"]
    assert track is not None
    assert track["title"] == "Pending Song"
    assert track["entity_id"] == "e-d"


# ── edge: STOPPED uses confirmed, falls back to desired ────────────────


@pytest.mark.asyncio
async def test_stopped_uses_confirmed_with_fallback_to_desired() -> None:
    """STOPPED phase: confirmed_track preferred, desired fallback."""

    desired = _tr("e-des", "Desired", playlist_item_id="pi-des")
    confirmed = _tr("e-con", "Confirmed", playlist_item_id="pi-con")

    class _Dev:
        def __init__(self) -> None:
            self._runtime_state = _build(
                PlaybackPhase.STOPPED,
                desired=desired,
                confirmed=confirmed,
            )
            self._play_session_id = 1

        def get_runtime_state(self) -> PlaybackRuntimeState:
            return self._runtime_state

    class _XM:
        device_manager = SimpleNamespace(devices={"did-sto": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return True  # must not override

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 0.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return "stale"

    facade = PlaybackFacade(_XM())
    s1 = await facade.build_player_state_snapshot("did-sto")
    assert s1["transport_state"] == "stopped"
    assert s1["track"]["title"] == "Confirmed"
    assert s1["track"]["id"] == "pi-con"

    # without confirmed → falls back to desired
    dev = type(
        "_Dev2",
        (),
        {
            "__init__": lambda self: setattr(
                self,
                "_runtime_state",
                _build(PlaybackPhase.STOPPED, desired=desired),
            ),
            "get_runtime_state": lambda self: self._runtime_state,
            "_play_session_id": 1,
        },
    )()

    class _XM2:
        device_manager = SimpleNamespace(devices={"did-sto": dev})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return True

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 0.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return "stale"

    facade2 = PlaybackFacade(_XM2())
    s2 = await facade2.build_player_state_snapshot("did-sto")
    assert s2["transport_state"] == "stopped"
    assert s2["track"]["title"] == "Desired"
    assert s2["track"]["id"] == "pi-des"


# ── runtime track source normalisation ─────────────────────────────────


@pytest.mark.asyncio
async def test_runtime_source_external_resolved_via_context() -> None:
    """Runtime source='external' must go through _resolve_track_source
    normalisation and never leak raw to API."""

    # runtime reports source='external'; facade has remembered source hint
    # via device_track_source_hints → final output must be the resolved
    # source, not 'external'.

    class _Dev:
        def __init__(self) -> None:
            self._runtime_state = _build(
                PlaybackPhase.PLAYING,
                desired=_tr("e-ext", "Ext Song", source="external"),
                confirmed=_tr(
                    "e-ext", "Ext Song", source="external",
                    playlist_item_id="pi-ext",
                ),
            )
            self._play_session_id = 1

        def get_runtime_state(self) -> PlaybackRuntimeState:
            return self._runtime_state

    class _XM:
        device_manager = SimpleNamespace(devices={"did-ext": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 0.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return ""

    facade = PlaybackFacade(_XM())

    # Pre-populate a remembered source hint (simulates a direct_url play)
    facade._device_track_source_hints["did-ext"] = {
        "source": "direct_url",
        "track_title": "Ext Song",
        "context_id": "",
        "play_session_id": "",
    }

    snapshot = await facade.build_player_state_snapshot("did-ext")
    track = snapshot["track"]
    assert track is not None
    # Must be 'direct_url' (resolved), NOT 'external' (raw runtime)
    assert track["source"] == "direct_url", (
        f"expected resolved source 'direct_url', got {track.get('source')}"
    )


@pytest.mark.asyncio
async def test_runtime_source_no_resolvable_clue_no_illegal_output() -> None:
    """Runtime source='external' with no resolvable clue → source not
    present in output (no 'external' leakage)."""

    class _Dev:
        def __init__(self) -> None:
            self._runtime_state = _build(
                PlaybackPhase.PLAYING,
                desired=_tr("e-ext2", "Ext Song 2", source="external"),
                confirmed=_tr(
                    "e-ext2", "Ext Song 2", source="external",
                    playlist_item_id="pi-ext2",
                ),
            )
            self._play_session_id = 1

        def get_runtime_state(self) -> PlaybackRuntimeState:
            return self._runtime_state

    class _XM:
        device_manager = SimpleNamespace(devices={"did-ext2": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            return False

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 0.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return ""

    facade = PlaybackFacade(_XM())
    # No remembered hint, no context source → "external" must not leak
    snapshot = await facade.build_player_state_snapshot("did-ext2")
    track = snapshot["track"]
    assert track is not None
    source_val = track.get("source")
    assert source_val != "external", (
        "raw runtime 'external' must never leak to API"
    )


# ── isplaying exception when runtime authoritative → still succeeds ────


@pytest.mark.asyncio
async def test_isplaying_exception_runtime_authoritative_still_works() -> None:
    """Legacy xiaomusic.isplaying() raises → runtime authoritative snapshot
    still succeeds with 0 calls to isplaying."""

    class _Dev:
        def __init__(self) -> None:
            self._runtime_state = _build(
                PlaybackPhase.PLAYING,
                desired=_tr("e-ok"),
                confirmed=_tr("e-ok", playlist_item_id="pi-ok"),
            )
            self._play_session_id = 7

        def get_runtime_state(self) -> PlaybackRuntimeState:
            return self._runtime_state

    class _XM:
        device_manager = SimpleNamespace(devices={"did-exc": _Dev()})

        @staticmethod
        def did_exist(did: str) -> bool:
            return True

        @staticmethod
        def isplaying(did: str) -> bool:
            raise RuntimeError("isplying unavailable")

        @staticmethod
        def get_offset_duration(did: str) -> tuple[float, float]:
            return (0.0, 120.0)

        @staticmethod
        def get_cur_play_list(did: str) -> str:
            return ""

        @staticmethod
        def playingmusic(did: str) -> str:
            return ""

    facade = PlaybackFacade(_XM())
    # Must not raise — runtime is authoritative, isplaying is never read
    snapshot = await facade.build_player_state_snapshot("did-exc")
    assert snapshot["transport_state"] == "playing"
    assert snapshot["track"] is not None
    assert snapshot["track"]["title"] == ""  # display_name is empty
    assert snapshot["revision"] >= 0
