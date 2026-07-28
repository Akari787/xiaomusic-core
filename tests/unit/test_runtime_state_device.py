"""Tests for T02-B1: XiaoMusicDevice owns PlaybackRuntimeState."""

from __future__ import annotations

import ast
import asyncio
import logging
import time
import types

import pytest

import xiaomusic.device_player as dp
from xiaomusic.config import Device
from xiaomusic.const import PLAY_TYPE_ALL
from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.events import PLAYER_STATE_CHANGED
from xiaomusic.playback.runtime_state import (
    LifecycleToken,
    PlaybackPhase,
    PlaybackRuntimeState,
    TrackReference,
    TransitionError,
)

# ── lightweight __new__ fixture (for setter tests only) ────────────────

def _make_device_via_new(device_id="did-test") -> XiaoMusicDevice:
    """Create via __new__ -does NOT test __init__. Use for setter isolation."""
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("test")
    d._play_list_items = []
    d._current_index = -1
    d._play_session_id = 0
    d._last_cmd = ""
    d.is_playing = False
    d._start_time = 0
    d._paused_time = 0
    d._duration = 0
    d._play_failed_cnt = 0
    d._play_fail_first_ts = 0.0
    d._play_fail_last_reason = ""
    d._degraded = False
    d._degraded_notified = False
    d._last_volume = 0
    d._next_timer = None
    d._autonext_guard_task = None
    d._playback_confirm_task = None
    d._playback_status_probe_task = None
    d._timer_expiry_false_count = 0
    d._bg_confirm_false_count = 0
    d._timer_expiry_playing_grace_count = 1  # next expiry triggers advance
    d._timer_expiry_unknown_grace_count = 0
    d._playlist_session_shuffled = False
    d._command_arbiter = None  # T04-B: per-device command arbiter (lazy)
    d._runtime_state = PlaybackRuntimeState()
    d.device = types.SimpleNamespace(did=device_id, play_type=PLAY_TYPE_ALL)
    d.config = types.SimpleNamespace(delay_sec=0, verbose=False)
    d.event_bus = None
    d.group_name = "test"
    d.xiaomusic = types.SimpleNamespace(
        device_manager=types.SimpleNamespace(
            get_group_device_id_list=lambda g: [],
            get_group_devices=lambda g: {},
        ),
    )
    return d


# ── real __init__ tests ────────────────────────────────────────────────

def _fake_xm():
    """Minimal XiaoMusic fake for real __init__ instantiation."""
    return types.SimpleNamespace(
        config=types.SimpleNamespace(
            delay_sec=0, verbose=False, ffmpeg_location="",
            music_list_json="[]",
        ),
        log=logging.getLogger("test"),
        auth_manager=types.SimpleNamespace(),
        music_library=types.SimpleNamespace(
            music_list={"全部": []},
            is_music_exist=lambda n: True,
        ),
        device_manager=types.SimpleNamespace(
            get_group_device_id_list=lambda g: [],
            get_group_devices=lambda g: {},
        ),
        event_bus=None,
        analytics=types.SimpleNamespace(
            send_play_event=lambda *a, **k: None,
        ),
    )


def test_real_init_produces_idle_state():
    xm = _fake_xm()
    dev = Device(
        did="did-1", device_id="did-1", hardware="OH2P", name="Test",
        play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={},
    )
    d = XiaoMusicDevice(xm, dev, group_name="g")
    s = d.get_runtime_state()
    assert isinstance(s, PlaybackRuntimeState)
    assert s.phase == PlaybackPhase.IDLE


def test_real_init_two_devices_have_independent_states():
    xm = _fake_xm()
    d1 = XiaoMusicDevice(
        xm,
        Device(did="d1", device_id="d1", hardware="OH2P", name="A", play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={}),
        group_name="g",
    )
    d2 = XiaoMusicDevice(
        xm,
        Device(did="d2", device_id="d2", hardware="OH2P", name="B", play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={}),
        group_name="g",
    )
    from xiaomusic.playback.runtime_state import begin_resolve
    d1._set_runtime_state(begin_resolve(d1.get_runtime_state(), updated_at=1.0))
    assert d1.get_runtime_state().phase == PlaybackPhase.RESOLVING
    assert d2.get_runtime_state().phase == PlaybackPhase.IDLE


# ── getter ─────────────────────────────────────────────────────────────

def test_getter_returns_same_object():
    d = _make_device_via_new()
    s1 = d.get_runtime_state()
    s2 = d.get_runtime_state()
    assert s1 is s2


# ── setter ─────────────────────────────────────────────────────────────

def test_setter_updates_and_returns():
    d = _make_device_via_new()
    from xiaomusic.playback.runtime_state import begin_resolve
    s1 = d.get_runtime_state()
    s2 = begin_resolve(s1, updated_at=1.0)
    ret = d._set_runtime_state(s2)
    assert ret is s2
    assert d.get_runtime_state() is s2
    assert d.get_runtime_state().phase == PlaybackPhase.RESOLVING


def test_setter_rejects_none():
    d = _make_device_via_new()
    with pytest.raises(TypeError):
        d._set_runtime_state(None)


def test_setter_rejects_dict():
    d = _make_device_via_new()
    with pytest.raises(TypeError):
        d._set_runtime_state({})


def test_setter_rejects_str():
    d = _make_device_via_new()
    with pytest.raises(TypeError):
        d._set_runtime_state("idle")


# ── immutability ───────────────────────────────────────────────────────

def test_old_state_not_mutated_after_set():
    d = _make_device_via_new()
    s1 = d.get_runtime_state()
    from xiaomusic.playback.runtime_state import begin_resolve
    s2 = begin_resolve(s1, updated_at=1.0)
    d._set_runtime_state(s2)
    assert s1.phase == PlaybackPhase.IDLE


# ── AST guard ──────────────────────────────────────────────────────────

def test_runtime_state_writes_only_in_allowed_methods():
    """AST: _runtime_state + lifecycle IDs only set via _set_runtime_state."""
    with open(dp.__file__) as f:
        tree = ast.parse(f.read())

    LIFECYCLE_IDS = {"queue_session_id", "command_generation", "track_attempt_id"}
    ALLOWED_RS_METHODS = {"__init__", "_set_runtime_state"}

    class WriteChecker(ast.NodeVisitor):
        def __init__(self):
            self.func_stack: list[str] = []
            self.violations: list[tuple[int, str, str]] = []
            self._rs_writes: dict[str, int] = {}

        def visit_FunctionDef(self, node):
            self.func_stack.append(node.name)
            self.generic_visit(node)
            self.func_stack.pop()

        def visit_AsyncFunctionDef(self, node):
            self.func_stack.append(node.name)
            self.generic_visit(node)
            self.func_stack.pop()

        def _check(self, target, lineno):
            if not isinstance(target, ast.Attribute):
                return
            if not (isinstance(target.value, ast.Name) and target.value.id == "self"):
                return
            ctx = self.func_stack[-1] if self.func_stack else "<module>"
            if target.attr == "_runtime_state":
                self._rs_writes.setdefault(ctx, 0)
                self._rs_writes[ctx] += 1
                if ctx not in ALLOWED_RS_METHODS:
                    self.violations.append((lineno, ctx, "_runtime_state"))
            elif target.attr in LIFECYCLE_IDS:
                # Direct self.queue_session_id etc. NEVER allowed
                self.violations.append((lineno, ctx, target.attr))

        def visit_Assign(self, node):
            for target in node.targets:
                self._check(target, node.lineno)
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            self._check(node.target, node.lineno)
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            self._check(node.target, node.lineno)
            self.generic_visit(node)

    checker = WriteChecker()
    checker.visit(tree)

    assert not checker.violations, f"Unauthorized writes: {checker.violations}"
    assert "__init__" in checker._rs_writes
    assert "_set_runtime_state" in checker._rs_writes


# ── AST guard: reducer call-site enforcement ──────────────────────────

_REDUCER_WRAPPER_MAP = {
    "_pause_transition": "_pause_runtime",
    "begin_stop": "_begin_runtime_stop",
    "complete_stop": "_complete_runtime_stop",
    "report_failure": "_report_runtime_failure",
}


def test_pause_stop_reducers_only_called_from_wrappers():
    """Each reducer (_pause_transition, begin_stop, complete_stop)
    must only be called from its designated wrapper method."""
    with open(dp.__file__) as f:
        tree = ast.parse(f.read())

    class ReducerCallChecker(ast.NodeVisitor):
        def __init__(self):
            self.func_stack: list[str] = []
            self.violations: list[tuple[int, str, str]] = []

        def visit_FunctionDef(self, node):
            self.func_stack.append(node.name)
            self.generic_visit(node)
            self.func_stack.pop()

        def visit_AsyncFunctionDef(self, node):
            self.func_stack.append(node.name)
            self.generic_visit(node)
            self.func_stack.pop()

        def visit_Call(self, node):
            ctx = self.func_stack[-1] if self.func_stack else "<module>"
            if isinstance(node.func, ast.Name) and node.func.id in _REDUCER_WRAPPER_MAP:
                reducer = node.func.id
                allowed_wrapper = _REDUCER_WRAPPER_MAP[reducer]
                if ctx != allowed_wrapper:
                    self.violations.append(
                        (node.lineno, ctx, reducer)
                    )
            self.generic_visit(node)

    c = ReducerCallChecker()
    c.visit(tree)
    assert not c.violations, (
        f"Reducer called outside wrapper: {c.violations}"
    )


# ── lifecycle wrapper tests ───────────────────────────────────────────

def test_start_queue_session_increments_only_queue():
    d = _make_device_via_new()
    s_before = d.get_runtime_state()
    s = d._start_queue_session(updated_at=1.0)
    assert s.queue_session_id == s_before.queue_session_id + 1
    assert s.command_generation == s_before.command_generation
    assert s.track_attempt_id == s_before.track_attempt_id


def test_accept_command_increments_only_command():
    d = _make_device_via_new()
    s_before = d.get_runtime_state()
    s = d._accept_command(updated_at=1.0)
    assert s.command_generation == s_before.command_generation + 1
    assert s.queue_session_id == s_before.queue_session_id
    assert s.track_attempt_id == s_before.track_attempt_id


def test_start_track_attempt_increments_only_attempt_and_returns_token():
    d = _make_device_via_new()
    s_before = d.get_runtime_state()
    token = d._start_track_attempt(updated_at=1.0)
    s = d.get_runtime_state()
    assert s.track_attempt_id == s_before.track_attempt_id + 1
    assert s.queue_session_id == s_before.queue_session_id
    assert s.command_generation == s_before.command_generation
    # Token matches current state (captured after increment)
    assert token.queue_session_id == s.queue_session_id
    assert token.command_generation == s.command_generation
    assert token.track_attempt_id == s.track_attempt_id


def test_two_devices_independent_lifecycle():
    d1 = _make_device_via_new("d1")
    d2 = _make_device_via_new("d2")
    d1._start_queue_session(updated_at=1.0)
    assert d1.get_runtime_state().queue_session_id == 1
    assert d2.get_runtime_state().queue_session_id == 0


def test_stale_true_after_any_change():
    d = _make_device_via_new()
    token = d._start_track_attempt(updated_at=1.0)
    assert not d._is_lifecycle_token_stale(token)
    d._accept_command(updated_at=2.0)
    assert d._is_lifecycle_token_stale(token)


def test_capture_token_matches_current():
    d = _make_device_via_new()
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    d._start_track_attempt(updated_at=1.0)
    token = d._capture_lifecycle_token()
    s = d.get_runtime_state()
    assert token.queue_session_id == s.queue_session_id
    assert token.command_generation == s.command_generation
    assert token.track_attempt_id == s.track_attempt_id


# ── real-init lifecycle tests ─────────────────────────────────────────

def test_real_init_full_lifecycle_sequence():
    """Real init -queue→command→attempt: all three go 0-, token matches, stale after."""
    xm = _fake_xm()
    dev = Device(
        did="d1", device_id="d1", hardware="OH2P", name="Test",
        play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={},
    )
    d = XiaoMusicDevice(xm, dev, group_name="g")

    # Initial: all 0
    s0 = d.get_runtime_state()
    assert s0.queue_session_id == 0 and s0.command_generation == 0 and s0.track_attempt_id == 0

    # Start queue session
    d._start_queue_session(updated_at=1.0)
    s1 = d.get_runtime_state()
    assert s1.queue_session_id == 1 and s1.command_generation == 0 and s1.track_attempt_id == 0

    # Accept command
    d._accept_command(updated_at=2.0)
    s2 = d.get_runtime_state()
    assert s2.queue_session_id == 1 and s2.command_generation == 1 and s2.track_attempt_id == 0

    # Start track attempt -get token
    token = d._start_track_attempt(updated_at=3.0)
    s3 = d.get_runtime_state()
    assert s3.queue_session_id == 1 and s3.command_generation == 1 and s3.track_attempt_id == 1
    assert token.queue_session_id == 1 and token.command_generation == 1 and token.track_attempt_id == 1
    assert not d._is_lifecycle_token_stale(token)

    # Another command -old token stale
    d._accept_command(updated_at=4.0)
    assert d._is_lifecycle_token_stale(token)


def test_real_init_two_devices_independent_lifecycle():
    xm = _fake_xm()
    d1 = XiaoMusicDevice(xm, Device(did="d1", device_id="d1", hardware="OH2P", name="A", play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={}), group_name="g")
    d2 = XiaoMusicDevice(xm, Device(did="d2", device_id="d2", hardware="OH2P", name="B", play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={}), group_name="g")
    d1._start_queue_session(updated_at=1.0)
    assert d1.get_runtime_state().queue_session_id == 1
    assert d2.get_runtime_state().queue_session_id == 0


# ── _begin_runtime_play_request wrapper tests ────────────────────────


def test_begin_runtime_play_request_idle_to_resolving_ids_unchanged():
    """From IDLE: phase→RESOLVING, desired_track set, three IDs unchanged."""
    d = _make_device_via_new()
    s_before = d.get_runtime_state()
    track = TrackReference(entity_id="e1", display_name="song1")

    s = d._begin_runtime_play_request(desired_track=track, updated_at=1.0)

    assert s.phase == PlaybackPhase.RESOLVING
    assert s.desired_track == track
    assert s.transition_reason == "begin_play_request"
    assert s.updated_at == 1.0
    # three IDs unchanged
    assert s.queue_session_id == s_before.queue_session_id
    assert s.command_generation == s_before.command_generation
    assert s.track_attempt_id == s_before.track_attempt_id
    # confirmed_track / expected_end_at / failure preserved
    assert s.confirmed_track == s_before.confirmed_track
    assert s.expected_end_at == s_before.expected_end_at
    assert s.failure == s_before.failure


def test_begin_runtime_play_request_playing_to_switching_confirmed_preserved():
    """From PLAYING: phase→SWITCHING, confirmed_track preserved, IDs unchanged."""
    from xiaomusic.playback.runtime_state import (
        begin_confirm,
        begin_dispatch,
        begin_resolve,
        confirm_playing,
    )

    d = _make_device_via_new()
    old_confirmed = TrackReference(entity_id="old", display_name="old-song")
    # IDLE -RESOLVING -DISPATCHING -CONFIRMING -PLAYING
    d._set_runtime_state(
        confirm_playing(
            begin_confirm(
                begin_dispatch(
                    begin_resolve(d.get_runtime_state(), updated_at=0.0),
                    updated_at=0.2,
                ),
                updated_at=0.4,
            ),
            confirmed_track=old_confirmed,
            updated_at=0.6,
        )
    )
    s_before = d.get_runtime_state()
    assert s_before.phase == PlaybackPhase.PLAYING
    assert s_before.confirmed_track == old_confirmed

    new_desired = TrackReference(entity_id="new", display_name="new-song")
    s = d._begin_runtime_play_request(desired_track=new_desired, updated_at=2.0)

    assert s.phase == PlaybackPhase.SWITCHING
    assert s.desired_track == new_desired
    assert s.confirmed_track == old_confirmed  # preserved
    assert s.transition_reason == "begin_play_request"
    assert s.updated_at == 2.0
    assert s.expected_end_at is None  # cleared
    # three IDs unchanged
    assert s.queue_session_id == s_before.queue_session_id
    assert s.command_generation == s_before.command_generation
    assert s.track_attempt_id == s_before.track_attempt_id


def test_begin_runtime_play_request_dispatching_to_resolving_latest_wins():
    """From DISPATCHING: phase→RESOLVING (latest-wins), IDs unchanged."""
    from xiaomusic.playback.runtime_state import begin_dispatch, begin_resolve

    d = _make_device_via_new()
    # IDLE -RESOLVING -DISPATCHING
    d._set_runtime_state(
        begin_dispatch(
            begin_resolve(d.get_runtime_state(), updated_at=0.0),
            updated_at=0.5,
        )
    )
    s_before = d.get_runtime_state()
    assert s_before.phase == PlaybackPhase.DISPATCHING

    track = TrackReference(entity_id="latest", display_name="latest-song")
    s = d._begin_runtime_play_request(desired_track=track, updated_at=3.0)

    assert s.phase == PlaybackPhase.RESOLVING
    assert s.desired_track == track
    assert s.transition_reason == "begin_play_request"
    assert s.updated_at == 3.0
    # three IDs unchanged
    assert s.queue_session_id == s_before.queue_session_id
    assert s.command_generation == s_before.command_generation
    assert s.track_attempt_id == s_before.track_attempt_id


def test_begin_runtime_play_request_stopping_raises_transition_error_state_unchanged():
    """From STOPPING: raises TransitionError, state unchanged."""
    from xiaomusic.playback.runtime_state import begin_resolve, begin_stop

    d = _make_device_via_new()
    # IDLE -RESOLVING -STOPPING
    d._set_runtime_state(
        begin_stop(
            begin_resolve(d.get_runtime_state(), updated_at=0.0),
            updated_at=0.5,
        )
    )
    s_before = d.get_runtime_state()
    assert s_before.phase == PlaybackPhase.STOPPING

    track = TrackReference(entity_id="blocked", display_name="blocked-song")
    with pytest.raises(TransitionError):
        d._begin_runtime_play_request(desired_track=track, updated_at=4.0)

    s_after = d.get_runtime_state()
    # State unchanged (same object identity -immutable, but value equal)
    assert s_after is s_before
    assert s_after.phase == PlaybackPhase.STOPPING
    assert s_after.queue_session_id == s_before.queue_session_id
    assert s_after.command_generation == s_before.command_generation
    assert s_after.track_attempt_id == s_before.track_attempt_id


# ── _begin_runtime_play_dispatch tests ─────────────────────────────


def test_begin_runtime_play_dispatch_resolving_to_dispatching():
    """RESOLVING -DISPATCHING, IDs unchanged."""
    from xiaomusic.playback.runtime_state import begin_resolve

    d = _make_device_via_new()
    d._set_runtime_state(begin_resolve(d.get_runtime_state(), updated_at=1.0))
    s_before = d.get_runtime_state()
    assert s_before.phase == PlaybackPhase.RESOLVING

    s = d._begin_runtime_play_dispatch(updated_at=2.0)

    assert s.phase == PlaybackPhase.DISPATCHING
    assert s.transition_reason == "begin_play_dispatch"
    assert s.updated_at == 2.0
    assert s.queue_session_id == s_before.queue_session_id
    assert s.command_generation == s_before.command_generation
    assert s.track_attempt_id == s_before.track_attempt_id


def test_begin_runtime_play_dispatch_switching_to_dispatching_preserves_desired_confirmed():
    """SWITCHING -DISPATCHING, desired_track and confirmed_track preserved, IDs unchanged."""
    from xiaomusic.playback.runtime_state import (
        begin_confirm,
        begin_dispatch,
        begin_resolve,
        begin_switch,
        confirm_playing,
    )

    d = _make_device_via_new()
    first_track = TrackReference(entity_id="e1", display_name="first")
    second_track = TrackReference(entity_id="e2", display_name="second")

    # Build: PLAYING (confirmed=first) -SWITCHING (desired=second)
    d._set_runtime_state(
        begin_switch(
            confirm_playing(
                begin_confirm(
                    begin_dispatch(
                        begin_resolve(d.get_runtime_state(), updated_at=1.0),
                        updated_at=1.1,
                    ),
                    updated_at=1.2,
                ),
                confirmed_track=first_track,
                updated_at=1.3,
            ),
            desired_track=second_track,
            updated_at=2.0,
        )
    )
    s_before = d.get_runtime_state()
    assert s_before.phase == PlaybackPhase.SWITCHING
    assert s_before.desired_track == second_track
    assert s_before.confirmed_track == first_track

    s = d._begin_runtime_play_dispatch(updated_at=3.0)

    assert s.phase == PlaybackPhase.DISPATCHING
    assert s.desired_track == second_track
    assert s.confirmed_track == first_track
    assert s.queue_session_id == s_before.queue_session_id
    assert s.command_generation == s_before.command_generation
    assert s.track_attempt_id == s_before.track_attempt_id


def test_begin_runtime_play_dispatch_idle_raises_transition_error_state_unchanged():
    """IDLE -TransitionError, state object unchanged."""
    d = _make_device_via_new()
    s_before = d.get_runtime_state()
    assert s_before.phase == PlaybackPhase.IDLE

    with pytest.raises(TransitionError):
        d._begin_runtime_play_dispatch(updated_at=1.0)

    s_after = d.get_runtime_state()
    assert s_after is s_before
    assert s_after.phase == PlaybackPhase.IDLE
    assert s_after.queue_session_id == s_before.queue_session_id


def test_begin_runtime_play_dispatch_stopping_raises_transition_error_state_unchanged():
    """STOPPING -TransitionError, state object unchanged."""
    from xiaomusic.playback.runtime_state import begin_resolve, begin_stop

    d = _make_device_via_new()
    d._set_runtime_state(
        begin_stop(
            begin_resolve(d.get_runtime_state(), updated_at=1.0),
            updated_at=1.5,
        )
    )
    s_before = d.get_runtime_state()
    assert s_before.phase == PlaybackPhase.STOPPING

    with pytest.raises(TransitionError):
        d._begin_runtime_play_dispatch(updated_at=2.0)

    s_after = d.get_runtime_state()
    assert s_after is s_before
    assert s_after.phase == PlaybackPhase.STOPPING
    assert s_after.queue_session_id == s_before.queue_session_id


def test_begin_runtime_play_dispatch_ids_unchanged():
    """All three lifecycle IDs preserved across dispatch."""
    from xiaomusic.playback.runtime_state import begin_resolve

    d = _make_device_via_new()
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.1)
    d._start_track_attempt(updated_at=1.2)
    d._set_runtime_state(begin_resolve(d.get_runtime_state(), updated_at=2.0))
    s_before = d.get_runtime_state()
    assert s_before.queue_session_id == 1
    assert s_before.command_generation == 1
    assert s_before.track_attempt_id == 1

    s = d._begin_runtime_play_dispatch(updated_at=3.0)

    assert s.phase == PlaybackPhase.DISPATCHING
    assert s.queue_session_id == 1
    assert s.command_generation == 1
    assert s.track_attempt_id == 1


# ── AST guard: begin_play_request call point ────────────────────────

def test_begin_play_request_only_called_in_wrapper():
    """AST: begin_play_request is only called from _begin_runtime_play_request."""
    with open(dp.__file__) as f:
        tree = ast.parse(f.read())

    class CallChecker(ast.NodeVisitor):
        def __init__(self):
            self.func_stack: list[str] = []
            self.violations: list[tuple[int, str]] = []

        def visit_FunctionDef(self, node):
            self.func_stack.append(node.name)
            self.generic_visit(node)
            self.func_stack.pop()

        def visit_AsyncFunctionDef(self, node):
            self.func_stack.append(node.name)
            self.generic_visit(node)
            self.func_stack.pop()

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == "begin_play_request":
                ctx = self.func_stack[-1] if self.func_stack else "<module>"
                if ctx != "_begin_runtime_play_request":
                    self.violations.append((node.lineno, ctx))
            self.generic_visit(node)

    checker = CallChecker()
    checker.visit(tree)

    assert not checker.violations, (
        f"begin_play_request called outside _begin_runtime_play_request: "
        f"{checker.violations}"
    )


def test_begin_play_dispatch_only_called_in_wrapper():
    """AST: begin_play_dispatch is only called from _begin_runtime_play_dispatch."""
    with open(dp.__file__) as f:
        tree = ast.parse(f.read())

    class CallChecker(ast.NodeVisitor):
        def __init__(self):
            self.func_stack: list[str] = []
            self.violations: list[tuple[int, str]] = []

        def visit_FunctionDef(self, node):
            self.func_stack.append(node.name)
            self.generic_visit(node)
            self.func_stack.pop()

        def visit_AsyncFunctionDef(self, node):
            self.func_stack.append(node.name)
            self.generic_visit(node)
            self.func_stack.pop()

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == "begin_play_dispatch":
                ctx = self.func_stack[-1] if self.func_stack else "<module>"
                if ctx != "_begin_runtime_play_dispatch":
                    self.violations.append((node.lineno, ctx))
            self.generic_visit(node)

    checker = CallChecker()
    checker.visit(tree)

    assert not checker.violations, (
        f"begin_play_dispatch called outside _begin_runtime_play_dispatch: "
        f"{checker.violations}"
    )


# ── on_external_url_play queue integration ─────────────────────────────

@pytest.mark.asyncio
async def test_external_url_play_bumps_queue_once():
    """on_external_url_play bumps both queue and command by 1, attempt stays 0."""
    xm = _fake_xm()
    dev = Device(did="d1", device_id="d1", hardware="OH2P", name="T", play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm, dev, group_name="g")
    import asyncio
    d.cancel_group_next_timer = lambda: asyncio.sleep(0)
    d._invalidate_manual_navigation = lambda reason: None

    await d.on_external_url_play()
    s = d.get_runtime_state()
    assert s.queue_session_id == 1 and s.command_generation == 1 and s.track_attempt_id == 0

    await d.on_external_url_play()
    s2 = d.get_runtime_state()
    assert s2.queue_session_id == 2 and s2.command_generation == 2 and s2.track_attempt_id == 0


@pytest.mark.asyncio
async def test_external_url_play_bumps_queue_even_on_exception():
    """Queue increments before any await -even if cancel_group_next_timer raises."""
    xm = _fake_xm()
    dev = Device(did="d1", device_id="d1", hardware="OH2P", name="T", play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm, dev, group_name="g")

    class TestError(Exception):
        pass

    d.cancel_group_next_timer = lambda: (_ for _ in ()).throw(TestError("boom"))

    token_before = d._capture_lifecycle_token()
    with pytest.raises(TestError):
        await d.on_external_url_play()

    s = d.get_runtime_state()
    assert s.queue_session_id == 1 and s.command_generation == 1 and s.track_attempt_id == 0
    assert d._is_lifecycle_token_stale(token_before)


def test_start_queue_session_called_only_in_on_external_url_play():
    """AST: self._start_queue_session(...) called exactly once, only in on_external_url_play."""
    _check_calls("_start_queue_session", {"on_external_url_play"})


def test_accept_command_called_only_in_allowed_funcs():
    """AST: _accept_command calls in on_external_url_play, _queue_manual_navigation,
    stop, pause, _play_next, play, playlocal, _play_internal, submit_external_url_play,
    check_replay.
    submit_external_url_play may have 2 call sites (new-command branches).
    T04-C2b/C2c/C3b1 constraint."""
    _check_calls("_accept_command", {
        "on_external_url_play",
        "_queue_manual_navigation",
        "stop",
        "pause",
        "_play_next",
        "play",
        "playlocal",
        "_play_internal",
        "_submit_auto_retry",
        "submit_external_url_play",
        "check_replay",
    })


def _check_calls(method_name: str, allowed_funcs: set[str]):
    import ast as _ast
    with open(dp.__file__) as f:
        tree = _ast.parse(f.read())

    calls: list[tuple[int, str]] = []
    func_stack: list[str] = []

    class Visitor(_ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            func_stack.append(node.name)
            self.generic_visit(node)
            func_stack.pop()

        def visit_AsyncFunctionDef(self, node):
            func_stack.append(node.name)
            self.generic_visit(node)
            func_stack.pop()

        def visit_Call(self, node):
            ctx = func_stack[-1] if func_stack else "<module>"
            if isinstance(node.func, _ast.Attribute) and node.func.attr == method_name:
                if isinstance(node.func.value, _ast.Name) and node.func.value.id == "self":
                    calls.append((node.lineno, ctx))
            self.generic_visit(node)

    Visitor().visit(tree)

    assert calls, f"No {method_name} calls found"
    # Only check that all actual callers are in the allowed set;
    # a single function may have multiple call sites (e.g., two branches).
    actual_callers = {c[1] for c in calls}
    assert actual_callers == allowed_funcs, (
        f"Expected callers {allowed_funcs}, got {actual_callers} (all calls: {calls})"
    )


# ── manual next/prev command generation ────────────────────────────────

@pytest.mark.asyncio
async def test_manual_next_accepts_command():
    """Manual next bumps command_generation, not queue."""
    d = _make_device_via_new()
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "", "entity_id": ""},
        {"display_name": "B", "legacy_name": "B", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )
    d._start_queue_session(updated_at=1.0)
    q_before = d.get_runtime_state().queue_session_id
    c_before = d.get_runtime_state().command_generation

    await d._queue_manual_navigation(direction="next")
    s = d.get_runtime_state()
    assert s.command_generation == c_before + 1
    assert s.queue_session_id == q_before


@pytest.mark.asyncio
async def test_three_manual_nexts_bump_command_three_times():
    """Three accepted nexts -command+3, queue unchanged."""
    d = _make_device_via_new()
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "", "entity_id": ""},
        {"display_name": "B", "legacy_name": "B", "item_id": "", "entity_id": ""},
        {"display_name": "C", "legacy_name": "C", "item_id": "", "entity_id": ""},
        {"display_name": "D", "legacy_name": "D", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )
    d._start_queue_session(updated_at=1.0)
    q_before = d.get_runtime_state().queue_session_id
    c_before = d.get_runtime_state().command_generation

    for _ in range(3):
        await d._queue_manual_navigation(direction="next")
    s = d.get_runtime_state()
    assert s.command_generation == c_before + 3
    assert s.queue_session_id == q_before


@pytest.mark.asyncio
async def test_manual_prev_accepts_command():
    """Manual previous bumps command_generation."""
    d = _make_device_via_new()
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "", "entity_id": ""},
        {"display_name": "B", "legacy_name": "B", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 1
    d.device.cur_music = "B"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )
    d._start_queue_session(updated_at=1.0)
    c_before = d.get_runtime_state().command_generation

    await d._queue_manual_navigation(direction="previous")
    assert d.get_runtime_state().command_generation == c_before + 1


@pytest.mark.asyncio
async def test_manual_next_empty_playlist_no_command_bump():
    """空播放列-next 不递增 command."""
    d = _make_device_via_new()
    d._play_list_items = []
    d._current_index = -1
    d._start_queue_session(updated_at=1.0)
    c_before = d.get_runtime_state().command_generation

    result = await d._queue_manual_navigation(direction="next")
    assert result is False
    assert d.get_runtime_state().command_generation == c_before


def test_new_queue_makes_old_token_stale():
    d = _make_device_via_new()
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    assert not d._is_lifecycle_token_stale(token)
    d._start_queue_session(updated_at=2.0)
    assert d._is_lifecycle_token_stale(token)


# ── token stale before dispatch ────────────────────────────────────────

@pytest.mark.asyncio
async def test_token_stale_before_arbiter_dispatches():
    """Old token stale immediately after accept, before arbiter executor dispatches.

    T04-B: replaces _manual_navigation_worker with arbiter executor.
    """
    import asyncio

    d = _make_device_via_new()
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "", "entity_id": ""},
        {"display_name": "B", "legacy_name": "B", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )
    d._manual_nav_generation = 0
    d._manual_nav_target = None
    d._ensure_manual_navigation_state = lambda: None
    d._manual_nav_lock = asyncio.Lock()
    d._start_queue_session(updated_at=1.0)

    # Make settle instant (deterministic)
    settle_done = asyncio.Event()
    d._wait_manual_navigation_settle = lambda: settle_done.wait()

    # Block _play at dispatch
    play_entered = asyncio.Event()
    play_release = asyncio.Event()
    dispatch_done = asyncio.Event()
    dispatched = []

    async def _blocking_play(name="", **kwargs):
        play_entered.set()
        await asyncio.wait_for(play_release.wait(), timeout=5)
        dispatched.append((kwargs.get("navigation_generation"), name))
        dispatch_done.set()
        return True

    d._play = _blocking_play

    token = d._capture_lifecycle_token()
    assert not d._is_lifecycle_token_stale(token)

    try:
        await d._queue_manual_navigation(direction="next")
        assert d._is_lifecycle_token_stale(token)
        arb = d._command_arbiter
        assert arb is not None

        # Let arbiter executor pass settle and enter _play
        settle_done.set()
        await asyncio.wait_for(play_entered.wait(), timeout=5)
        assert dispatched == []  # not yet dispatched

        # Release _play
        play_release.set()
        # Wait for dispatch to complete via dispatch_done Event
        await asyncio.wait_for(dispatch_done.wait(), timeout=5)
        assert len(dispatched) == 1
        assert dispatched[0][1] == "B"
    finally:
        play_release.set()
        settle_done.set()
        dispatch_done.set()
        await d.close_command_arbiter()


async def _noop():
    pass

async def _noop_list(fast=False):
    return []

# ── stop / pause command generation ────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_accepts_command():
    """stop() on PLAYING: bumps command, queue/attempt unchanged, token stale."""
    d = _make_device_via_new()
    d._start_queue_session(updated_at=1.0)
    # Construct PLAYING: IDLE->RESOLVING->DISPATCHING->CONFIRMING->PLAYING
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="t", source="test"
        ),
        updated_at=2.0,
    )
    d._begin_runtime_play_dispatch(updated_at=3.0)
    d._begin_runtime_confirmation(updated_at=4.0)
    d._confirm_runtime_playing(updated_at=5.0)
    d.do_tts = lambda v: None
    d.cancel_group_next_timer = _noop
    d.group_force_stop_xiaoai = _noop_list
    d._invalidate_manual_navigation = lambda reason: None
    d.event_bus = None
    q_before = d.get_runtime_state().queue_session_id
    c_before = d.get_runtime_state().command_generation
    a_before = d.get_runtime_state().track_attempt_id
    token = d._capture_lifecycle_token()

    stop_done = asyncio.Event()
    _orig_stop_1 = d._execute_stop_intent
    async def _spy_stop_1(payload):
        try:
            await _orig_stop_1(payload)
        finally:
            stop_done.set()
    d._execute_stop_intent = _spy_stop_1

    await d.stop(arg1="notts")
    await asyncio.wait_for(stop_done.wait(), timeout=5.0)

    s = d.get_runtime_state()
    assert s.command_generation == c_before + 1
    assert s.queue_session_id == q_before
    assert s.track_attempt_id == a_before
    assert d._is_lifecycle_token_stale(token)
    await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_pause_accepts_command():
    """pause() on PLAYING: bumps command, queue/attempt unchanged, token stale."""
    d = _make_device_via_new()
    d._start_queue_session(updated_at=1.0)
    # Construct PLAYING: IDLE->RESOLVING->DISPATCHING->CONFIRMING->PLAYING
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="t", source="test"
        ),
        updated_at=2.0,
    )
    d._begin_runtime_play_dispatch(updated_at=3.0)
    d._begin_runtime_confirmation(updated_at=4.0)
    d._confirm_runtime_playing(updated_at=5.0)
    d.cancel_group_next_timer = _noop
    d.group_force_stop_xiaoai = _noop_list
    d._invalidate_manual_navigation = lambda reason: None
    d.event_bus = None
    q_before = d.get_runtime_state().queue_session_id
    c_before = d.get_runtime_state().command_generation
    a_before = d.get_runtime_state().track_attempt_id
    token = d._capture_lifecycle_token()

    pause_done = asyncio.Event()
    _orig_pause_1 = d._execute_pause_intent
    async def _spy_pause_1(payload):
        try:
            await _orig_pause_1(payload)
        finally:
            pause_done.set()
    d._execute_pause_intent = _spy_pause_1

    await d.pause()
    await asyncio.wait_for(pause_done.wait(), timeout=5.0)

    s = d.get_runtime_state()
    assert s.command_generation == c_before + 1
    assert s.queue_session_id == q_before
    assert s.track_attempt_id == a_before
    assert d._is_lifecycle_token_stale(token)
    await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_repeated_stop_each_bumps_command():
    d = _make_device_via_new()
    # Construct PLAYING so stop is accepted
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="t", source="test"
        ),
        updated_at=1.0,
    )
    d._begin_runtime_play_dispatch(updated_at=2.0)
    d._begin_runtime_confirmation(updated_at=3.0)
    d._confirm_runtime_playing(updated_at=4.0)
    d.do_tts = lambda v: None
    d.cancel_group_next_timer = _noop
    d.group_force_stop_xiaoai = _noop_list
    d._invalidate_manual_navigation = lambda reason: None
    d.event_bus = None
    c_before = d.get_runtime_state().command_generation

    await d.stop(arg1="notts")
    # STOPPING: idempotent, second stop still accepted
    await d.stop(arg1="notts")
    assert d.get_runtime_state().command_generation == c_before + 2
    await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_stop_exception_preserves_command_bump():
    """stop: physical exception -> arbiter.last_error, command still bumped, no fake completion."""
    class TestError(Exception):
        pass

    d = _make_device_via_new()
    d._start_queue_session(updated_at=1.0)
    # Construct PLAYING via real wrapper chain: IDLE→RESOLVING→DISPATCHING→CONFIRMING→PLAYING
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="test-song", source="test"
        ),
        updated_at=2.0,
    )
    d._begin_runtime_play_dispatch(updated_at=3.0)
    d._begin_runtime_confirmation(updated_at=4.0)
    d._confirm_runtime_playing(updated_at=5.0)
    d.do_tts = lambda v: None
    async def _raise():
        raise TestError("boom")
    d.cancel_group_next_timer = _raise
    d.group_force_stop_xiaoai = _noop_list
    d._invalidate_manual_navigation = lambda reason: None
    d.event_bus = None

    q_before = d.get_runtime_state().queue_session_id
    c_before = d.get_runtime_state().command_generation
    a_before = d.get_runtime_state().track_attempt_id
    token = d._capture_lifecycle_token()

    # Wrap executor to signal completion
    stop_exc_done = asyncio.Event()
    _orig_stop_exc = d._execute_stop_intent
    async def _spy_stop_exc(payload):
        try:
            await _orig_stop_exc(payload)
        finally:
            stop_exc_done.set()
    d._execute_stop_intent = _spy_stop_exc

    # stop() returns immediately (True), exception is caught by arbiter
    result = await d.stop(arg1="notts")
    assert result is True
    await asyncio.wait_for(stop_exc_done.wait(), timeout=5.0)

    s = d.get_runtime_state()
    assert s.command_generation == c_before + 1
    assert s.queue_session_id == q_before
    assert s.track_attempt_id == a_before
    assert d._is_lifecycle_token_stale(token)
    # Phase stays STOPPING (no fake completion)
    assert s.phase == PlaybackPhase.STOPPING
    # Exception recorded in arbiter.last_error
    arb = d._command_arbiter
    assert arb is not None
    assert isinstance(arb.last_error, TestError)
    await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_pause_exception_preserves_command_bump():
    """pause: physical exception -> arbiter.last_error, command still bumped, no fake completion."""
    class TestError(Exception):
        pass

    d = _make_device_via_new()
    d._start_queue_session(updated_at=1.0)
    # Construct PLAYING via real wrapper chain: IDLE→RESOLVING→DISPATCHING→CONFIRMING→PLAYING
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="test-song", source="test"
        ),
        updated_at=2.0,
    )
    d._begin_runtime_play_dispatch(updated_at=3.0)
    d._begin_runtime_confirmation(updated_at=4.0)
    d._confirm_runtime_playing(updated_at=5.0)
    async def _raise():
        raise TestError("boom")
    d.cancel_group_next_timer = _raise
    d.group_force_stop_xiaoai = _noop_list
    d._invalidate_manual_navigation = lambda reason: None
    d.event_bus = None

    q_before = d.get_runtime_state().queue_session_id
    c_before = d.get_runtime_state().command_generation
    a_before = d.get_runtime_state().track_attempt_id
    token = d._capture_lifecycle_token()

    # Wrap executor to signal completion
    pause_exc_done = asyncio.Event()
    _orig_pause_exc = d._execute_pause_intent
    async def _spy_pause_exc(payload):
        try:
            await _orig_pause_exc(payload)
        finally:
            pause_exc_done.set()
    d._execute_pause_intent = _spy_pause_exc

    # pause() returns immediately (True), exception is caught by arbiter
    result = await d.pause()
    assert result is True
    await asyncio.wait_for(pause_exc_done.wait(), timeout=5.0)

    s = d.get_runtime_state()
    assert s.command_generation == c_before + 1
    assert s.queue_session_id == q_before
    assert s.track_attempt_id == a_before
    assert d._is_lifecycle_token_stale(token)
    # Phase stays PAUSED (no event was fired, but phase already set by acceptance)
    assert s.phase == PlaybackPhase.PAUSED
    # Exception recorded in arbiter.last_error
    arb = d._command_arbiter
    assert arb is not None
    assert isinstance(arb.last_error, TestError)
    await d.close_command_arbiter()

async def _fake_async_play(name="", **kw):
    pass

async def _fake_async_play(name="", **kw):
    pass

async def _async_true():
    return True

async def _async_false():
    return False

# ── _play_next command generation ──────────────────────────────────────

@pytest.mark.asyncio
async def test_play_next_accepts_command():
    """_play_next 调用-+1 command，q/a 不变."""
    from xiaomusic.device_player import XiaoMusicDevice
    d = _make_device_via_new()
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "", "entity_id": ""},
        {"display_name": "B", "legacy_name": "B", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )
    d._play = _fake_async_play
    d._play_next = XiaoMusicDevice._play_next.__get__(d, XiaoMusicDevice)
    d._start_queue_session(updated_at=1.0)
    c_before = d.get_runtime_state().command_generation
    q_before = d.get_runtime_state().queue_session_id
    a_before = d.get_runtime_state().track_attempt_id
    token = d._capture_lifecycle_token()
    await d._play_next(manual=False)
    s = d.get_runtime_state()
    assert s.command_generation == c_before + 1
    assert s.queue_session_id == q_before
    assert s.track_attempt_id == a_before
    assert d._is_lifecycle_token_stale(token)

@pytest.mark.asyncio
async def test_play_next_empty_no_target_still_bumps_command():
    """空播放列-_play_next 返回 False，但 command -+1."""
    from xiaomusic.device_player import XiaoMusicDevice
    d = _make_device_via_new()
    d._play_list_items = []
    d._current_index = -1
    d.device.cur_music = ""
    d._play_next = XiaoMusicDevice._play_next.__get__(d, XiaoMusicDevice)
    d._start_queue_session(updated_at=1.0)
    c_before = d.get_runtime_state().command_generation
    await d._play_next(manual=False)
    assert d.get_runtime_state().command_generation == c_before + 1
# ── production path: timer -_play_next -command ────────────────────

@pytest.mark.asyncio
async def test_timer_grace_exhausted_calls_real_play_next_bumps_command_once():
    """Real timer playing-grace-exhausted submits AUTO_NEXT via arbiter;
    command+1 synchronously; physical play deferred to executor."""
    import asyncio

    from xiaomusic.const import PLAY_TYPE_ALL

    d = _make_device_via_new()
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "", "entity_id": ""},
        {"display_name": "B", "legacy_name": "B", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.device.play_type = PLAY_TYPE_ALL
    d.config = types.SimpleNamespace(delay_sec=0, verbose=False)
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )
    d._play_session_id = 1
    d.MAX_COMPLETION_GRACE_EXTENSIONS = 0
    d._timer_expiry_playing_grace_count = 1
    d.get_if_xiaoai_is_playing = _async_true
    d.stop = lambda arg1="": None

    phys_done = asyncio.Event()
    phys_calls: list = []

    async def _fake_play_next(command_already_accepted=False):
        phys_calls.append(("play_next", command_already_accepted))
        phys_done.set()

    d._play_next = _fake_play_next
    async def _fake_play(name="", **kw):
        phys_calls.append(("play", name))
        phys_done.set()
    d._play = _fake_play
    d._start_queue_session(updated_at=1.0)

    c_before = d.get_runtime_state().command_generation
    q_before = d.get_runtime_state().queue_session_id
    a_before = d.get_runtime_state().track_attempt_id

    await d.set_next_music_timeout(0)
    timer_task = d._next_timer
    assert timer_task is not None
    try:
        await asyncio.wait_for(timer_task, timeout=5)
    except asyncio.TimeoutError:
        timer_task.cancel()
        try:
            await timer_task
        except asyncio.CancelledError:
            pass
        raise

    s = d.get_runtime_state()
    assert s.command_generation == c_before + 1, f"phys_calls={phys_calls} q={s.queue_session_id} c={s.command_generation} a={s.track_attempt_id}"
    assert s.queue_session_id == q_before
    assert s.track_attempt_id == a_before

    # Physical play deferred to arbiter — wait for executor
    await asyncio.wait_for(phys_done.wait(), timeout=2.0)
    assert len(phys_calls) >= 1
    assert any(c[0] == "play_next" for c in phys_calls)


async def _async_false_confirm(n, sid, **kw):
    return False


# ── production path: bg confirm -_play_next -command ────────────────

@pytest.mark.asyncio
async def test_bg_confirm_two_false_preserves_timer_no_autonext():
    """T05-A: Two-False path produces NOT_STARTED observation.

    Timer is preserved, no AUTO_NEXT/RETRY submitted, no _play/_play_next
    called.  Counter set to 2, c/q/a unchanged.
    """
    d = _make_device_via_new()
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "", "entity_id": ""},
        {"display_name": "B", "legacy_name": "B", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda name: True),
    )
    d._play_session_id = 1
    d._confirm_playback_started = _async_false_confirm
    d._is_jellyfin_auto_candidate = lambda **kwargs: False

    # Create a real timer to verify it is preserved
    timer_sentinel = asyncio.create_task(asyncio.sleep(999))
    d._next_timer = timer_sentinel

    play_next_calls = 0
    async def _fake_play_next(command_already_accepted=False):
        nonlocal play_next_calls
        play_next_calls += 1
    d._play_next = _fake_play_next
    d._play = _fake_play_next

    d._start_queue_session(updated_at=1.0)
    before = d.get_runtime_state()

    await d._background_confirm_playback_started(
        name="A",
        sid=1,
        cur_playlist="BGM",
        origin_url="http://x/A.mp3",
        current_url="http://x/A.mp3",
        fast_stop=False,
    )

    after = d.get_runtime_state()
    # No AUTO_NEXT submitted: c unchanged
    assert after.command_generation == before.command_generation
    assert after.queue_session_id == before.queue_session_id
    assert after.track_attempt_id == before.track_attempt_id
    # Counter set to 2 by NOT_STARTED handler
    assert d._bg_confirm_false_count == 2
    # Timer preserved, not cancelled
    assert d._next_timer is timer_sentinel
    assert not d._next_timer.done()
    # No play_next called
    assert play_next_calls == 0

    # Cleanup
    timer_sentinel.cancel()
    try:
        await timer_sentinel
    except asyncio.CancelledError:
        pass


def test_start_track_attempt_called_only_in_playmusic_and_play_url():
    """AST: _start_track_attempt in _playmusic, _try_proxy_fallback,
    and _execute_external_play_intent. T04-C2c constraint."""
    import ast as _ast

    class _Visitor(_ast.NodeVisitor):
        def __init__(self, filename, calls_out):
            super().__init__()
            self._filename = filename
            self._calls = calls_out
            self._stack: list[str] = []

        def visit_FunctionDef(self, n):
            self._stack.append(n.name)
            self.generic_visit(n)
            self._stack.pop()

        def visit_AsyncFunctionDef(self, n):
            self._stack.append(n.name)
            self.generic_visit(n)
            self._stack.pop()

        def visit_Call(self, n):
            ctx = self._stack[-1] if self._stack else "<module>"
            if isinstance(n.func, _ast.Attribute) and n.func.attr == "_start_track_attempt":
                self._calls.append((self._filename, n.lineno, ctx))
            self.generic_visit(n)

    calls: list[tuple[str, int, str]] = []
    for fname in ["xiaomusic/device_player.py", "xiaomusic/xiaomusic.py"]:
        with open(fname) as f:
            _Visitor(fname, calls).visit(_ast.parse(f.read()))

    expected = {
        ("xiaomusic/device_player.py", "_playmusic"),
        ("xiaomusic/device_player.py", "_try_proxy_fallback"),
        ("xiaomusic/device_player.py", "_execute_external_play_intent"),
    }
    actual = {(c[0], c[2]) for c in calls}
    assert calls, "No _start_track_attempt calls"
    assert actual == expected, f"Expected {expected}, got {actual}"

# ── _playmusic track attempt integration ──────────────────────────────

def _build_playmusic_attempt_device():
    import asyncio
    """Real XiaoMusicDevice with minimal mocks for _playmusic testing."""
    from xiaomusic.config import Device

    class _ML:
        music_list = {"全部": ["song1"]}
        async def get_music_url(self, name):
            return "http://x/song1.mp3", "http://x/song1.mp3"
        async def get_music_duration(self, name):
            return 10.0
        def is_jellyfin_url(self, u):
            return False

    class _Analytics:
        async def send_play_event(self, *a, **k):
            pass

    import logging as _logging
    xm = types.SimpleNamespace(
        config=types.SimpleNamespace(delay_sec=0, verbose=False, ffmpeg_location="", jellyfin_proxy_mode="off"),
        log=_logging.getLogger("test-playmusic-attempt"),
        auth_manager=types.SimpleNamespace(mina_call=None),
        music_library=_ML(),
        analytics=_Analytics(),
        device_manager=types.SimpleNamespace(get_group_device_id_list=lambda g: []),
        event_bus=None,
    )
    dev = Device(did="d1", device_id="d1", hardware="", name="", play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm, dev, group_name="g")
    d.cancel_group_next_timer = _noop
    d.group_force_stop_xiaoai = _noop_list
    d._execute_group_stop = lambda fast_stop=False, sid=0: asyncio.sleep(0, result=None)
    d._mark_play_started = lambda **kw: asyncio.sleep(0, result=None)
    d._schedule_playback_confirmation = lambda **kw: None
    d._confirm_playback_started = lambda n, sid, **kw: asyncio.sleep(0, result=True)
    d._refresh_runtime_volume = lambda **kw: asyncio.sleep(0, result=0)
    d._schedule_background_confirm_playback_started = lambda **kw: None
    d.set_next_music_timeout = lambda sec, token=None: _noop
    d._is_jellyfin_auto_candidate = lambda **kw: False
    d._try_proxy_fallback = lambda **kw: ""
    d._handle_play_failure = lambda **kw: asyncio.sleep(0, result=None)
    d.auto_add_song = lambda cur, sec: asyncio.sleep(0, result=None)
    d._start_duration_probe = lambda name, sid, **kw: None
    return d


class _AttemptError(Exception):
    pass


@pytest.mark.asyncio
async def test_playmusic_success_bumps_attempt():
    """Success: attempt 0-, q/c unchanged."""
    d = _build_playmusic_attempt_device()
    group_called = False
    async def _spy_group(url, name=""):
        nonlocal group_called
        group_called = True
        # attempt should already be 1 when group is called
        s = d.get_runtime_state()
        assert s.track_attempt_id == 1
        return [{"code": 0}]
    d.group_player_play = _spy_group

    q_before = d.get_runtime_state().queue_session_id
    c_before = d.get_runtime_state().command_generation
    assert d.get_runtime_state().track_attempt_id == 0

    await d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)

    s = d.get_runtime_state()
    assert s.track_attempt_id == 1
    assert s.queue_session_id == q_before
    assert s.command_generation == c_before
    assert group_called


@pytest.mark.asyncio
async def test_playmusic_player_play_failed_passes_dispatch_attempt_token():
    """The direct non-Jellyfin failure receives its physical dispatch token."""
    d = _build_playmusic_attempt_device()
    dispatch_tokens: list[LifecycleToken] = []
    failure_tokens: list[LifecycleToken] = []

    async def _fail_group(url, name=""):
        dispatch_tokens.append(d._capture_lifecycle_token())
        return [None]

    async def _record_failure(*, name, sid, reason, token):
        assert reason == "player_play_failed"
        failure_tokens.append(token)

    d.group_player_play = _fail_group
    d._handle_play_failure = _record_failure

    result = await d._playmusic(
        "song1", confirm_start_in_background=True, fast_stop=True
    )

    assert result is False
    assert len(dispatch_tokens) == 1
    assert failure_tokens == dispatch_tokens
    assert failure_tokens[0] == d._capture_lifecycle_token()


@pytest.mark.asyncio
async def test_playmusic_dispatch_exception_preserves_attempt():
    """Group throws -attempt still 1."""
    d = _build_playmusic_attempt_device()
    async def _raise_group(url, name=""):
        raise _AttemptError("dispatch failed")
    d.group_player_play = _raise_group

    with pytest.raises(_AttemptError):
        await d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)
    assert d.get_runtime_state().track_attempt_id == 1


@pytest.mark.asyncio
async def test_playmusic_url_throws_before_group_no_attempt():
    """get_music_url raises -attempt stays 0 (attempt is after URL resolution)."""
    d = _build_playmusic_attempt_device()
    async def _raise_url(name):
        raise _AttemptError("url failed")
    d.xiaomusic.music_library.get_music_url = _raise_url

    with pytest.raises(_AttemptError):
        await d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)
    assert d.get_runtime_state().track_attempt_id == 0


@pytest.mark.asyncio
async def test_playmusic_stop_failure_no_attempt_increment():
    """_execute_group_stop raises -attempt stays 0."""
    d = _build_playmusic_attempt_device()
    async def _raise_stop(fast_stop=False, sid=0):
        raise _AttemptError("stop failed")
    d._execute_group_stop = _raise_stop

    with pytest.raises(_AttemptError):
        await d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)
    assert d.get_runtime_state().track_attempt_id == 0


@pytest.mark.asyncio
async def test_playmusic_consecutive_increments_attempt():
    """Two successful dispatches -attempt=2."""
    d = _build_playmusic_attempt_device()
    d.group_player_play = lambda url, name="": asyncio.sleep(0, result=[{"code": 0}])
    await d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)
    assert d.get_runtime_state().track_attempt_id == 1
    await d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)
    assert d.get_runtime_state().track_attempt_id == 2
# ── on_external_url_play one-time init guard ──────────────────────────

@pytest.mark.asyncio
async def test_same_context_twice_only_inits_once():
    """Same context dict -q/c +1 only, session bumps once, cancel once,
    second call preserves manually set playlist/cur_music."""
    xm = _fake_xm()
    dev = Device(did="d1", device_id="d1", hardware="OH2P", name="T", play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm, dev, group_name="g")
    cancel_count = 0
    async def _count_cancel():
        nonlocal cancel_count
        cancel_count += 1
    d.cancel_group_next_timer = _count_cancel
    d._invalidate_manual_navigation = lambda reason: None

    ctx = {}
    await d.on_external_url_play(context=ctx)
    s1 = d.get_runtime_state()
    sid1 = d._play_session_id
    assert s1.queue_session_id == 1 and s1.command_generation == 1
    assert cancel_count == 1

    # Manually set playlist state to verify second call doesn't clear it
    d.device.cur_playlist = "BGM"
    d.device.cur_music = "A"
    d.device.current_display_name = "A"
    d._play_list_items = [{"display_name": "A", "legacy_name": "A", "item_id": "", "entity_id": ""}]

    # Second call with same context -no-op
    await d.on_external_url_play(context=ctx)
    s2 = d.get_runtime_state()
    assert s2.queue_session_id == 1 and s2.command_generation == 1
    assert d._play_session_id == sid1
    assert cancel_count == 1
    assert d.device.cur_playlist == "BGM"
    assert d.device.cur_music == "A"
    assert d.device.current_display_name == "A"
    assert len(d._play_list_items) == 1


@pytest.mark.asyncio
async def test_different_context_each_full_init():
    """Two different contexts -queue+command each +1."""
    xm = _fake_xm()
    dev = Device(did="d1", device_id="d1", hardware="OH2P", name="T", play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm, dev, group_name="g")
    d.cancel_group_next_timer = lambda: asyncio.sleep(0)
    d._invalidate_manual_navigation = lambda reason: None

    await d.on_external_url_play(context={})
    s1 = d.get_runtime_state()
    assert s1.queue_session_id == 1 and s1.command_generation == 1

    await d.on_external_url_play(context={})  # new dict -full init
    s2 = d.get_runtime_state()
    assert s2.queue_session_id == 2 and s2.command_generation == 2


@pytest.mark.asyncio
async def test_context_none_preserves_legacy_always_init():
    """context=None -each call full init (new dict each time)."""
    xm = _fake_xm()
    dev = Device(did="d1", device_id="d1", hardware="OH2P", name="T", play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm, dev, group_name="g")
    d.cancel_group_next_timer = lambda: asyncio.sleep(0)
    d._invalidate_manual_navigation = lambda reason: None

    await d.on_external_url_play(context=None)
    assert d.get_runtime_state().queue_session_id == 1
    await d.on_external_url_play(context=None)
    assert d.get_runtime_state().queue_session_id == 2


@pytest.mark.asyncio
async def test_init_failure_marker_still_true():
    """If first init's cancel_group_next_timer raises, marker is already True,
    so retry with same context does not re-init."""
    class TestError(Exception):
        pass

    xm = _fake_xm()
    dev = Device(did="d1", device_id="d1", hardware="OH2P", name="T", play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm, dev, group_name="g")
    async def _raise():
        raise TestError("boom")
    d.cancel_group_next_timer = _raise
    d._invalidate_manual_navigation = lambda reason: None

    ctx = {}
    with pytest.raises(TestError):
        await d.on_external_url_play(context=ctx)
    # Marker should be True even though init failed
    assert ctx.get("_device_queue_session_initialized") is True
    # q/c did increment (before the await that raised)
    assert d.get_runtime_state().queue_session_id == 1

    # Retry with same context -should return immediately, not re-increment q or c
    d.cancel_group_next_timer = lambda: asyncio.sleep(0)
    await d.on_external_url_play(context=ctx)
    s = d.get_runtime_state()
    assert s.queue_session_id == 1
    assert s.command_generation == 1


@pytest.mark.asyncio
async def test_mina_transport_preserves_context_identity():
    """MinaTransport.play_url passes same context dict object to xiaomusic.play_url."""
    from xiaomusic.adapters.mina import MinaTransport
    from xiaomusic.core.models.media import PreparedStream

    contexts_received = []
    class _XM:
        async def play_url(self, did="", arg1="", **kwargs):
            contexts_received.append(kwargs.get("context"))
            return {"code": 0}

    t = MinaTransport(_XM())
    ctx = {"shuffle": True}
    p = PreparedStream(final_url="http://x/a.mp3")

    await t.play_url("d1", p, ctx)
    assert contexts_received[0] is ctx  # same object identity

    # Also test: empty dict -preserves identity
    ctx2 = {}
    await t.play_url("d1", p, ctx2)
    assert contexts_received[1] is ctx2

    # None -creates new dict
    await t.play_url("d1", p, None)
    assert isinstance(contexts_received[2], dict)
    assert contexts_received[2] is not contexts_received[0]
async def _noop_play_started(context=None, resolved=None, *, token):
    pass


async def _noop_coro():
    pass

# ── external play_url track attempt (T04-C2c arbiter-based) ────────

@pytest.mark.asyncio
async def test_play_url_single_success_attempt_one():
    """single play_url via arbiter: q=1,c=1,a=1, dispatch once.
    Wait for arbiter to complete, then assert final state."""
    from xiaomusic.xiaomusic import XiaoMusic

    xm2 = _fake_xm()
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None
    d._bootstrap_playlist_session_for_external_url = lambda context: None

    dispatches = []
    dispatch_done = asyncio.Event()
    attempt_seen = 0

    async def _spy_group(url):
        nonlocal attempt_seen
        dispatches.append(url)
        attempt_seen = d.get_runtime_state().track_attempt_id
        dispatch_done.set()
        return [{"code": 0}]

    d.group_player_play = _spy_group

    xm = XiaoMusic.__new__(XiaoMusic)
    xm.log = logging.getLogger("test")
    xm.device_manager = types.SimpleNamespace(devices={"d1": d})

    try:
        receipt = await XiaoMusic.play_url(xm, did="d1", arg1="http://x/A.mp3")
        assert receipt["accepted"] is True

        await asyncio.wait_for(dispatch_done.wait(), timeout=5.0)

        s = d.get_runtime_state()
        assert s.queue_session_id == 1
        assert s.command_generation == 1
        assert s.track_attempt_id == 1
        assert attempt_seen == 1
        assert len(dispatches) == 1
    finally:
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_play_url_same_context_two_calls_attempt_two():
    """same context x2 rapid submit: latest-wins, only one dispatch.
    c=2 (both accepted), q=1, a=1 (one physical dispatch)."""
    from xiaomusic.xiaomusic import XiaoMusic

    xm2 = _fake_xm()
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None
    d._bootstrap_playlist_session_for_external_url = lambda context: None

    dispatches = []
    dispatch_done = asyncio.Event()

    async def _spy_group(url):
        dispatches.append(url)
        dispatch_done.set()
        return [{"code": 0}]

    d.group_player_play = _spy_group

    xm = XiaoMusic.__new__(XiaoMusic)
    xm.log = logging.getLogger("test")
    xm.device_manager = types.SimpleNamespace(devices={"d1": d})

    try:
        r1 = await XiaoMusic.play_url(xm, did="d1", arg1="http://x/a.mp3", context={})
        assert r1["accepted"] is True
        r2 = await XiaoMusic.play_url(xm, did="d1", arg1="http://x/b.mp3", context={})
        assert r2["accepted"] is True

        await asyncio.wait_for(dispatch_done.wait(), timeout=5.0)

        s = d.get_runtime_state()
        # c bumped twice (both accepted), q+1 and a+1 once (latest-wins dispatch)
        assert s.command_generation == 2
        assert s.queue_session_id == 1
        assert s.track_attempt_id == 1
        assert len(dispatches) == 1
        assert dispatches == ["http://x/b.mp3"]
    finally:
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_play_url_init_exception_attempt_zero():
    """on_external_url_play raises → arbiter.last_error set, a=0, group never called.
    Exception does NOT propagate to caller (play_url returns receipt immediately)."""
    from xiaomusic.xiaomusic import XiaoMusic

    class TestError(Exception):
        pass

    xm2 = _fake_xm()
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None

    async def _raise_init(context=None, *, command_already_accepted=False, manual_already_invalidated=False):
        nonlocal d
        # Bump q before raising so we can observe the attempt stayed 0
        raise TestError("init fail")
    d.on_external_url_play = _raise_init

    dispatches = []
    d.group_player_play = lambda url: dispatches.append(url) or [None]

    xm = XiaoMusic.__new__(XiaoMusic)
    xm.log = logging.getLogger("test")
    xm.device_manager = types.SimpleNamespace(devices={"d1": d})

    executor_done = asyncio.Event()

    try:
        receipt = await XiaoMusic.play_url(xm, did="d1", arg1="http://x/A.mp3")
        # play_url returns receipt immediately, does NOT raise
        assert receipt["accepted"] is True

        # Wait for executor to finish (will raise internally)
        # The executor is invoked by the arbiter; wrap it to signal done
        _orig_exec = d._execute_external_play_intent
        async def _spy_exec(payload):
            try:
                await _orig_exec(payload)
            finally:
                executor_done.set()
        d._execute_external_play_intent = _spy_exec

        await asyncio.wait_for(executor_done.wait(), timeout=5.0)

        arb = d._command_arbiter
        assert arb.last_error is not None
        assert isinstance(arb.last_error, TestError)
        assert d.get_runtime_state().track_attempt_id == 0
        assert len(dispatches) == 0
    finally:
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_play_url_group_exception_preserves_attempt():
    """group raises after init success → last_error, a=1, q=1, c=1."""
    from xiaomusic.xiaomusic import XiaoMusic

    class TestError(Exception):
        pass

    xm2 = _fake_xm()
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None
    d._bootstrap_playlist_session_for_external_url = lambda context: None

    async def _raise_group(url):
        raise TestError("group fail")
    d.group_player_play = _raise_group

    xm = XiaoMusic.__new__(XiaoMusic)
    xm.log = logging.getLogger("test")
    xm.device_manager = types.SimpleNamespace(devices={"d1": d})

    try:
        receipt = await XiaoMusic.play_url(xm, did="d1", arg1="http://x/A.mp3")
        assert receipt["accepted"] is True

        # Wrap executor to signal when done
        executor_done_ge = asyncio.Event()
        _orig_exec_ge = d._execute_external_play_intent
        async def _spy_exec_ge(payload):
            try:
                await _orig_exec_ge(payload)
            finally:
                executor_done_ge.set()
        d._execute_external_play_intent = _spy_exec_ge

        await asyncio.wait_for(executor_done_ge.wait(), timeout=5.0)

        arb = d._command_arbiter
        assert arb.last_error is not None
        assert isinstance(arb.last_error, TestError)

        s = d.get_runtime_state()
        assert s.track_attempt_id == 1
        assert s.queue_session_id == 1
        assert s.command_generation == 1
    finally:
        await d.close_command_arbiter()

# ── proxy fallback track attempt ───────────────────────────────────────

@pytest.mark.asyncio
async def test_proxy_fallback_success_bumps_attempt():
    """_try_proxy_fallback success: attempt +1."""
    xm2 = _fake_xm()
    xm2.music_library.get_proxy_url = lambda origin_url, name="": "http://proxy/" + name
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = lambda: asyncio.sleep(0)
    d._invalidate_manual_navigation = lambda reason: None
    async def _group_success(url, name=""):
        return [{"code": 0}]
    d.group_player_play = _group_success
    a_before = d.get_runtime_state().track_attempt_id
    sid = d._play_session_id
    url = await d._try_proxy_fallback(name="s", sid=sid, origin_url="http://x/a.mp3",
                                       fast_stop=True, reason="t", verify_started=False)
    assert url == "http://proxy/s"
    assert d.get_runtime_state().track_attempt_id == a_before + 1


@pytest.mark.asyncio
async def test_proxy_fallback_empty_url_no_attempt():
    """proxy URL empty -no stop, no attempt, no dispatch."""
    xm2 = _fake_xm()
    xm2.music_library.get_proxy_url = lambda origin_url, name="": ""
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = lambda: asyncio.sleep(0)
    d._invalidate_manual_navigation = lambda reason: None
    dispatches = []
    async def _spy_group(url, name=""):
        dispatches.append(url)
        return [{"code": 0}]
    d.group_player_play = _spy_group
    a_before = d.get_runtime_state().track_attempt_id
    url = await d._try_proxy_fallback(name="s", sid=d._play_session_id, origin_url="http://x/a.mp3",
                                       fast_stop=True, reason="t", verify_started=False)
    assert url == ""
    assert d.get_runtime_state().track_attempt_id == a_before
    assert len(dispatches) == 0


@pytest.mark.asyncio
async def test_proxy_fallback_sid_stale_no_attempt():
    """sid stale -no stop, no attempt, no dispatch."""
    xm2 = _fake_xm()
    xm2.music_library.get_proxy_url = lambda origin_url, name="": "http://proxy/" + name
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = lambda: asyncio.sleep(0)
    d._invalidate_manual_navigation = lambda reason: None
    dispatches = []
    async def _spy_group(url, name=""):
        dispatches.append(url)
        return [{"code": 0}]
    d.group_player_play = _spy_group
    a_before = d.get_runtime_state().track_attempt_id
    url = await d._try_proxy_fallback(name="s", sid=999, origin_url="http://x/a.mp3",
                                       fast_stop=True, reason="t", verify_started=False)
    assert url == ""
    assert d.get_runtime_state().track_attempt_id == a_before
    assert len(dispatches) == 0


@pytest.mark.asyncio
async def test_proxy_fallback_stop_failure_no_attempt():
    """stop fails -no attempt, no dispatch."""
    class TestError(Exception):
        pass
    xm2 = _fake_xm()
    xm2.music_library.get_proxy_url = lambda origin_url, name="": "http://proxy/" + name
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = lambda: asyncio.sleep(0)
    d._invalidate_manual_navigation = lambda reason: None
    async def _raise_stop(fast=False):
        raise TestError("stop fail")
    d.group_force_stop_xiaoai = _raise_stop
    a_before = d.get_runtime_state().track_attempt_id
    url = await d._try_proxy_fallback(name="s", sid=d._play_session_id, origin_url="http://x/a.mp3",
                                       fast_stop=True, reason="t", verify_started=False)
    assert url == ""
    assert d.get_runtime_state().track_attempt_id == a_before


@pytest.mark.asyncio
async def test_proxy_fallback_group_exception_attempt_retained():
    """group raises -attempt already bumped and retained."""
    class TestError(Exception):
        pass
    xm2 = _fake_xm()
    xm2.music_library.get_proxy_url = lambda origin_url, name="": "http://proxy/" + name
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = lambda: asyncio.sleep(0)
    d._invalidate_manual_navigation = lambda reason: None
    async def _raise_group(url, name=""):
        raise TestError("group fail")
    d.group_player_play = _raise_group
    sid = d._play_session_id
    result = await d._try_proxy_fallback(
        name="s",
        sid=sid,
        origin_url="http://x/a.mp3",
        fast_stop=True,
        reason="t",
        verify_started=False,
    )
    assert result == ""
    assert d.get_runtime_state().track_attempt_id == 1


@pytest.mark.asyncio
async def test_playmusic_direct_fail_proxy_success_attempt_two():
    """_playmusic direct dispatch all-None -_try_proxy_fallback -attempt=2."""
    xm2 = _fake_xm()
    xm2.music_library.get_proxy_url = lambda origin_url, name="": "http://proxy/" + name
    xm2.music_library.get_music_url = lambda name: asyncio.sleep(0, result=("http://x/" + name, "http://x/" + name))
    xm2.music_library.get_music_duration = lambda name: asyncio.sleep(0, result=10.0)
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = lambda: asyncio.sleep(0)
    d._invalidate_manual_navigation = lambda reason: None
    d._mark_play_started = lambda **kw: asyncio.sleep(0, result=None)
    d._schedule_playback_confirmation = lambda **kw: None
    d._start_duration_probe = lambda name, sid, **kw: None
    d.auto_add_song = lambda cur, sec: asyncio.sleep(0, result=None)
    d.set_next_music_timeout = lambda sec, token=None: _noop
    d._refresh_runtime_volume = lambda **kw: asyncio.sleep(0, result=0)
    d._is_jellyfin_auto_candidate = lambda **kw: True
    d._handle_play_failure = lambda **kw: asyncio.sleep(0, result=None)

    dispatches = []
    async def _spy_group(url, name=""):
        dispatches.append(url)
        if len(dispatches) == 1:
            return [None]
        return [{"code": 0}]
    d.group_player_play = _spy_group

    q_before = d.get_runtime_state().queue_session_id
    c_before = d.get_runtime_state().command_generation

    await d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)

    s = d.get_runtime_state()
    assert s.track_attempt_id == 2
    assert s.queue_session_id == q_before
    assert s.command_generation == c_before
    assert dispatches == ["http://x/song1", "http://proxy/song1"]
# ── lifecycle stale guard in _playmusic ────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt"])
async def test_playmusic_stale_after_lifecycle_mutation(mutation_fn):
    """group blocked; lifecycle mutation -stale -returns False, no post-effects."""
    entered = asyncio.Event()
    release = asyncio.Event()

    xm2 = _fake_xm()
    xm2.music_library.get_music_url = lambda name: asyncio.sleep(0, result=("http://x/" + name, "http://x/" + name))
    xm2.music_library.get_music_duration = lambda name: asyncio.sleep(0, result=10.0)
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop
    d._invalidate_manual_navigation = lambda reason: None
    d._is_jellyfin_auto_candidate = lambda **kw: False

    call_counts = {"mark": 0, "schedule": 0, "fallback": 0, "failure": 0}

    async def _mark(**kw):
        call_counts["mark"] += 1

    def _schedule(**kw):
        call_counts["schedule"] += 1

    async def _fallback(**kw):
        call_counts["fallback"] += 1

    async def _failure(**kw):
        call_counts["failure"] += 1

    d._mark_play_started = _mark
    d._schedule_playback_confirmation = _schedule
    d._try_proxy_fallback = _fallback
    d._handle_play_failure = _failure

    async def _block_group(url, name=""):
        entered.set()
        await release.wait()
        return [{"code": 0}]
    d.group_player_play = _block_group

    task = asyncio.create_task(d._playmusic("song1", confirm_start_in_background=True, fast_stop=True))
    try:
        await entered.wait()
        failure_before = d.get_runtime_state().failure

        # Apply mutation while group is blocked
        if mutation_fn == "command":
            d._accept_command(updated_at=999.0)
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=999.0)
        elif mutation_fn == "attempt":
            d._start_track_attempt(updated_at=999.0)

        release.set()
        result = await task

        assert result is False
        assert call_counts == {"mark": 0, "schedule": 0, "fallback": 0, "failure": 0}
        assert d.get_runtime_state().phase != PlaybackPhase.FAILED
        assert d.get_runtime_state().failure == failure_before
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass




@pytest.mark.asyncio
async def test_playmusic_current_token_normal_path():
    """No mutation -stale guard passes -mark=1, schedule=1, fallback=0, failure=0."""
    entered = asyncio.Event()
    release = asyncio.Event()

    xm2 = _fake_xm()
    xm2.music_library.get_music_url = lambda name: asyncio.sleep(0, result=("http://x/" + name, "http://x/" + name))
    xm2.music_library.get_music_duration = lambda name: asyncio.sleep(0, result=10.0)
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop
    d._invalidate_manual_navigation = lambda reason: None
    d._is_jellyfin_auto_candidate = lambda **kw: False

    call_counts = {"mark": 0, "schedule": 0, "fallback": 0, "failure": 0}

    async def _mark(**kw):
        call_counts["mark"] += 1

    def _schedule(**kw):
        call_counts["schedule"] += 1

    async def _fallback(**kw):
        call_counts["fallback"] += 1

    async def _failure(**kw):
        call_counts["failure"] += 1

    d._mark_play_started = _mark
    d._schedule_playback_confirmation = _schedule
    d._try_proxy_fallback = _fallback
    d._handle_play_failure = _failure
    d.set_next_music_timeout = _noop
    d._start_duration_probe = lambda name, sid, **kw: None
    d.auto_add_song = lambda cur, sec: _noop

    async def _block_group(url, name=""):
        entered.set()
        await release.wait()
        return [{"code": 0}]
    d.group_player_play = _block_group

    task = asyncio.create_task(d._playmusic("song1", confirm_start_in_background=True, fast_stop=True))
    try:
        await entered.wait()
        release.set()
        result = await task

        assert result is True
        assert call_counts["mark"] == 1
        assert call_counts["schedule"] == 1
        assert call_counts["fallback"] == 0
        assert call_counts["failure"] == 0
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

# ── _playmusic begin_play_request phase tests ───────────────────────


def _build_blocking_playmusic_device():
    """Build device where get_music_url blocks until test releases it.

    Returns (device, entered_event, release_event).  The test must set
    release_event and cancel the playmusic task in its finally block.
    """
    d = _build_playmusic_attempt_device()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _blocking_url(name):
        entered.set()
        await release.wait()
        return "http://x/song1.mp3", "http://x/song1.mp3"

    d.xiaomusic.music_library.get_music_url = _blocking_url
    return d, entered, release


@pytest.mark.asyncio
async def test_playmusic_phase_idle_enters_url_await_as_resolving():
    """IDLE: enters get_music_url await with RESOLVING, desired fields correct,
    attempt unchanged."""
    d, entered, release = _build_blocking_playmusic_device()
    assert d.get_runtime_state().phase == PlaybackPhase.IDLE
    a_before = d.get_runtime_state().track_attempt_id

    task = asyncio.create_task(
        d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)
    )
    try:
        await entered.wait()  # _playmusic reached get_music_url

        s = d.get_runtime_state()
        assert s.phase == PlaybackPhase.RESOLVING, (
            f"Expected RESOLVING, got {s.phase}"
        )
        assert s.desired_track is not None
        assert s.desired_track.source == "legacy"
        assert s.desired_track.display_name == str(d.get_cur_music() or "")
        assert s.track_attempt_id == a_before
    finally:
        release.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_playmusic_phase_playing_enters_url_await_as_switching():
    """Pre-set PLAYING: enters URL await with SWITCHING, confirmed preserved,
    desired=new, attempt unchanged."""
    d, entered, release = _build_blocking_playmusic_device()

    old_confirmed = TrackReference(
        entity_id="old_e", display_name="old", source="legacy"
    )
    playing_state = PlaybackRuntimeState(
        phase=PlaybackPhase.PLAYING,
        confirmed_track=old_confirmed,
        desired_track=old_confirmed,
        updated_at=1.0,
    )
    d._set_runtime_state(playing_state)

    a_before = d.get_runtime_state().track_attempt_id

    task = asyncio.create_task(
        d._playmusic("new_song", confirm_start_in_background=True, fast_stop=True)
    )
    try:
        await entered.wait()

        s = d.get_runtime_state()
        assert s.phase == PlaybackPhase.SWITCHING, (
            f"Expected SWITCHING, got {s.phase}"
        )
        assert s.desired_track is not None
        assert s.desired_track.source == "legacy"
        assert s.confirmed_track is old_confirmed
        assert s.desired_track is not old_confirmed
        assert s.track_attempt_id == a_before
    finally:
        release.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_playmusic_phase_dispatching_enters_url_await_as_resolving():
    """Pre-set DISPATCHING: enters URL await with RESOLVING (latest-wins),
    attempt unchanged."""
    d, entered, release = _build_blocking_playmusic_device()

    dispatching_state = PlaybackRuntimeState(
        phase=PlaybackPhase.DISPATCHING,
        desired_track=TrackReference(
            entity_id="old_e", display_name="old", source="legacy"
        ),
    )
    d._set_runtime_state(dispatching_state)

    a_before = d.get_runtime_state().track_attempt_id

    task = asyncio.create_task(
        d._playmusic("new_song", confirm_start_in_background=True, fast_stop=True)
    )
    try:
        await entered.wait()

        s = d.get_runtime_state()
        assert s.phase == PlaybackPhase.RESOLVING, (
            f"Expected RESOLVING, got {s.phase}"
        )
        assert s.desired_track is not None
        assert s.desired_track.source == "legacy"
        assert s.track_attempt_id == a_before
    finally:
        release.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_playmusic_phase_stopping_returns_false_no_side_effects():
    """Pre-set STOPPING: _playmusic returns False, no side effects at all.

    Device playback state, session, timer, and legacy track must be
    completely untouched.  Entity resolver may be called (sync, no I/O).
    """
    d = _build_playmusic_attempt_device()

    stopping_state = PlaybackRuntimeState(
        phase=PlaybackPhase.STOPPING,
        desired_track=TrackReference(
            entity_id="e1", display_name="old", source="legacy"
        ),
    )
    d._set_runtime_state(stopping_state)

    # ── snapshot before call ──────────────────────────────────────
    sid_before = d._play_session_id
    is_playing_before = d.is_playing
    cur_music_before = d.device.cur_music
    display_name_before = getattr(d.device, "current_display_name", None)
    entity_id_before = getattr(d.device, "current_entity_id", None)
    playlist_item_id_before = getattr(d.device, "current_playlist_item_id", None)
    index_before = d._current_index
    playlist2music_before = d.device.playlist2music
    state_obj_before = d.get_runtime_state()
    a_before = state_obj_before.track_attempt_id

    # ── spies ─────────────────────────────────────────────────────
    url_calls = 0
    stop_calls = 0
    dispatch_calls = 0
    cancel_group_timer_calls = 0

    async def _spy_url(name):
        nonlocal url_calls
        url_calls += 1
        return "http://x/u", "http://x/u"

    async def _spy_cancel_group_next_timer():
        nonlocal cancel_group_timer_calls
        cancel_group_timer_calls += 1

    async def _spy_stop(*, fast_stop=False, sid=0):
        nonlocal stop_calls
        stop_calls += 1
        return None

    async def _spy_dispatch(url, name=""):
        nonlocal dispatch_calls
        dispatch_calls += 1
        return [{"code": 0}]

    d.xiaomusic.music_library.get_music_url = _spy_url
    d.cancel_group_next_timer = _spy_cancel_group_next_timer
    d._execute_group_stop = _spy_stop
    d.group_player_play = _spy_dispatch

    result = await d._playmusic(
        "song1", confirm_start_in_background=True, fast_stop=True
    )

    # ── no side effects whatsoever ────────────────────────────────
    assert result is False
    assert url_calls == 0
    assert stop_calls == 0
    assert dispatch_calls == 0
    assert cancel_group_timer_calls == 0

    # ── playback / session identity unchanged ─────────────────────
    assert d._play_session_id == sid_before
    assert d.is_playing == is_playing_before
    assert d.device.cur_music == cur_music_before
    assert getattr(d.device, "current_display_name", None) == display_name_before
    assert getattr(d.device, "current_entity_id", None) == entity_id_before
    assert (
        getattr(d.device, "current_playlist_item_id", None)
        == playlist_item_id_before
    )
    assert d._current_index == index_before
    assert d.device.playlist2music is playlist2music_before
    assert d.get_runtime_state() is state_obj_before
    assert d.get_runtime_state().track_attempt_id == a_before
    assert d.get_runtime_state().phase == PlaybackPhase.STOPPING


# ── external play_url lifecycle stale guard (T04-C2c arbiter-based) ───

@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt"])
async def test_play_url_stale_after_lifecycle_mutation(mutation_fn):
    """group blocked; mutation -stale -started callback NOT called.
    Uses submit_external_url_play + arbiter."""
    import asyncio

    entered = asyncio.Event()
    release = asyncio.Event()
    started_called = False

    xm2 = _fake_xm()
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None
    d._bootstrap_playlist_session_for_external_url = lambda context: None

    async def _block_group(url):
        entered.set()
        await release.wait()
        return [{"code": 0}]
    d.group_player_play = _block_group

    async def _on_started(context=None, resolved=None, *, token):
        nonlocal started_called
        started_called = True
    d.on_external_url_play_started = _on_started

    executor_done_stale = asyncio.Event()

    try:
        # Wrap executor to signal when done
        _orig_exec_stale = d._execute_external_play_intent
        async def _spy_exec_stale(payload):
            try:
                await _orig_exec_stale(payload)
            finally:
                executor_done_stale.set()
        d._execute_external_play_intent = _spy_exec_stale

        receipt = await d.submit_external_url_play(url="http://x/a.mp3")
        assert receipt["accepted"] is True

        await asyncio.wait_for(entered.wait(), timeout=5.0)

        if mutation_fn == "command":
            d._accept_command(updated_at=999.0)
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=999.0)
        elif mutation_fn == "attempt":
            d._start_track_attempt(updated_at=999.0)

        release.set()
        await asyncio.wait_for(executor_done_stale.wait(), timeout=5.0)

        assert not started_called
    finally:
        release.set()
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_play_url_current_token_normal_path():
    """No mutation -started callback called once.
    Uses submit_external_url_play + arbiter."""
    import asyncio

    entered = asyncio.Event()
    release = asyncio.Event()
    started_called = False
    started_done_normal = asyncio.Event()

    xm2 = _fake_xm()
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None
    d._bootstrap_playlist_session_for_external_url = lambda context: None

    async def _block_group(url):
        entered.set()
        await release.wait()
        return [{"code": 0}]
    d.group_player_play = _block_group

    async def _on_started(context=None, resolved=None, *, token):
        nonlocal started_called
        started_called = True
        started_done_normal.set()
    d.on_external_url_play_started = _on_started

    try:
        receipt = await d.submit_external_url_play(url="http://x/a.mp3")
        assert receipt["accepted"] is True

        await asyncio.wait_for(entered.wait(), timeout=5.0)
        release.set()
        await asyncio.wait_for(started_done_normal.wait(), timeout=5.0)

        assert started_called
    finally:
        release.set()
        await d.close_command_arbiter()

# ── bg confirm lifecycle stale guard ───────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt"])
async def _legacy_bg_confirm_stale_after_lifecycle_mutation(mutation_fn):
    """Superseded by the assertion-complete lifecycle test below."""
    import asyncio

    from xiaomusic.device_player import XiaoMusicDevice

    entered = asyncio.Event()
    release = asyncio.Event()

    d = _make_device_via_new()
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "", "entity_id": ""},
        {"display_name": "B", "legacy_name": "B", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
        device_manager=types.SimpleNamespace(
            get_group_device_id_list=lambda g: [],
            get_group_devices=lambda g: {},
        ),
    )
    d._play_next = XiaoMusicDevice._play_next.__get__(d, XiaoMusicDevice)
    d._is_jellyfin_auto_candidate = lambda **kw: False
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._play_session_id = 1

    async def _block_confirm(name, sid, **kw):
        entered.set()
        await release.wait()
        return True
    d._confirm_playback_started = _block_confirm

    task = asyncio.create_task(
        d._background_confirm_playback_started(
            name="B", sid=1, cur_playlist="BGM",
            origin_url="http://x/A.mp3", current_url="http://x/A.mp3",
            fast_stop=False, token=token,
        )
    )
    try:
        await entered.wait()
        if mutation_fn == "command":
            d._accept_command(updated_at=999.0)
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=999.0)
        elif mutation_fn == "attempt":
            d._start_track_attempt(updated_at=999.0)
        release.set()
        await task
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def _legacy_bg_confirm_current_token_calls_mark():
    """Superseded by the assertion-complete current-token test below."""
    import asyncio

    from xiaomusic.device_player import XiaoMusicDevice

    entered = asyncio.Event()
    release = asyncio.Event()

    d = _make_device_via_new()
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "", "entity_id": ""},
        {"display_name": "B", "legacy_name": "B", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
        device_manager=types.SimpleNamespace(
            get_group_device_id_list=lambda g: [],
            get_group_devices=lambda g: {},
        ),
    )
    d._play_next = XiaoMusicDevice._play_next.__get__(d, XiaoMusicDevice)
    d._is_jellyfin_auto_candidate = lambda **kw: False
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._play_session_id = 1

    mark_called = 0
    async def _mark(**kw):
        nonlocal mark_called
        mark_called += 1
    d._mark_play_started = _mark

    async def _block_confirm(name, sid, **kw):
        entered.set()
        await release.wait()
        return True
    d._confirm_playback_started = _block_confirm
    d._refresh_runtime_volume = _noop

    task = asyncio.create_task(
        d._background_confirm_playback_started(
            name="B", sid=1, cur_playlist="BGM",
            origin_url="http://x/A.mp3", current_url="http://x/A.mp3",
            fast_stop=False, token=token,
        )
    )
    try:
        await entered.wait()
        release.set()
        await task
        # started=True path -no extra mark (already handled by _playmusic sync path)
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

# ── bg confirm lifecycle stale guard ───────────────────────────────────
# ── schedule token propagation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_schedule_passes_token_to_background():
    """Schedule freezes and passes a concrete lifecycle token."""
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._start_track_attempt(updated_at=1.0)

    received_token = None

    async def _fake_bg(name, sid, cur_playlist, origin_url, current_url, fast_stop, token=None):
        nonlocal received_token
        received_token = token
        # short-circuit to avoid background work
        return

    d._background_confirm_playback_started = _fake_bg
    d._playback_confirm_task = None

    expected_token = d._capture_lifecycle_token()
    d._schedule_playback_confirmation(
        name="x",
        sid=1,
        cur_playlist="BGM",
        origin_url="http://x/a.mp3",
        current_url="http://x/a.mp3",
        fast_stop=False,
    )
    d._accept_command(updated_at=2.0)
    # The runner must receive the pre-mutation token captured by schedule.
    task = d._playback_confirm_task
    assert task is not None
    await task
    assert isinstance(received_token, LifecycleToken)
    assert received_token == expected_token


# ── mutation during confirm probe ──────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt"])
async def test_bg_confirm_stale_after_lifecycle_mutation(mutation_fn):
    """Mutation during initial confirmation suppresses every post-effect."""
    entered = asyncio.Event()
    release = asyncio.Event()

    d = _make_device_via_new()
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "", "entity_id": ""},
        {"display_name": "B", "legacy_name": "B", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
        device_manager=types.SimpleNamespace(get_group_device_id_list=lambda g: [], get_group_devices=lambda g: {}),
    )
    d._play_next = XiaoMusicDevice._play_next.__get__(d, XiaoMusicDevice)
    d._is_jellyfin_auto_candidate = lambda **kw: False
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._play_session_id = 1

    counters = {"play_next": 0, "fallback": 0, "mark": 0}

    async def _play_next():
        counters["play_next"] += 1

    async def _mark(**kw):
        counters["mark"] += 1

    d._play_next = _play_next
    d._mark_play_started = _mark
    d._bg_confirm_false_count = 7
    timer_sentinel = object()
    d._next_timer = timer_sentinel

    async def _block_confirm(name, sid, **kw):
        entered.set()
        await release.wait()
        return True
    d._confirm_playback_started = _block_confirm

    async def _mock_fallback(**kw):
        counters["fallback"] += 1
        return ""

    d._try_proxy_fallback = _mock_fallback

    task = asyncio.create_task(
        d._background_confirm_playback_started(
            name="B", sid=1, cur_playlist="BGM",
            origin_url="http://x/A.mp3", current_url="http://x/A.mp3",
            fast_stop=False, token=token,
        )
    )
    try:
        await entered.wait()
        if mutation_fn == "command":
            d._accept_command(updated_at=999.0)
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=999.0)
        elif mutation_fn == "attempt":
            d._start_track_attempt(updated_at=999.0)
        release.set()
        await task
        assert counters == {"play_next": 0, "fallback": 0, "mark": 0}
        assert d._bg_confirm_false_count == 7
        assert d._next_timer is timer_sentinel
        assert d.get_runtime_state().phase != PlaybackPhase.FAILED
        assert d.get_runtime_state().failure is None
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# ── current token normal path ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_bg_confirm_current_token_proceeds():
    """Current token preserves the normal confirmed path.

    Establishes CONFIRMING phase via real wrappers, then runs bg confirm
    with started=True.  Asserts PLAYING, confirmed_track=desired, counter=0.
    """
    entered = asyncio.Event()
    release = asyncio.Event()

    d = _make_device_via_new()
    d._play_session_id = 1
    d._is_jellyfin_auto_candidate = lambda **kw: False
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)

    # Establish CONFIRMING via real wrappers (not replace)
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="B", source="test"
        ),
        updated_at=2.0,
    )
    d._begin_runtime_play_dispatch(updated_at=3.0)
    d._begin_runtime_confirmation(updated_at=4.0)
    assert d.get_runtime_state().phase == PlaybackPhase.CONFIRMING

    desired = d.get_runtime_state().desired_track
    assert desired is not None

    d._bg_confirm_false_count = 3

    async def _block_confirm(name, sid, **kw):
        entered.set()
        await release.wait()
        return True
    d._confirm_playback_started = _block_confirm

    task = asyncio.create_task(
        d._background_confirm_playback_started(
            name="B", sid=1, cur_playlist="BGM",
            origin_url="http://x/A.mp3", current_url="http://x/A.mp3",
            fast_stop=False, token=token,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=5.0)
        release.set()
        await asyncio.wait_for(task, timeout=5.0)
        s = d.get_runtime_state()
        assert s.phase == PlaybackPhase.PLAYING
        assert s.confirmed_track is not None
        assert s.confirmed_track.display_name == "B"
        assert d._bg_confirm_false_count == 0
        assert s.failure is None
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_bg_confirm_stale_during_status_query_skips_fallback_and_mark():
    """A command accepted during the device-status await silences old work.

    Establishes CONFIRMING via real wrappers, then runs bg confirm with
    started=True + jf candidate=True.  Status query blocks via Event;
    during the block a command is accepted (token stale).  After release
    the stale guard prevents fallback/mark, and phase is not mutated.
    """
    entered = asyncio.Event()
    release = asyncio.Event()
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._is_jellyfin_auto_candidate = lambda **kw: True

    # Establish CONFIRMING via real wrappers (not replace)
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="A", source="test"
        ),
        updated_at=2.0,
    )
    d._begin_runtime_play_dispatch(updated_at=3.0)
    d._begin_runtime_confirmation(updated_at=4.0)
    assert d.get_runtime_state().phase == PlaybackPhase.CONFIRMING

    async def _confirmed(name, sid, **kw):
        return True

    async def _status():
        entered.set()
        await release.wait()
        return False

    calls = {"fallback": 0, "mark": 0}

    async def _fallback(**kw):
        calls["fallback"] += 1
        return "http://proxy/a.mp3"

    async def _mark(**kw):
        calls["mark"] += 1

    d._confirm_playback_started = _confirmed
    d.get_if_xiaoai_is_playing = _status
    d._try_proxy_fallback = _fallback
    d._mark_play_started = _mark

    task = asyncio.create_task(
        d._background_confirm_playback_started(
            name="A",
            sid=1,
            cur_playlist="BGM",
            origin_url="http://x/a.mp3",
            current_url="http://x/a.mp3",
            fast_stop=False,
            token=token,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=5.0)
        d._accept_command(updated_at=999.0)
        release.set()
        await asyncio.wait_for(task, timeout=5.0)
        assert calls == {"fallback": 0, "mark": 0}
        # Phase was legitimately PLAYING (helper ran before token went stale).
        # The stale guard correctly suppressed fallback/mark after the status query.
        s = d.get_runtime_state()
        assert s.phase not in {PlaybackPhase.FAILED}, (
            f"Phase mutated to {s.phase} by stale task"
        )
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_bg_confirm_stale_after_first_false_preserves_timer():
    """T05-A: stale token after first False prevents grace retry and handler.

    Blocks during the first confirm probe; a command is accepted while
    blocked (token stale).  The function exits before grace retry or handler,
    leaving timer and counter unchanged.
    """
    entered = asyncio.Event()
    release = asyncio.Event()

    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._is_jellyfin_auto_candidate = lambda **kw: False
    d._bg_confirm_false_count = 7

    timer_sentinel = asyncio.create_task(asyncio.sleep(999))
    d._next_timer = timer_sentinel

    async def _blocking_false(name, sid, **kw):
        entered.set()
        await release.wait()
        return False

    play_next_calls = 0
    async def _play_next():
        nonlocal play_next_calls
        play_next_calls += 1

    d._confirm_playback_started = _blocking_false
    d._play_next = _play_next

    task = asyncio.create_task(
        d._background_confirm_playback_started(
            name="A",
            sid=1,
            cur_playlist="BGM",
            origin_url="http://x/a.mp3",
            current_url="http://x/a.mp3",
            fast_stop=False,
            token=token,
        )
    )
    try:
        await entered.wait()
        d._accept_command(updated_at=2.0)
        release.set()
        await task
        assert play_next_calls == 0
        assert d._bg_confirm_false_count == 7
        assert d._next_timer is timer_sentinel
        assert not timer_sentinel.done()
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        timer_sentinel.cancel()
        try:
            await timer_sentinel
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_bg_confirm_proxy_fallback_hands_off_to_new_attempt():
    """A legitimate proxy dispatch transfers ownership to exactly attempt+1."""
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._is_jellyfin_auto_candidate = lambda **kw: True
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            get_proxy_url=lambda origin_url, name="": "http://proxy/a.mp3"
        )
    )

    async def _not_started(name, sid, **kw):
        return False

    async def _stop(fast=False):
        return None

    async def _dispatch(url, name=""):
        return [{"code": 0}]

    async def _playing():
        return True

    mark_calls = 0

    async def _mark(**kw):
        nonlocal mark_calls
        mark_calls += 1

    d._confirm_playback_started = _not_started
    d.group_force_stop_xiaoai = _stop
    d.group_player_play = _dispatch
    d.get_if_xiaoai_is_playing = _playing
    d._mark_play_started = _mark

    # Phase must be CONFIRMING (as set by _playmusic before scheduling)
    from xiaomusic.playback.runtime_state import (
        begin_confirm,
        begin_play_dispatch,
        begin_resolve,
    )
    s = d.get_runtime_state()
    s = begin_resolve(
        s,
        desired_track=TrackReference(
            entity_id="e1", display_name="A", source="test"
        ),
        updated_at=1.0,
    )
    s = begin_play_dispatch(s, updated_at=2.0)
    s = begin_confirm(s, updated_at=3.0)
    d._set_runtime_state(s)

    await d._background_confirm_playback_started(
        name="A",
        sid=1,
        cur_playlist="BGM",
        origin_url="http://x/a.mp3",
        current_url="http://x/a.mp3",
        fast_stop=False,
        token=token,
    )

    state = d.get_runtime_state()
    assert state.queue_session_id == token.queue_session_id
    assert state.command_generation == token.command_generation
    assert state.track_attempt_id == token.track_attempt_id + 1
    assert mark_calls == 1
    assert d._bg_confirm_false_count == 0


@pytest.mark.asyncio
async def test_next_timer_freezes_token_before_runner_starts():
    """Mutation after scheduling makes the not-yet-started timer inert."""
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    d._start_track_attempt(updated_at=1.0)
    status_calls = 0
    play_next_calls = 0

    async def _status():
        nonlocal status_calls
        status_calls += 1
        return False

    async def _play_next():
        nonlocal play_next_calls
        play_next_calls += 1

    d.get_if_xiaoai_is_playing = _status
    d._play_next = _play_next
    false_before = d._timer_expiry_false_count
    await d.set_next_music_timeout(0)
    timer = d._next_timer
    assert timer is not None
    d._accept_command(updated_at=2.0)
    await timer

    assert status_calls == 0
    assert play_next_calls == 0
    assert d._timer_expiry_false_count == false_before


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["command", "queue", "attempt"])
async def test_next_timer_stale_during_cancel_does_not_create_timer_or_change_counters(
    mutation,
):
    """Lifecycle changes while cancelling prevent a stale replacement timer."""
    entered = asyncio.Event()
    release = asyncio.Event()
    d = _make_device_via_new()
    real_cancel_next_timer = d.cancel_next_timer

    async def _blocking_cancel_next_timer():
        entered.set()
        await release.wait()
        await real_cancel_next_timer()

    d.cancel_next_timer = _blocking_cancel_next_timer
    counters_before = (
        d._timer_expiry_false_count,
        d._timer_expiry_playing_grace_count,
        d._timer_expiry_unknown_grace_count,
    )
    call_task = asyncio.create_task(d.set_next_music_timeout(60))
    try:
        await entered.wait()
        if mutation == "command":
            d._accept_command(updated_at=1.0)
        elif mutation == "queue":
            d._start_queue_session(updated_at=1.0)
        else:
            d._start_track_attempt(updated_at=1.0)
        release.set()
        await call_task

        assert d._next_timer is None
        assert (
            d._timer_expiry_false_count,
            d._timer_expiry_playing_grace_count,
            d._timer_expiry_unknown_grace_count,
        ) == counters_before
    finally:
        release.set()
        if not call_task.done():
            call_task.cancel()
            try:
                await call_task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_next_timer_current_after_cancel_creates_timer():
    """Without lifecycle mutation, cancelling is followed by normal scheduling."""
    entered = asyncio.Event()
    release = asyncio.Event()
    d = _make_device_via_new()
    real_cancel_next_timer = d.cancel_next_timer

    async def _blocking_cancel_next_timer():
        entered.set()
        await release.wait()
        await real_cancel_next_timer()

    d.cancel_next_timer = _blocking_cancel_next_timer
    call_task = asyncio.create_task(d.set_next_music_timeout(60))
    timer = None
    try:
        await entered.wait()
        release.set()
        await call_task

        timer = d._next_timer
        assert timer is not None
        assert not timer.done()
    finally:
        release.set()
        if not call_task.done():
            call_task.cancel()
            try:
                await call_task
            except asyncio.CancelledError:
                pass
        d.cancel_next_timer = real_cancel_next_timer
        await d.cancel_next_timer()
        if timer is not None:
            assert timer.cancelled()
        assert d._next_timer is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt"])
async def test_next_timer_stale_during_status_query_has_no_post_effects(mutation_fn):
    """Every lifecycle dimension invalidates a blocked timer status result."""
    entered = asyncio.Event()
    release = asyncio.Event()
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    d._start_track_attempt(updated_at=1.0)
    play_next_calls = 0

    async def _status():
        entered.set()
        await release.wait()
        return False

    async def _play_next():
        nonlocal play_next_calls
        play_next_calls += 1

    d.get_if_xiaoai_is_playing = _status
    d._play_next = _play_next
    counters_before = (
        d._timer_expiry_false_count,
        d._timer_expiry_playing_grace_count,
        d._timer_expiry_unknown_grace_count,
    )
    await d.set_next_music_timeout(0)
    old_timer = d._next_timer
    assert old_timer is not None
    try:
        await entered.wait()
        if mutation_fn == "command":
            d._accept_command(updated_at=2.0)
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=2.0)
        else:
            d._start_track_attempt(updated_at=2.0)
        newer_timer = object()
        d._next_timer = newer_timer
        release.set()
        await old_timer

        assert play_next_calls == 0
        assert d._next_timer is newer_timer
        assert (
            d._timer_expiry_false_count,
            d._timer_expiry_playing_grace_count,
            d._timer_expiry_unknown_grace_count,
        ) == counters_before
    finally:
        if not old_timer.done():
            release.set()
            old_timer.cancel()
            try:
                await old_timer
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_duration_probe_freezes_token_before_runner_starts(monkeypatch):
    """A lifecycle mutation before probe wake prevents cloud status access."""
    original_sleep = asyncio.sleep

    async def _yield_once(delay):
        await original_sleep(0)

    monkeypatch.setattr(dp.asyncio, "sleep", _yield_once)
    d = _make_device_via_new()
    d._duration_probe_task = None
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    d._start_track_attempt(updated_at=1.0)
    status_calls = 0

    async def _status():
        nonlocal status_calls
        status_calls += 1
        return {"duration": 30}

    d.get_player_status = _status
    d._start_duration_probe("A", 1)
    task = d._duration_probe_task
    assert task is not None
    d._accept_command(updated_at=2.0)
    await task

    assert status_calls == 0
    assert d._duration == 0
    assert d._duration_probe_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt"])
async def test_duration_probe_stale_status_cannot_write_or_restore_timer(
    monkeypatch, mutation_fn
):
    """A stale cloud response cannot overwrite duration or create a timer."""
    original_sleep = asyncio.sleep

    async def _yield_once(delay):
        await original_sleep(0)

    monkeypatch.setattr(dp.asyncio, "sleep", _yield_once)
    entered = asyncio.Event()
    release = asyncio.Event()
    d = _make_device_via_new()
    d._duration_probe_task = None
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    d._start_track_attempt(updated_at=1.0)
    d._duration = 9.0
    timer_calls = 0

    async def _status():
        entered.set()
        await release.wait()
        return {"duration": 30}

    async def _set_timer(sec, token=None):
        nonlocal timer_calls
        timer_calls += 1

    d.get_player_status = _status
    d.set_next_music_timeout = _set_timer
    d._start_duration_probe("A", 1)
    task = d._duration_probe_task
    assert task is not None
    try:
        await entered.wait()
        if mutation_fn == "command":
            d._accept_command(updated_at=2.0)
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=2.0)
        else:
            d._start_track_attempt(updated_at=2.0)
        release.set()
        await task

        assert d._duration == 9.0
        assert timer_calls == 0
        assert d._duration_probe_task is None
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt"])
async def test_failure_retry_stale_after_backoff_has_no_side_effects(
    monkeypatch, mutation_fn
):
    """A stale retry cannot query status, replay, or advance after backoff."""
    entered = asyncio.Event()
    release = asyncio.Event()
    created_tasks = []
    original_create_task = asyncio.create_task

    async def _blocked_sleep(delay):
        entered.set()
        await release.wait()

    def _capture_task(coro):
        task = original_create_task(coro)
        created_tasks.append(task)
        return task

    monkeypatch.setattr(dp.asyncio, "sleep", _blocked_sleep)
    monkeypatch.setattr(dp.asyncio, "create_task", _capture_task)
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    d._start_track_attempt(updated_at=1.0)
    # Transition to DISPATCHING ->?a legal phase for _handle_play_failure.
    d._begin_runtime_play_request(
        desired_track=TrackReference(entity_id="e1", display_name="A", source="test"),
        updated_at=1.0,
    )
    d._begin_runtime_play_dispatch(updated_at=1.0)
    # Re-capture token now that phase is legal.
    token = d._capture_lifecycle_token()
    d.is_playing = True
    d._last_cmd = "play"
    d._degraded = False
    d._play_failed_cnt = 0
    d._play_fail_first_ts = 0.0
    d._play_fail_last_reason = ""
    d._degraded_notified = False
    calls = {"status": 0, "play": 0, "next": 0}

    async def _status():
        calls["status"] += 1
        return False

    async def _play(*args, **kwargs):
        calls["play"] += 1

    async def _next():
        calls["next"] += 1

    d.get_if_xiaoai_is_playing = _status
    d._play = _play
    d._play_next = _next

    await d._handle_play_failure(
        name="A", sid=1, reason="test", token=token
    )
    assert len(created_tasks) == 1
    retry_task = created_tasks[0]
    try:
        await entered.wait()
        if mutation_fn == "command":
            d._accept_command(updated_at=2.0)
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=2.0)
        else:
            d._start_track_attempt(updated_at=2.0)
        release.set()
        await retry_task
        assert calls == {"status": 0, "play": 0, "next": 0}
        # Runtime failure was set by the initial _handle_play_failure call
        # (before the retry task was created). The retry task itself had
        # zero side effects.
        assert d.get_runtime_state().failure is not None
        assert d.get_runtime_state().failure.count == 1
    finally:
        if not retry_task.done():
            release.set()
            retry_task.cancel()
            try:
                await retry_task
            except asyncio.CancelledError:
                pass


# ══════════════════════════════════════════════════════════════════════-# _playmusic request_token stale-guard tests
# ══════════════════════════════════════════════════════════════════════-

@pytest.mark.asyncio
async def test_playmusic_request_token_idle_to_dispatching():
    """A-IDLE: _playmusic from IDLE -group_player_play sees DISPATCHING phase, attempt=1."""
    d = _build_playmusic_attempt_device()
    assert d.get_runtime_state().phase == PlaybackPhase.IDLE

    entered = asyncio.Event()
    release = asyncio.Event()
    phase_at_group = None
    attempt_at_group = 0

    async def _block_group(url, name=""):
        nonlocal phase_at_group, attempt_at_group
        phase_at_group = d.get_runtime_state().phase
        attempt_at_group = d.get_runtime_state().track_attempt_id
        entered.set()
        await release.wait()
        return [{"code": 0}]

    d.group_player_play = _block_group

    task = asyncio.create_task(
        d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)
    )
    try:
        await entered.wait()
        assert phase_at_group == PlaybackPhase.DISPATCHING
        assert attempt_at_group == 1
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_playmusic_request_token_playing_preserves_confirmed():
    """A-PLAYING: pre-existing PLAYING with confirmed_track -DISPATCHING, confirmed preserved."""
    from xiaomusic.playback.runtime_state import (
        begin_confirm,
        begin_play_dispatch,
        begin_resolve,
        confirm_playing,
    )

    d = _build_playmusic_attempt_device()

    # Set up PLAYING state with confirmed_track and track_attempt_id=2
    old_confirmed = TrackReference(entity_id="old", display_name="old_song", source="test")
    s = d.get_runtime_state()
    s = begin_resolve(s, desired_track=old_confirmed, updated_at=1.0)
    s = begin_play_dispatch(s, updated_at=2.0)
    s = begin_confirm(s, updated_at=3.0)
    s = confirm_playing(s, confirmed_track=old_confirmed, updated_at=4.0)
    # advance attempt to 2 via pure model (preserves phase)
    from xiaomusic.playback.runtime_state import begin_track_attempt
    s = begin_track_attempt(s, updated_at=5.0)
    s = begin_track_attempt(s, updated_at=6.0)
    d._set_runtime_state(s)
    assert d.get_runtime_state().phase == PlaybackPhase.PLAYING
    assert d.get_runtime_state().confirmed_track == old_confirmed
    assert d.get_runtime_state().track_attempt_id == 2

    entered = asyncio.Event()
    release = asyncio.Event()
    phase_at_group = None
    confirmed_at_group = None
    attempt_at_group = 0

    async def _block_group(url, name=""):
        nonlocal phase_at_group, confirmed_at_group, attempt_at_group
        phase_at_group = d.get_runtime_state().phase
        confirmed_at_group = d.get_runtime_state().confirmed_track
        attempt_at_group = d.get_runtime_state().track_attempt_id
        entered.set()
        await release.wait()
        return [{"code": 0}]

    d.group_player_play = _block_group

    task = asyncio.create_task(
        d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)
    )
    try:
        await entered.wait()
        assert phase_at_group == PlaybackPhase.DISPATCHING
        assert attempt_at_group == 3  # 2-
        assert confirmed_at_group == old_confirmed
    finally:
        release.set()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt"])
async def test_playmusic_request_token_stale_after_url(mutation_fn):
    """B: mutation during get_music_url -old call returns False, no stop/dispatch/attempt."""
    d = _build_playmusic_attempt_device()

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _block_url(name):
        entered.set()
        await release.wait()
        return "http://x/song1.mp3", "http://x/song1.mp3"

    d.xiaomusic.music_library.get_music_url = _block_url

    stop_called = False
    async def _spy_stop(fast_stop=False, sid=0):
        nonlocal stop_called
        stop_called = True
    d._execute_group_stop = _spy_stop

    dispatch_calls = 0
    async def _spy_group(url, name=""):
        nonlocal dispatch_calls
        dispatch_calls += 1
        return [{"code": 0}]
    d.group_player_play = _spy_group

    a_before = d.get_runtime_state().track_attempt_id

    task = asyncio.create_task(
        d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)
    )
    try:
        await entered.wait()
        if mutation_fn == "command":
            d._accept_command(updated_at=999.0)
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=999.0)
        else:
            d._start_track_attempt(updated_at=999.0)
        release.set()
        result = await task
        assert result is False
        assert stop_called is False
        assert dispatch_calls == 0
        # attempt mutation: only external +1, old call does not add
        expected_attempt = a_before + (1 if mutation_fn == "attempt" else 0)
        assert d.get_runtime_state().track_attempt_id == expected_attempt
        assert d.get_runtime_state().phase != PlaybackPhase.FAILED
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_playmusic_request_token_stale_after_stop():
    """C: mutation during _execute_group_stop -old call returns False, no dispatch/attempt."""
    d = _build_playmusic_attempt_device()

    entered = asyncio.Event()
    release = asyncio.Event()

    async def _block_stop(fast_stop=False, sid=0):
        entered.set()
        await release.wait()
    d._execute_group_stop = _block_stop

    dispatch_calls = 0
    async def _spy_group(url, name=""):
        nonlocal dispatch_calls
        dispatch_calls += 1
        return [{"code": 0}]
    d.group_player_play = _spy_group

    a_before = d.get_runtime_state().track_attempt_id

    task = asyncio.create_task(
        d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)
    )
    try:
        await entered.wait()
        d._accept_command(updated_at=999.0)
        release.set()
        result = await task
        assert result is False
        assert dispatch_calls == 0
        assert d.get_runtime_state().track_attempt_id == a_before
        assert d.get_runtime_state().phase != PlaybackPhase.DISPATCHING
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# ══════════════════════════════════════════════════════════════════════-# _begin_runtime_confirmation wrapper tests
# ══════════════════════════════════════════════════════════════════════-

class TestBeginRuntimeConfirmation:
    """T02-B6: _begin_runtime_confirmation wrapper."""

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _make_device_with_phase(
        phase: PlaybackPhase,
    ) -> XiaoMusicDevice:
        """Create a device whose runtime state is forced to the given phase.

        For DISPATCHING, we go through the real path:
          IDLE -RESOLVING -DISPATCHING
        then bump track_attempt_id so it is non-zero.
        For other phases, we use _set_runtime_state with a fresh dataclass
        so we can test rejection properly.
        """
        d = _make_device_via_new()
        if phase == PlaybackPhase.DISPATCHING:
            from xiaomusic.playback.runtime_state import (
                begin_play_dispatch,
                begin_resolve,
                begin_track_attempt,
            )
            s = d.get_runtime_state()
            s = begin_resolve(
                s,
                desired_track=TrackReference(
                    entity_id="e1", display_name="test", source="test"
                ),
                updated_at=1.0,
            )
            s = begin_play_dispatch(s, updated_at=2.0)
            s = begin_track_attempt(s, updated_at=3.0)
            d._set_runtime_state(s)
            return d
        # Other phases: replace entire state via dataclass
        from dataclasses import replace
        s = d.get_runtime_state()
        s = replace(s, phase=phase, updated_at=1.0)
        d._set_runtime_state(s)
        return d

    # ── DISPATCHING -CONFIRMING ──────────────────────────────────────

    def test_dispatching_to_confirming(self):
        """DISPATCHING -CONFIRMING: phase changes, desired/confirmed/IDs preserved."""
        d = self._make_device_with_phase(PlaybackPhase.DISPATCHING)
        s_before = d.get_runtime_state()

        s = d._begin_runtime_confirmation(updated_at=10.0)

        assert s.phase == PlaybackPhase.CONFIRMING
        assert s.desired_track == s_before.desired_track
        assert s.confirmed_track == s_before.confirmed_track
        assert s.queue_session_id == s_before.queue_session_id
        assert s.command_generation == s_before.command_generation
        assert s.track_attempt_id == s_before.track_attempt_id
        assert s.updated_at == 10.0
        assert s is d.get_runtime_state()

    def test_dispatching_to_confirming_preserves_track_reference(self):
        """DISPATCHING -CONFIRMING: desired_track with entity_id/display_name preserved."""
        d = self._make_device_with_phase(PlaybackPhase.DISPATCHING)
        tr = d.get_runtime_state().desired_track
        assert tr is not None
        assert tr.entity_id == "e1"
        assert tr.display_name == "test"

        s = d._begin_runtime_confirmation(updated_at=5.0)
        assert s.desired_track == tr

    def test_dispatching_to_confirming_returns_new_state(self):
        """DISPATCHING -CONFIRMING: returns new PlaybackRuntimeState instance."""
        d = self._make_device_with_phase(PlaybackPhase.DISPATCHING)
        s_before = d.get_runtime_state()

        s = d._begin_runtime_confirmation(updated_at=1.0)
        assert s is not s_before
        assert s_before.phase == PlaybackPhase.DISPATCHING  # original unchanged

    # ── rejection: RESOLVING ───────────────────────────────────────────

    def test_resolving_rejected(self):
        """RESOLVING -_begin_runtime_confirmation raises TransitionError."""
        d = self._make_device_with_phase(PlaybackPhase.RESOLVING)
        s_before = d.get_runtime_state()

        with pytest.raises(TransitionError):
            d._begin_runtime_confirmation(updated_at=1.0)

        assert d.get_runtime_state() is s_before  # state object unchanged
        assert d.get_runtime_state().phase == PlaybackPhase.RESOLVING

    # ── rejection: PLAYING ─────────────────────────────────────────────

    def test_playing_rejected(self):
        """PLAYING -_begin_runtime_confirmation raises TransitionError."""
        d = self._make_device_with_phase(PlaybackPhase.PLAYING)
        s_before = d.get_runtime_state()

        with pytest.raises(TransitionError):
            d._begin_runtime_confirmation(updated_at=1.0)

        assert d.get_runtime_state() is s_before
        assert d.get_runtime_state().phase == PlaybackPhase.PLAYING

    # ── rejection: STOPPING ────────────────────────────────────────────

    def test_stopping_rejected(self):
        """STOPPING -_begin_runtime_confirmation raises TransitionError."""
        d = self._make_device_with_phase(PlaybackPhase.STOPPING)
        s_before = d.get_runtime_state()

        with pytest.raises(TransitionError):
            d._begin_runtime_confirmation(updated_at=1.0)

        assert d.get_runtime_state() is s_before
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING

    # ── rejection: other non-DISPATCHING phases ────────────────────────

    @pytest.mark.parametrize("phase", [
        PlaybackPhase.IDLE,
        PlaybackPhase.SWITCHING,
        PlaybackPhase.FAILED,
        PlaybackPhase.PAUSED,
        PlaybackPhase.STOPPED,
    ])
    def test_non_dispatching_phases_rejected(self, phase):
        """All non-DISPATCHING phases raise TransitionError, state unchanged."""
        d = self._make_device_with_phase(phase)
        s_before = d.get_runtime_state()

        with pytest.raises(TransitionError):
            d._begin_runtime_confirmation(updated_at=1.0)

        assert d.get_runtime_state() is s_before
        assert d.get_runtime_state().phase == phase


# ══════════════════════════════════════════════════════════════════════-# _confirm_runtime_playing wrapper tests
# ══════════════════════════════════════════════════════════════════════-

class TestConfirmRuntimePlaying:
    """T02-B7: _confirm_runtime_playing wrapper."""

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _make_device_with_phase(
        phase: PlaybackPhase,
    ) -> XiaoMusicDevice:
        """Create a device whose runtime state is forced to the given phase.

        For DISPATCHING/CONFIRMING we go through the real path:
          IDLE -RESOLVING -DISPATCHING -(optionally) CONFIRMING
        then bump track_attempt_id. For other phases we use replace.
        """
        d = _make_device_via_new()
        if phase in (PlaybackPhase.DISPATCHING, PlaybackPhase.CONFIRMING):
            from xiaomusic.playback.runtime_state import (
                begin_confirm,
                begin_play_dispatch,
                begin_resolve,
                begin_track_attempt,
            )
            s = d.get_runtime_state()
            s = begin_resolve(
                s,
                desired_track=TrackReference(
                    entity_id="e1", display_name="test", source="test"
                ),
                updated_at=1.0,
            )
            s = begin_play_dispatch(s, updated_at=2.0)
            s = begin_track_attempt(s, updated_at=3.0)
            if phase == PlaybackPhase.CONFIRMING:
                s = begin_confirm(s, updated_at=4.0)
            d._set_runtime_state(s)
            return d
        # Other phases: replace entire state via dataclass
        from dataclasses import replace
        s = d.get_runtime_state()
        s = replace(s, phase=phase, updated_at=1.0)
        d._set_runtime_state(s)
        return d

    # ── CONFIRMING -PLAYING ──────────────────────────────────────────

    def test_confirming_to_playing(self):
        """CONFIRMING -PLAYING: phase changes, IDs unchanged, returns self."""
        d = self._make_device_with_phase(PlaybackPhase.CONFIRMING)
        s_before = d.get_runtime_state()

        s = d._confirm_runtime_playing(updated_at=10.0)

        assert s.phase == PlaybackPhase.PLAYING
        assert s.queue_session_id == s_before.queue_session_id
        assert s.command_generation == s_before.command_generation
        assert s.track_attempt_id == s_before.track_attempt_id
        assert s.updated_at == 10.0
        assert s is d.get_runtime_state()

    def test_confirming_to_playing_returns_new_state(self):
        """CONFIRMING -PLAYING: returns new PlaybackRuntimeState instance."""
        d = self._make_device_with_phase(PlaybackPhase.CONFIRMING)
        s_before = d.get_runtime_state()

        s = d._confirm_runtime_playing(updated_at=1.0)
        assert s is not s_before
        assert s_before.phase == PlaybackPhase.CONFIRMING  # original unchanged

    # ── confirmed_track: explicit override ────────────────────────────

    def test_explicit_confirmed_track_overrides(self):
        """confirmed_track explicit parameter overrides desired_track."""
        d = self._make_device_with_phase(PlaybackPhase.CONFIRMING)
        explicit = TrackReference(
            entity_id="e99", display_name="custom", source="override"
        )

        s = d._confirm_runtime_playing(
            confirmed_track=explicit, updated_at=10.0
        )

        assert s.confirmed_track == explicit
        assert s.confirmed_track.entity_id == "e99"

    def test_none_confirmed_track_uses_desired(self):
        """confirmed_track=None falls back to desired_track."""
        d = self._make_device_with_phase(PlaybackPhase.CONFIRMING)
        desired = d.get_runtime_state().desired_track
        assert desired is not None
        assert desired.entity_id == "e1"

        s = d._confirm_runtime_playing(updated_at=10.0)

        assert s.confirmed_track == desired
        assert s.confirmed_track is not None

    # ── expected_end_at ───────────────────────────────────────────────

    def test_expected_end_at_written(self):
        """expected_end_at is written to state."""
        d = self._make_device_with_phase(PlaybackPhase.CONFIRMING)

        s = d._confirm_runtime_playing(expected_end_at=180.5, updated_at=10.0)

        assert s.expected_end_at == 180.5

    def test_expected_end_at_none(self):
        """expected_end_at=None is accepted."""
        d = self._make_device_with_phase(PlaybackPhase.CONFIRMING)

        s = d._confirm_runtime_playing(expected_end_at=None, updated_at=10.0)

        assert s.expected_end_at is None

    # ── failure cleared ───────────────────────────────────────────────

    def test_failure_cleared(self):
        """failure is set to None after confirm_playing."""
        d = self._make_device_with_phase(PlaybackPhase.CONFIRMING)
        from dataclasses import replace

        from xiaomusic.playback.runtime_state import FailureState
        s_with_failure = replace(
            d.get_runtime_state(),
            failure=FailureState(count=3, reason="prev_failure"),
        )
        d._set_runtime_state(s_with_failure)

        s = d._confirm_runtime_playing(updated_at=10.0)

        assert s.failure is None

    # ── desired preserved ─────────────────────────────────────────────

    def test_desired_preserved(self):
        """desired_track is preserved across transition."""
        d = self._make_device_with_phase(PlaybackPhase.CONFIRMING)
        desired_before = d.get_runtime_state().desired_track
        assert desired_before is not None

        s = d._confirm_runtime_playing(updated_at=10.0)

        assert s.desired_track == desired_before

    # ── IDs unchanged ─────────────────────────────────────────────────

    def test_ids_unchanged(self):
        """queue_session_id, command_generation, track_attempt_id unchanged."""
        d = self._make_device_with_phase(PlaybackPhase.CONFIRMING)
        s_before = d.get_runtime_state()

        s = d._confirm_runtime_playing(updated_at=10.0)

        assert s.queue_session_id == s_before.queue_session_id
        assert s.command_generation == s_before.command_generation
        assert s.track_attempt_id == s_before.track_attempt_id

    # ── PAUSED -PLAYING (constructed via pure model path) ────────────

    @staticmethod
    def _make_device_paused() -> XiaoMusicDevice:
        """Construct a genuine PAUSED state via pure model functions:

        IDLE -RESOLVING -DISPATCHING -CONFIRMING -PLAYING -PAUSED.
        No replace-based phase forgery -every transition is real.
        """
        from xiaomusic.playback.runtime_state import (
            begin_confirm,
            begin_play_dispatch,
            begin_resolve,
            begin_track_attempt,
            confirm_playing,
            pause,
        )
        d = _make_device_via_new()
        s = d.get_runtime_state()
        s = begin_resolve(
            s,
            desired_track=TrackReference(
                entity_id="e1", display_name="test", source="test"
            ),
            updated_at=1.0,
        )
        s = begin_play_dispatch(s, updated_at=2.0)
        s = begin_track_attempt(s, updated_at=3.0)
        s = begin_confirm(s, updated_at=4.0)
        s = confirm_playing(s, updated_at=5.0)
        s = pause(s, updated_at=6.0)
        d._set_runtime_state(s)
        return d

    def test_paused_to_playing(self):
        """PAUSED -PLAYING: phase changes, IDs unchanged."""
        d = self._make_device_paused()
        s_before = d.get_runtime_state()
        assert s_before.phase == PlaybackPhase.PAUSED

        s = d._confirm_runtime_playing(updated_at=10.0)

        assert s.phase == PlaybackPhase.PLAYING
        assert s.queue_session_id == s_before.queue_session_id
        assert s.command_generation == s_before.command_generation
        assert s.track_attempt_id == s_before.track_attempt_id
        assert s.updated_at == 10.0
        assert s is d.get_runtime_state()

    def test_paused_explicit_confirmed_track_overrides(self):
        """PAUSED: confirmed_track explicit overrides desired_track."""
        d = self._make_device_paused()
        explicit = TrackReference(
            entity_id="e99", display_name="custom", source="override"
        )

        s = d._confirm_runtime_playing(
            confirmed_track=explicit, updated_at=10.0
        )

        assert s.confirmed_track == explicit
        assert s.confirmed_track.entity_id == "e99"

    def test_paused_none_confirmed_track_uses_desired(self):
        """PAUSED: confirmed_track=None falls back to desired_track."""
        d = self._make_device_paused()
        desired = d.get_runtime_state().desired_track
        assert desired is not None

        s = d._confirm_runtime_playing(updated_at=10.0)

        assert s.confirmed_track == desired

    def test_paused_expected_end_at_written(self):
        """PAUSED: expected_end_at is written to state."""
        d = self._make_device_paused()

        s = d._confirm_runtime_playing(expected_end_at=180.5, updated_at=10.0)

        assert s.expected_end_at == 180.5

    def test_paused_failure_cleared(self):
        """PAUSED: failure is set to None after confirm_playing."""
        d = self._make_device_paused()
        from dataclasses import replace

        from xiaomusic.playback.runtime_state import FailureState
        s_with_failure = replace(
            d.get_runtime_state(),
            failure=FailureState(count=3, reason="prev_failure"),
        )
        d._set_runtime_state(s_with_failure)

        s = d._confirm_runtime_playing(updated_at=10.0)

        assert s.failure is None

    def test_paused_desired_preserved(self):
        """PAUSED: desired_track is preserved across transition."""
        d = self._make_device_paused()
        desired_before = d.get_runtime_state().desired_track

        s = d._confirm_runtime_playing(updated_at=10.0)

        assert s.desired_track == desired_before

    def test_paused_ids_unchanged(self):
        """PAUSED: queue_session_id, command_generation, track_attempt_id unchanged."""
        d = self._make_device_paused()
        s_before = d.get_runtime_state()

        s = d._confirm_runtime_playing(updated_at=10.0)

        assert s.queue_session_id == s_before.queue_session_id
        assert s.command_generation == s_before.command_generation
        assert s.track_attempt_id == s_before.track_attempt_id

    # ── rejection: DISPATCHING ────────────────────────────────────────

    def test_dispatching_rejected(self):
        """DISPATCHING -_confirm_runtime_playing raises TransitionError."""
        d = self._make_device_with_phase(PlaybackPhase.DISPATCHING)
        s_before = d.get_runtime_state()

        with pytest.raises(TransitionError):
            d._confirm_runtime_playing(updated_at=1.0)

        assert d.get_runtime_state() is s_before
        assert d.get_runtime_state().phase == PlaybackPhase.DISPATCHING

    # ── rejection: PLAYING ────────────────────────────────────────────

    def test_playing_rejected(self):
        """PLAYING -_confirm_runtime_playing raises TransitionError."""
        d = self._make_device_with_phase(PlaybackPhase.PLAYING)
        s_before = d.get_runtime_state()

        with pytest.raises(TransitionError):
            d._confirm_runtime_playing(updated_at=1.0)

        assert d.get_runtime_state() is s_before
        assert d.get_runtime_state().phase == PlaybackPhase.PLAYING

    # ── rejection: STOPPING ───────────────────────────────────────────

    def test_stopping_rejected(self):
        """STOPPING -_confirm_runtime_playing raises TransitionError."""
        d = self._make_device_with_phase(PlaybackPhase.STOPPING)
        s_before = d.get_runtime_state()

        with pytest.raises(TransitionError):
            d._confirm_runtime_playing(updated_at=1.0)

        assert d.get_runtime_state() is s_before
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING

    # ── rejection: other phases ───────────────────────────────────────

    @pytest.mark.parametrize("phase", [
        PlaybackPhase.IDLE,
        PlaybackPhase.RESOLVING,
        PlaybackPhase.SWITCHING,
        PlaybackPhase.FAILED,
        PlaybackPhase.STOPPED,
    ])
    def test_non_confirming_phases_rejected(self, phase):
        """All non-CONFIRMING phases raise TransitionError, state unchanged."""
        d = self._make_device_with_phase(phase)
        s_before = d.get_runtime_state()

        with pytest.raises(TransitionError):
            d._confirm_runtime_playing(updated_at=1.0)

        assert d.get_runtime_state() is s_before
        assert d.get_runtime_state().phase == phase


# ══════════════════════════════════════════════════════════════════════-# AST guard: begin_confirm called exactly once in device_player
# ══════════════════════════════════════════════════════════════════════-

# ══════════════════════════════════════════════════════════════════════-# _playmusic confirmation flow: real _playmusic integration tests
# ══════════════════════════════════════════════════════════════════════-

def _build_confirmation_device(
    *,
    direct_result=None,
    proxy_result=None,
    jellyfin_candidate: bool = False,
    proxy_url: str = "http://proxy/song1.mp3",
    confirm_result: bool = True,
    is_playing_result: bool = True,
):
    """Build a device with real _playmusic / _try_proxy_fallback.

    Fakes network boundaries (group_player_play, get_music_url) and
    mark/schedule/confirm.  Returns (device, spies_dict).
    """
    import asyncio

    from xiaomusic.config import Device

    class _ML:
        music_list = {"\u5168\u90e8": ["song1"]}

        async def get_music_url(self, name):
            return "http://x/song1.mp3", "http://x/song1.mp3"

        async def get_music_duration(self, name):
            return 10.0

        def is_jellyfin_url(self, u):
            return True

        def get_proxy_url(self, origin_url, name=""):
            return proxy_url

    class _Analytics:
        async def send_play_event(self, *a, **k):
            pass

    import logging as _logging
    xm = types.SimpleNamespace(
        config=types.SimpleNamespace(
            delay_sec=0,
            verbose=False,
            ffmpeg_location="",
            jellyfin_proxy_mode="auto",
        ),
        log=_logging.getLogger("test-playmusic-confirm"),
        auth_manager=types.SimpleNamespace(mina_call=None),
        music_library=_ML(),
        analytics=_Analytics(),
        device_manager=types.SimpleNamespace(
            get_group_device_id_list=lambda g: [],
            get_group_devices=lambda g: {},
        ),
        event_bus=None,
    )
    dev = Device(
        did="d1", device_id="d1", hardware="", name="",
        play_type=PLAY_TYPE_ALL, cur_playlist="\u5168\u90e8", playlist2music={},
    )
    d = XiaoMusicDevice(xm, dev, group_name="g")
    d.cancel_group_next_timer = _noop
    d.group_force_stop_xiaoai = _noop_list
    d._invalidate_manual_navigation = lambda reason: None
    d._execute_group_stop = (
        lambda fast_stop=False, sid=0: asyncio.sleep(0, result=None)
    )
    d._refresh_runtime_volume = lambda **kw: asyncio.sleep(0, result=0)
    d.set_next_music_timeout = lambda sec, token=None: _noop
    d._start_duration_probe = lambda name, sid, **kw: None
    d.auto_add_song = lambda cur, sec: asyncio.sleep(0, result=None)

    # Spies
    mark_spy: list[dict] = []
    schedule_spy: list[dict] = []
    confirm_spy: list[dict] = []
    group_urls: list[str] = []

    async def _spy_mark(**kw):
        mark_spy.append(kw)

    def _spy_schedule(**kw):
        schedule_spy.append(kw)

    async def _spy_confirm(name, sid, **kw):
        confirm_spy.append({"name": name, "sid": sid, **kw})
        return confirm_result

    d._mark_play_started = _spy_mark
    d._schedule_playback_confirmation = _spy_schedule
    d._confirm_playback_started = _spy_confirm
    d._handle_play_failure = lambda **kw: asyncio.sleep(0, result=None)

    # group_player_play: returns direct_result first, proxy_result second
    _group_call = 0

    async def _spy_group(url, name=""):
        nonlocal _group_call
        _group_call += 1
        group_urls.append(url)
        if _group_call == 1:
            return direct_result if direct_result is not None else [{"code": 0}]
        return proxy_result if proxy_result is not None else [{"code": 0}]

    d.group_player_play = _spy_group
    d.get_if_xiaoai_is_playing = lambda: asyncio.sleep(0, result=is_playing_result)

    if jellyfin_candidate:
        # Keep real _is_jellyfin_auto_candidate (will use is_jellyfin_url=True, auto mode)
        pass
    else:
        d._is_jellyfin_auto_candidate = lambda **kw: False
        d._try_proxy_fallback = lambda **kw: ""

    spies = {
        "mark": mark_spy,
        "schedule": schedule_spy,
        "confirm": confirm_spy,
        "group_urls": group_urls,
    }
    return d, spies


# ── Test A: direct background success ──────────────────────────────────


@pytest.mark.asyncio
async def test_playmusic_direct_bg_success_confirming_phase():
    """A: direct background success -_mark_play_started sees CONFIRMING,
    schedule receives current attempt token."""
    d, spies = _build_confirmation_device(
        direct_result=[{"code": 0}],
    )

    await d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)

    s = d.get_runtime_state()
    assert s.phase == PlaybackPhase.CONFIRMING, (
        f"Expected CONFIRMING, got {s.phase}"
    )
    assert s.track_attempt_id == 1

    # _mark_play_started was called (phase is CONFIRMING at that point)
    assert len(spies["mark"]) == 1

    # schedule received the current attempt token
    assert len(spies["schedule"]) == 1
    schedule_token = spies["schedule"][0].get("token")
    assert isinstance(schedule_token, LifecycleToken)
    current_token = d._capture_lifecycle_token()
    assert schedule_token.queue_session_id == current_token.queue_session_id
    assert schedule_token.command_generation == current_token.command_generation
    assert schedule_token.track_attempt_id == current_token.track_attempt_id


# ── Test B: direct sync success ───────────────────────────────────────


@pytest.mark.asyncio
async def test_playmusic_direct_sync_success_confirming_phase():
    """B: direct sync success -_confirm_playback_started sees CONFIRMING,
    attempt token / IDs unchanged."""
    d, spies = _build_confirmation_device(
        direct_result=[{"code": 0}],
    )

    # Capture state before
    token_before = d._capture_lifecycle_token()
    q_before = token_before.queue_session_id
    c_before = token_before.command_generation

    await d._playmusic("song1", confirm_start_in_background=False, fast_stop=False)

    s = d.get_runtime_state()
    assert s.phase == PlaybackPhase.PLAYING
    assert s.track_attempt_id == 1
    assert s.queue_session_id == q_before
    assert s.command_generation == c_before

    # _confirm_playback_started was called
    assert len(spies["confirm"]) == 1

    # mark was called (sync path also marks after confirm)
    assert len(spies["mark"]) == 1


# ── Test C: direct None -proxy success -background ──────────────────


@pytest.mark.asyncio
async def test_playmusic_proxy_fallback_bg_success():
    """C: direct all None -real _try_proxy_fallback success -background.
    Two group URLs (direct + proxy), attempt=2, phase CONFIRMING,
    schedule token equals attempt2 current token; q/c unchanged."""
    d, spies = _build_confirmation_device(
        direct_result=[None],
        proxy_result=[{"code": 0}],
        jellyfin_candidate=True,
    )

    q_before = d.get_runtime_state().queue_session_id
    c_before = d.get_runtime_state().command_generation

    result = await d._playmusic(
        "song1", confirm_start_in_background=True, fast_stop=True
    )

    assert result is True
    s = d.get_runtime_state()
    assert s.track_attempt_id == 2, f"Expected attempt=2, got {s.track_attempt_id}"
    assert s.phase == PlaybackPhase.CONFIRMING
    assert s.queue_session_id == q_before
    assert s.command_generation == c_before

    # Two group URLs: direct + proxy
    assert len(spies["group_urls"]) == 2
    assert spies["group_urls"][0] == "http://x/song1.mp3"  # direct
    assert spies["group_urls"][1] == "http://proxy/song1.mp3"  # proxy

    # mark called once
    assert len(spies["mark"]) == 1

    # schedule received attempt2 current token
    assert len(spies["schedule"]) == 1
    schedule_token = spies["schedule"][0].get("token")
    assert isinstance(schedule_token, LifecycleToken)
    current_token = d._capture_lifecycle_token()
    assert schedule_token.track_attempt_id == current_token.track_attempt_id == 2
    assert schedule_token.queue_session_id == current_token.queue_session_id
    assert schedule_token.command_generation == current_token.command_generation


# ── Test D: proxy during command mutation -handoff rejected ─────────


@pytest.mark.asyncio
async def test_playmusic_proxy_handoff_rejected_on_command_mutation():
    """D: proxy fallback group Event blocked, command accepted during block.
    Handoff rejected, mark/schedule=0, no PLAYING/FAILED; old call False."""
    import asyncio as _asyncio

    entered = _asyncio.Event()
    release = _asyncio.Event()

    d, spies = _build_confirmation_device(
        direct_result=[None],
        jellyfin_candidate=True,
        proxy_result=None,  # placeholder -will be replaced by blocking version
    )

    # Override group_player_play with blocking proxy call
    _group_call = 0

    async def _blocking_group(url, name=""):
        nonlocal _group_call
        _group_call += 1
        spies["group_urls"].append(url)
        if _group_call == 1:
            return [None]  # direct failure
        # Second call = proxy fallback internal dispatch -block
        entered.set()
        await release.wait()
        return [{"code": 0}]

    d.group_player_play = _blocking_group

    a_before = d.get_runtime_state().track_attempt_id

    task = _asyncio.create_task(
        d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)
    )
    try:
        await entered.wait()
        # While proxy fallback is blocked in group_player_play, mutate command
        d._accept_command(updated_at=999.0)
        release.set()
        result = await task

        assert result is False
        # No mark or schedule
        assert len(spies["mark"]) == 0
        assert len(spies["schedule"]) == 0
        # Phase is NOT PLAYING or FAILED
        assert d.get_runtime_state().phase not in (
            PlaybackPhase.PLAYING,
            PlaybackPhase.FAILED,
        )
        assert d.get_runtime_state().failure is None
        # attempt advanced by _playmusic initial + fallback's internal _start_track_attempt
        assert d.get_runtime_state().track_attempt_id == a_before + 2
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except _asyncio.CancelledError:
                pass


def test_begin_confirm_called_only_in_begin_runtime_confirmation():
    """AST: begin_confirm is called exactly once, inside _begin_runtime_confirmation."""
    with open(dp.__file__) as f:
        tree = ast.parse(f.read())

    class BeginConfirmCallVisitor(ast.NodeVisitor):
        def __init__(self):
            self.calls: list[tuple[int, str]] = []  # (lineno, function_name)
            self._stack: list[str] = []

        def visit_FunctionDef(self, node):
            self._stack.append(node.name)
            self.generic_visit(node)
            self._stack.pop()

        def visit_AsyncFunctionDef(self, node):
            self._stack.append(node.name)
            self.generic_visit(node)
            self._stack.pop()

        def visit_Call(self, node):
            ctx = self._stack[-1] if self._stack else "<module>"
            if isinstance(node.func, ast.Name) and node.func.id == "begin_confirm":
                self.calls.append((node.lineno, ctx))
            self.generic_visit(node)

    visitor = BeginConfirmCallVisitor()
    visitor.visit(tree)

    assert len(visitor.calls) == 1, (
        f"begin_confirm must be called exactly once in device_player.py, "
        f"got {len(visitor.calls)}: {visitor.calls}"
    )
    assert visitor.calls[0][1] == "_begin_runtime_confirmation", (
        f"begin_confirm must only be called in _begin_runtime_confirmation, "
        f"found in {visitor.calls[0][1]}"
    )


def test_confirm_playing_called_only_in_confirm_runtime_playing():
    """AST: confirm_playing is called exactly once, inside _confirm_runtime_playing."""
    with open(dp.__file__) as f:
        tree = ast.parse(f.read())

    class ConfirmPlayingCallVisitor(ast.NodeVisitor):
        def __init__(self):
            self.calls: list[tuple[int, str]] = []  # (lineno, function_name)
            self._stack: list[str] = []

        def visit_FunctionDef(self, node):
            self._stack.append(node.name)
            self.generic_visit(node)
            self._stack.pop()

        def visit_AsyncFunctionDef(self, node):
            self._stack.append(node.name)
            self.generic_visit(node)
            self._stack.pop()

        def visit_Call(self, node):
            ctx = self._stack[-1] if self._stack else "<module>"
            if isinstance(node.func, ast.Name) and node.func.id == "confirm_playing":
                self.calls.append((node.lineno, ctx))
            self.generic_visit(node)

    visitor = ConfirmPlayingCallVisitor()
    visitor.visit(tree)

    assert len(visitor.calls) == 1, (
        f"confirm_playing must be called exactly once in device_player.py, "
        f"got {len(visitor.calls)}: {visitor.calls}"
    )
    assert visitor.calls[0][1] == "_confirm_runtime_playing", (
        f"confirm_playing must only be called in _confirm_runtime_playing, "
        f"found in {visitor.calls[0][1]}"
    )


# ── LifecycleToken guard tests for _mark_play_started ──────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt"])
async def test_mark_stale_during_refresh_volume_blocks_all_writes(mutation_fn):
    """A: refresh_volume await 期间单维 stale（command/queue/attempt 各一），
    后续 duration/analytics/timer/probe/event 全为 0，failure counters 不变->?"""
    import time

    from xiaomusic.config import Device
    from xiaomusic.events import PLAYER_STATE_CHANGED

    enter_volume = asyncio.Event()
    release_volume = asyncio.Event()

    async def _blocking_volume(**kw):
        enter_volume.set()
        await release_volume.wait()
        return 0

    xm = _fake_xm()
    dev = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                 play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm, dev, group_name="g")
    d._refresh_runtime_volume = _blocking_volume
    d.auto_add_song = lambda cur, sec: asyncio.sleep(0, result=None)

    events_published: list[str] = []
    class _SpyBus:
        def publish(self, event, **kw):
            events_published.append(event)
    d.event_bus = _SpyBus()

    analytics_calls: list[dict] = []
    d.xiaomusic.analytics = types.SimpleNamespace(
        send_play_event=lambda name, sec, hw: analytics_calls.append(
            {"name": name, "sec": sec}
        ) or asyncio.sleep(0),
    )

    timer_secs: list[float] = []
    async def _spy_timer(sec, token=None):
        timer_secs.append(sec)
    d.set_next_music_timeout = _spy_timer

    duration_probe_calls: list[dict] = []
    d._start_duration_probe = lambda name, sid, **kw: duration_probe_calls.append(
        {"name": name, "sid": sid}
    )

    d._play_failed_cnt = 3
    d._play_fail_first_ts = 999.0
    d._play_fail_last_reason = "before"

    token = d._capture_lifecycle_token()
    sid = d._play_session_id

    task = asyncio.create_task(
        d._mark_play_started(
            name="test_song",
            sid=sid,
            cur_playlist="全部",
            token=token,
        )
    )

    await enter_volume.wait()

    # Mutate exactly one dimension via the real wrapper
    if mutation_fn == "command":
        d._accept_command(updated_at=time.time())
    elif mutation_fn == "queue":
        d._start_queue_session(updated_at=time.time())
    else:
        d._start_track_attempt(updated_at=time.time())

    release_volume.set()
    await task

    assert d._duration == 0, f"[{mutation_fn}] _duration={d._duration}"
    assert analytics_calls == [], f"[{mutation_fn}] analytics_calls={analytics_calls}"
    assert timer_secs == [], f"[{mutation_fn}] timer_secs={timer_secs}"
    assert duration_probe_calls == [], f"[{mutation_fn}] duration_probe_calls={duration_probe_calls}"
    assert PLAYER_STATE_CHANGED not in events_published, f"[{mutation_fn}] events={events_published}"
    assert d._play_failed_cnt == 0
    assert d._play_fail_first_ts == 0.0
    assert d._play_fail_last_reason == ""
    assert d._start_time > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt"])
async def test_mark_stale_during_duration_blocks_timer_and_analytics(mutation_fn):
    """B: duration await 期间单维 stale -> 不写 _duration/analytics/timer->?"""
    import time

    from xiaomusic.config import Device

    enter_duration = asyncio.Event()
    release_duration = asyncio.Event()

    async def _blocking_duration(name):
        enter_duration.set()
        await release_duration.wait()
        return 180.0

    xm = _fake_xm()
    dev = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                 play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm, dev, group_name="g")
    d._refresh_runtime_volume = lambda **kw: asyncio.sleep(0, result=0)
    d.xiaomusic.music_library = types.SimpleNamespace(
        get_music_duration=_blocking_duration,
    )
    d.auto_add_song = lambda cur, sec: asyncio.sleep(0, result=None)

    analytics_calls: list[dict] = []
    d.xiaomusic.analytics = types.SimpleNamespace(
        send_play_event=lambda name, sec, hw: analytics_calls.append(
            {"name": name, "sec": sec}
        ) or asyncio.sleep(0),
    )

    timer_secs: list[float] = []
    async def _spy_timer(sec, token=None):
        timer_secs.append(sec)
    d.set_next_music_timeout = _spy_timer

    token = d._capture_lifecycle_token()
    sid = d._play_session_id

    task = asyncio.create_task(
        d._mark_play_started(
            name="test_song",
            sid=sid,
            cur_playlist="全部",
            token=token,
        )
    )

    await enter_duration.wait()

    if mutation_fn == "command":
        d._accept_command(updated_at=time.time())
    elif mutation_fn == "queue":
        d._start_queue_session(updated_at=time.time())
    else:
        d._start_track_attempt(updated_at=time.time())

    release_duration.set()
    await task

    assert d._duration == 0, f"[{mutation_fn}] _duration={d._duration}"
    assert analytics_calls == [], f"[{mutation_fn}] analytics_calls={analytics_calls}"
    assert timer_secs == [], f"[{mutation_fn}] timer_secs={timer_secs}"


@pytest.mark.asyncio
async def test_mark_current_path_timer_receives_same_token():
    """C: current 正常路径 timer 收到同一 token->?"""
    from xiaomusic.config import Device

    xm = _fake_xm()
    dev = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                 play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm, dev, group_name="g")
    d._refresh_runtime_volume = lambda **kw: asyncio.sleep(0, result=0)
    d.xiaomusic.music_library = types.SimpleNamespace(
        get_music_duration=lambda name: asyncio.sleep(0, result=45.0),
    )
    d.xiaomusic.analytics = types.SimpleNamespace(
        send_play_event=lambda name, sec, hw: asyncio.sleep(0),
    )
    d.auto_add_song = lambda cur, sec: asyncio.sleep(0, result=None)

    # Capture the token that will be passed to set_next_music_timeout
    captured_token: LifecycleToken | None = None
    captured_sec: float | None = None

    async def _spy_timer(sec, token=None):
        nonlocal captured_token, captured_sec
        captured_sec = sec
        captured_token = token

    d.set_next_music_timeout = _spy_timer

    token = d._capture_lifecycle_token()
    sid = d._play_session_id

    await d._mark_play_started(
        name="test_song",
        sid=sid,
        cur_playlist="全部",
        token=token,
    )

    assert captured_token is not None
    assert captured_token.queue_session_id == token.queue_session_id
    assert captured_token.command_generation == token.command_generation
    assert captured_token.track_attempt_id == token.track_attempt_id
    assert captured_sec is not None and captured_sec > 0


@pytest.mark.asyncio
async def test_sync_fallback_mark_receives_attempt2_token():
    """D: sync fallback 最->?mark 收到 attempt2 token（_confirm 返回 False
    -> Jellyfin fallback 成功 -> handoff 替换 _attempt_token -> mark = attempt2）->?"""
    import logging as _logging
    import time

    from xiaomusic.config import Device
    xm = types.SimpleNamespace(
        config=types.SimpleNamespace(
            delay_sec=0, verbose=False, ffmpeg_location="",
            jellyfin_proxy_mode="auto",
        ),
        log=_logging.getLogger("test-sync-fallback"),
        auth_manager=types.SimpleNamespace(mina_call=None),
        music_library=types.SimpleNamespace(
            music_list={"全部": []},
            is_music_exist=lambda n: True,
            is_jellyfin_url=lambda u: True,
            get_music_url=lambda name: asyncio.sleep(
                0, result=("http://jf/x.mp3", "http://jf/x.mp3")
            ),
            get_proxy_url=lambda origin_url, name="": "http://proxy/" + name,
            get_music_duration=lambda name: asyncio.sleep(0, result=10.0),
        ),
        analytics=types.SimpleNamespace(
            send_play_event=lambda *a, **k: asyncio.sleep(0),
        ),
        device_manager=types.SimpleNamespace(
            get_group_device_id_list=lambda g: [],
        ),
        event_bus=None,
    )
    dev = Device(
        did="d1", device_id="d1", hardware="OH2P", name="T",
        play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={},
    )
    d = XiaoMusicDevice(xm, dev, group_name="g")
    d.cancel_group_next_timer = _noop
    d._invalidate_manual_navigation = lambda reason: None
    d._is_jellyfin_auto_candidate = lambda **kw: True
    d._handle_play_failure = lambda **kw: asyncio.sleep(0, result=None)

    # _confirm_playback_started returns False
    async def _confirm_false(name, sid, **kw):
        return False
    d._confirm_playback_started = _confirm_false

    # _try_proxy_fallback: success (bumps attempt internally via _start_track_attempt)
    async def _proxy_success(**kw):
        d._start_track_attempt(updated_at=time.time())
        return "http://proxy/song1"
    d._try_proxy_fallback = _proxy_success

    # Spy _mark_play_started to capture the token it receives
    mark_token: LifecycleToken | None = None
    real_mark = d._mark_play_started
    async def _spy_mark(**kw):
        nonlocal mark_token
        mark_token = kw.get("token")
        await real_mark(**kw)
    d._mark_play_started = _spy_mark

    # group_player_play: direct succeeds (reaches _confirm), proxy succeeds (fallback)
    calls = 0
    async def _spy_group(url, name=""):
        nonlocal calls
        calls += 1
        if calls <= 2:
            return [{"code": 0}]
        return [None]
    d.group_player_play = _spy_group

    await d._playmusic("song1", confirm_start_in_background=False, fast_stop=False)

    # Verify mark_token has attempt_id=2
    assert mark_token is not None, "_mark_play_started was not called"
    # track_attempt_id should be 2: 1 (original dispatch) + 1 (fallback)
    assert mark_token.track_attempt_id == 2, (
        f"Expected attempt_id=2, got {mark_token.track_attempt_id}"
    )


# ══════════════════════════════════════════════════════════════════════
# _confirm_runtime_playing_for_attempt tests
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_confirm_runtime_playing_bg_dispatch_then_bg_confirm_playing():
    """A: bg dispatch success, schedule intercepted -> phase CONFIRMING;
    then bg confirm started=True -> PLAYING, confirmed=desired, failure cleared."""
    d = _build_playmusic_attempt_device()
    # Mock group_player_play to return success (empty device list -> all None by default)
    d.group_player_play = lambda url, name="", **kw: asyncio.sleep(
        0, result=[{"code": 0}]
    )
    # Intercept schedule: capture token, do NOT start background task
    scheduled_token: LifecycleToken | None = None

    def _capture_schedule(**kw):
        nonlocal scheduled_token
        scheduled_token = kw.get("token")

    d._schedule_playback_confirmation = _capture_schedule

    result = await d._playmusic(
        "song1", confirm_start_in_background=True, fast_stop=True
    )
    assert result is True
    # Phase stays CONFIRMING ->?optimistic mark does NOT call helper
    assert d.get_runtime_state().phase == PlaybackPhase.CONFIRMING
    assert scheduled_token is not None

    desired = d.get_runtime_state().desired_track
    assert desired is not None

    # Now run real background confirm with started=True
    await d._background_confirm_playback_started(
        name="song1",
        sid=d._play_session_id,
        cur_playlist="全部",
        origin_url="http://x/song1.mp3",
        current_url="http://x/song1.mp3",
        fast_stop=True,
        token=scheduled_token,
    )

    s = d.get_runtime_state()
    assert s.phase == PlaybackPhase.PLAYING
    assert s.confirmed_track is not None
    # confirmed_track defaults to desired_track
    assert s.confirmed_track.display_name == "song1"
    assert s.failure is None


@pytest.mark.asyncio
async def test_confirm_runtime_playing_sync_success_mark_spy_phase_playing():
    """B: sync direct success -> _mark_play_started sees phase PLAYING."""
    d = _build_playmusic_attempt_device()
    # Mock group_player_play to return success
    d.group_player_play = lambda url, name="", **kw: asyncio.sleep(
        0, result=[{"code": 0}]
    )

    phase_at_mark = None

    async def _spy_mark(**kw):
        nonlocal phase_at_mark
        phase_at_mark = d.get_runtime_state().phase

    d._mark_play_started = _spy_mark

    result = await d._playmusic(
        "song1", confirm_start_in_background=False, fast_stop=True
    )
    assert result is True
    assert phase_at_mark == PlaybackPhase.PLAYING, (
        f"Expected PLAYING at mark entry, got {phase_at_mark}"
    )


@pytest.mark.asyncio
async def test_confirm_runtime_playing_bg_grace_second_true():
    """C: bg initial False, grace second True -> PLAYING.

    Uses asyncio.Event for controlled sequencing without fixed sleep.
    """
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._is_jellyfin_auto_candidate = lambda **kw: False

    # Set phase to CONFIRMING via real transitions
    from xiaomusic.playback.runtime_state import (
        begin_confirm,
        begin_play_dispatch,
        begin_resolve,
    )
    s = d.get_runtime_state()
    s = begin_resolve(
        s,
        desired_track=TrackReference(
            entity_id="e1", display_name="A", source="test"
        ),
        updated_at=1.0,
    )
    s = begin_play_dispatch(s, updated_at=2.0)
    s = begin_confirm(s, updated_at=3.0)
    d._set_runtime_state(s)
    assert d.get_runtime_state().phase == PlaybackPhase.CONFIRMING

    # Confirm: first False, second True
    first_call_entered = asyncio.Event()
    second_call_entered = asyncio.Event()
    call_count = 0

    async def _confirm_false_then_true(name, sid, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_call_entered.set()
            return False
        second_call_entered.set()
        return True

    d._confirm_playback_started = _confirm_false_then_true
    d._bg_confirm_false_count = 0

    task = asyncio.create_task(
        d._background_confirm_playback_started(
            name="A",
            sid=1,
            cur_playlist="BGM",
            origin_url="http://x/a.mp3",
            current_url="http://x/a.mp3",
            fast_stop=False,
            token=token,
        )
    )
    try:
        await asyncio.wait_for(second_call_entered.wait(), timeout=5.0)
        await asyncio.wait_for(task, timeout=2.0)
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert d.get_runtime_state().phase == PlaybackPhase.PLAYING
    assert d._bg_confirm_false_count == 0


@pytest.mark.asyncio
async def test_confirm_runtime_playing_stale_token_no_playing():
    """D: stale token + bg started=True via real _background_confirm_playback_started
    -> no PLAYING, no mark, counter unchanged.

    The confirm probe blocks via Event; while blocked a command is accepted
    (token stale).  On release the probe returns True, but the stale guard
    in _confirm_runtime_playing_for_attempt prevents any state mutation.
    """
    probe_entered = asyncio.Event()
    probe_release = asyncio.Event()

    d = _make_device_via_new()
    d._play_session_id = 1
    d._is_jellyfin_auto_candidate = lambda **kw: False
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)

    # Establish CONFIRMING via real wrappers
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="A", source="test"
        ),
        updated_at=2.0,
    )
    d._begin_runtime_play_dispatch(updated_at=3.0)
    d._begin_runtime_confirmation(updated_at=4.0)
    assert d.get_runtime_state().phase == PlaybackPhase.CONFIRMING

    # Set a non-zero counter to verify it is NOT reset
    d._bg_confirm_false_count = 7
    counter_before = d._bg_confirm_false_count

    mark_called = False
    async def _spy_mark(**kw):
        nonlocal mark_called
        mark_called = True
    d._mark_play_started = _spy_mark

    # _confirm_playback_started: block on first call, then return True
    async def _blocking_confirm(name, sid, **kw):
        probe_entered.set()
        await probe_release.wait()
        return True
    d._confirm_playback_started = _blocking_confirm

    task = asyncio.create_task(
        d._background_confirm_playback_started(
            name="A",
            sid=1,
            cur_playlist="BGM",
            origin_url="http://x/a.mp3",
            current_url="http://x/a.mp3",
            fast_stop=False,
            token=token,
        )
    )
    try:
        await asyncio.wait_for(probe_entered.wait(), timeout=5.0)
        # Make token stale while confirm probe is blocked
        d._accept_command(updated_at=999.0)
        assert d._is_lifecycle_token_stale(token)
        probe_release.set()
        await asyncio.wait_for(task, timeout=5.0)

        # Stale token prevented the helper from writing PLAYING or clearing counter
        assert d.get_runtime_state().phase != PlaybackPhase.PLAYING
        assert not mark_called
        assert d._bg_confirm_false_count == counter_before
    finally:
        if not task.done():
            probe_release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def test_confirm_runtime_playing_already_playing_idempotent():
    """E: phase PLAYING + current token -> idempotent True, state unchanged.

    Constructs PLAYING via real wrappers (request→dispatch→attempt→confirmation→confirm),
    never via dataclasses.replace.  Then calls the guard with current token;
    it must return True without replacing the state object.
    """
    import time as _time

    d = _make_device_via_new()
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    d._start_track_attempt(updated_at=1.0)

    # Construct PLAYING via real wrappers
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="T", source="test"
        ),
        updated_at=2.0,
    )
    d._begin_runtime_play_dispatch(updated_at=3.0)
    d._begin_runtime_confirmation(updated_at=4.0)
    d._confirm_runtime_playing(updated_at=5.0)
    assert d.get_runtime_state().phase == PlaybackPhase.PLAYING

    token = d._capture_lifecycle_token()
    state_before = d.get_runtime_state()

    result = d._confirm_runtime_playing_for_attempt(
        token=token, updated_at=_time.time()
    )

    assert result is True
    # State object unchanged ->?phase was already PLAYING, guard is idempotent
    assert d.get_runtime_state() is state_before


# ══════════════════════════════════════════════════════════════════════════════->?
# T03-E1 external request / direct-fallback dispatch phase
# ══════════════════════════════════════════════════════════════════════════════->?


# ── helper unit tests ────────────────────────────────────────────────────────


def test_external_dispatch_helper_stale_token_false():
    """Strict token stale -> False."""
    d = _make_device_via_new()
    token = d._capture_lifecycle_token()
    d._accept_command(updated_at=1.0)  # stale
    assert d._begin_runtime_external_dispatch_for_token(token) is False


def test_external_dispatch_helper_resolving_to_dispatching_true():
    """RESOLVING -> DISPATCHING -> True."""
    d = _make_device_via_new()
    d._set_runtime_state(
        PlaybackRuntimeState(phase=PlaybackPhase.RESOLVING)
    )
    token = d._capture_lifecycle_token()
    assert d._begin_runtime_external_dispatch_for_token(token) is True
    assert d.get_runtime_state().phase == PlaybackPhase.DISPATCHING


def test_external_dispatch_helper_switching_to_dispatching_true():
    """SWITCHING -> DISPATCHING -> True."""
    d = _make_device_via_new()
    d._set_runtime_state(
        PlaybackRuntimeState(phase=PlaybackPhase.SWITCHING)
    )
    token = d._capture_lifecycle_token()
    assert d._begin_runtime_external_dispatch_for_token(token) is True
    assert d.get_runtime_state().phase == PlaybackPhase.DISPATCHING


def test_external_dispatch_helper_dispatching_idempotent_true():
    """DISPATCHING -> idempotent True, no phase change."""
    d = _make_device_via_new()
    d._set_runtime_state(
        PlaybackRuntimeState(phase=PlaybackPhase.DISPATCHING)
    )
    token = d._capture_lifecycle_token()
    assert d._begin_runtime_external_dispatch_for_token(token) is True
    assert d.get_runtime_state().phase == PlaybackPhase.DISPATCHING


def test_external_dispatch_helper_idle_false():
    """IDLE -> False."""
    d = _make_device_via_new()
    token = d._capture_lifecycle_token()
    assert d._begin_runtime_external_dispatch_for_token(token) is False


def test_external_dispatch_helper_playing_false():
    """PLAYING -> False."""
    d = _make_device_via_new()
    d._set_runtime_state(
        PlaybackRuntimeState(phase=PlaybackPhase.PLAYING)
    )
    token = d._capture_lifecycle_token()
    assert d._begin_runtime_external_dispatch_for_token(token) is False


def test_external_dispatch_helper_no_io_no_id_change():
    """Helper does not change lifecycle IDs."""
    d = _make_device_via_new()
    d._set_runtime_state(
        PlaybackRuntimeState(phase=PlaybackPhase.RESOLVING)
    )
    s0 = d.get_runtime_state()
    token = d._capture_lifecycle_token()
    d._begin_runtime_external_dispatch_for_token(token)
    s1 = d.get_runtime_state()
    assert s1.queue_session_id == s0.queue_session_id
    assert s1.command_generation == s0.command_generation
    assert s1.track_attempt_id == s0.track_attempt_id


# ── A: STOPPING拒绝零副作用 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_stopping_rejects_zero_side_effects_no_marker():
    """STOPPING phase: on_external_url_play returns None, no marker,
    q/c/sid/timer/legacy track all unchanged."""
    xm2 = _fake_xm()
    dev_real = Device(
        did="d1", device_id="d1", hardware="OH2P", name="T",
        play_type=PLAY_TYPE_ALL, cur_playlist="BGM", playlist2music={},
    )
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")

    # Pre-set STOPPING phase
    stopping = PlaybackRuntimeState(phase=PlaybackPhase.STOPPING)
    d._set_runtime_state(stopping)

    q_before = d.get_runtime_state().queue_session_id
    c_before = d.get_runtime_state().command_generation
    a_before = d.get_runtime_state().track_attempt_id
    sid_before = d._play_session_id
    playlist_before = d.device.cur_playlist
    cur_music_before = d.device.cur_music

    cancel_called = 0
    async def _noop_cancel():
        nonlocal cancel_called
        cancel_called += 1
    d.cancel_group_next_timer = _noop_cancel
    d._invalidate_manual_navigation = lambda reason: None

    ctx = {}
    result = await d.on_external_url_play(context=ctx)

    assert result is None
    assert not ctx  # empty ->?no marker written
    s = d.get_runtime_state()
    assert s.queue_session_id == q_before
    assert s.command_generation == c_before
    assert s.track_attempt_id == a_before
    assert s.phase == PlaybackPhase.STOPPING
    assert d._play_session_id == sid_before
    assert d.device.cur_playlist == playlist_before
    assert d.device.cur_music == cur_music_before
    assert cancel_called == 0


# ── B: IDLE首次external RESOLVING→DISPATCHING+attempt1 ──────────────────


@pytest.mark.asyncio
async def test_b_idle_first_external_resolving_then_dispatching_attempt1():
    """IDLE first external: in cancel await phase=RESOLVING q=1,c=1;
    after release, group entry sees DISPATCHING + attempt=1."""
    from xiaomusic.xiaomusic import XiaoMusic

    cancel_entered = asyncio.Event()
    cancel_release = asyncio.Event()
    group_entered = asyncio.Event()
    group_release = asyncio.Event()

    xm2 = _fake_xm()
    dev_real = Device(
        did="d1", device_id="d1", hardware="OH2P", name="T",
        play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={},
    )
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d._invalidate_manual_navigation = lambda reason: None

    async def _block_cancel():
        cancel_entered.set()
        await cancel_release.wait()
    d.cancel_group_next_timer = _block_cancel

    phase_at_group = None
    attempt_at_group = 0

    async def _spy_group(url):
        nonlocal phase_at_group, attempt_at_group
        group_entered.set()
        phase_at_group = d.get_runtime_state().phase
        attempt_at_group = d.get_runtime_state().track_attempt_id
        await group_release.wait()
        return [{"code": 0}]
    d.group_player_play = _spy_group
    d.on_external_url_play_started = _noop_play_started

    xm = XiaoMusic.__new__(XiaoMusic)
    xm.log = logging.getLogger("test")
    xm.device_manager = types.SimpleNamespace(devices={"d1": d})

    task = asyncio.create_task(
        XiaoMusic.play_url(xm, did="d1", arg1="http://x/a.mp3")
    )
    try:
        await asyncio.wait_for(cancel_entered.wait(), timeout=5.0)

        s = d.get_runtime_state()
        assert s.phase == PlaybackPhase.RESOLVING
        assert s.queue_session_id == 1
        assert s.command_generation == 1
        assert s.track_attempt_id == 0

        cancel_release.set()
        await asyncio.wait_for(group_entered.wait(), timeout=5.0)

        assert phase_at_group == PlaybackPhase.DISPATCHING
        assert attempt_at_group == 1

        group_release.set()
        await asyncio.wait_for(task, timeout=5.0)
    finally:
        if not task.done():
            cancel_release.set()
            group_release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# ── C: PLAYING新external先SWITCHING后dispatch ────────────────────────────


@pytest.mark.asyncio
async def test_c_playing_new_external_switching_then_dispatch():
    """Pre-set PLAYING: on_external_url_play enters SWITCHING;
    then dispatch transitions to DISPATCHING."""
    from xiaomusic.xiaomusic import XiaoMusic

    group_entered = asyncio.Event()
    group_release = asyncio.Event()

    xm2 = _fake_xm()
    dev_real = Device(
        did="d1", device_id="d1", hardware="OH2P", name="T",
        play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={},
    )
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop
    d._invalidate_manual_navigation = lambda reason: None

    # Pre-set PLAYING via real wrappers
    old_confirmed = TrackReference(
        entity_id="old_e", display_name="old", source="legacy"
    )
    playing_state = PlaybackRuntimeState(
        phase=PlaybackPhase.PLAYING,
        confirmed_track=old_confirmed,
        desired_track=old_confirmed,
        updated_at=1.0,
    )
    d._set_runtime_state(playing_state)

    # Call on_external_url_play ->?should enter SWITCHING
    token = await d.on_external_url_play(context={})
    assert token is not None
    s = d.get_runtime_state()
    assert s.phase == PlaybackPhase.SWITCHING
    assert s.confirmed_track is old_confirmed

    # Now dispatch via play_url flow
    phase_at_group = None

    async def _spy_group(url):
        nonlocal phase_at_group
        group_entered.set()
        phase_at_group = d.get_runtime_state().phase
        await group_release.wait()
        return [{"code": 0}]
    d.group_player_play = _spy_group
    d.on_external_url_play_started = _noop_play_started

    xm = XiaoMusic.__new__(XiaoMusic)
    xm.log = logging.getLogger("test")
    xm.device_manager = types.SimpleNamespace(devices={"d1": d})

    task = asyncio.create_task(
        XiaoMusic.play_url(xm, did="d1", arg1="http://x/a.mp3")
    )
    try:
        await asyncio.wait_for(group_entered.wait(), timeout=5.0)
        assert phase_at_group == PlaybackPhase.DISPATCHING
        group_release.set()
        await asyncio.wait_for(task, timeout=5.0)
    finally:
        if not task.done():
            group_release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# ── D: 同context direct fail→fallback ────────────────────────────────────


@pytest.mark.asyncio
async def test_d_same_context_direct_fail_fallback_two_attempts():
    """Same context dict: q=1,c=1, a=2, phase stays DISPATCHING.
    Second call does NOT repeat request/q/c."""
    from xiaomusic.xiaomusic import XiaoMusic

    attempts_seen = []
    phases_seen = []
    dispatches = []
    first_group_called = asyncio.Event()
    second_dispatch_done = asyncio.Event()

    xm2 = _fake_xm()
    dev_real = Device(
        did="d1", device_id="d1", hardware="OH2P", name="T",
        play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={},
    )
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None
    d._bootstrap_playlist_session_for_external_url = lambda context: None

    async def _spy_group(url):
        dispatches.append(url)
        attempts_seen.append(d.get_runtime_state().track_attempt_id)
        phases_seen.append(d.get_runtime_state().phase)
        if len(dispatches) == 1:
            first_group_called.set()
            return [None]  # direct fails
        second_dispatch_done.set()
        return [{"code": 0}]  # fallback succeeds
    d.group_player_play = _spy_group

    async def _on_started(context=None, resolved=None, *, token):
        pass
    d.on_external_url_play_started = _on_started

    xm = XiaoMusic.__new__(XiaoMusic)
    xm.log = logging.getLogger("test")
    xm.device_manager = types.SimpleNamespace(devices={"d1": d})

    ctx = {}

    try:
        # First call -> direct
        r1 = await XiaoMusic.play_url(xm, did="d1", arg1="http://x/direct.mp3", context=ctx)
        assert r1["accepted"] is True

        # Yield to let first executor run before second submit
        await asyncio.wait_for(first_group_called.wait(), timeout=5.0)

        # Second call -> fallback, same context (reuses q/c)
        r2 = await XiaoMusic.play_url(xm, did="d1", arg1="http://x/fallback.mp3", context=ctx)
        assert r2["accepted"] is True

        await asyncio.wait_for(second_dispatch_done.wait(), timeout=5.0)
    finally:
        await d.close_command_arbiter()

    s = d.get_runtime_state()
    assert s.queue_session_id == 1
    assert s.command_generation == 1
    assert s.track_attempt_id == 2
    assert s.phase == PlaybackPhase.DISPATCHING
    assert len(dispatches) == 2
    assert attempts_seen == [1, 2]
    # Phase was DISPATCHING for both attempts
    assert all(p == PlaybackPhase.DISPATCHING for p in phases_seen)


# ── E: cancel await (T04-C2c arbiter-based) ──────────────────────────────


@pytest.mark.asyncio
async def test_e_cancel_await_stale_command_blocks_old_init():
    """New command during cancel await makes old init stale.
    Uses submit_external_url_play + arbiter.  Staleness check inside
    on_external_url_play; group never called."""
    cancel_entered = asyncio.Event()
    cancel_release = asyncio.Event()
    group_called = False
    executor_done = asyncio.Event()

    xm2 = _fake_xm()
    dev_real = Device(
        did="d1", device_id="d1", hardware="OH2P", name="T",
        play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={},
    )
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d._invalidate_manual_navigation = lambda reason: None
    d._bootstrap_playlist_session_for_external_url = lambda context: None

    async def _block_cancel():
        cancel_entered.set()
        await cancel_release.wait()
    d.cancel_group_next_timer = _block_cancel

    async def _spy_group(url):
        nonlocal group_called
        group_called = True
        return [{"code": 0}]
    d.group_player_play = _spy_group

    _orig_executor = d._execute_external_play_intent
    async def _spy_executor(payload):
        try:
            await _orig_executor(payload)
        finally:
            executor_done.set()
    d._execute_external_play_intent = _spy_executor

    try:
        receipt = await d.submit_external_url_play(url="http://x/a.mp3")
        assert receipt["accepted"] is True

        await asyncio.wait_for(cancel_entered.wait(), timeout=5.0)

        # New command accepted during cancel await -> stale
        d._accept_command(updated_at=999.0)

        cancel_release.set()

        # Wait for executor to finish
        await asyncio.wait_for(executor_done.wait(), timeout=5.0)

        assert not group_called
    finally:
        cancel_release.set()
        await d.close_command_arbiter()


# ── E2: stale context retry (T04-C2c) ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue"])
async def test_e2_stale_context_cannot_rebind(mutation_fn):
    """First call stale during cancel; retry with same context fails.
    q/c mismatch with pinned token -> None, no dispatch, attempt unchanged.
    Tests on_external_url_play directly via background task."""
    xm2 = _fake_xm()
    dev_real = Device(
        did="d1", device_id="d1", hardware="OH2P", name="T",
        play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={},
    )
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d._invalidate_manual_navigation = lambda reason: None

    a_before = d.get_runtime_state().track_attempt_id

    # First call: stale during cancel — run as task
    cancel_entered = asyncio.Event()
    cancel_release = asyncio.Event()

    async def _block_cancel():
        cancel_entered.set()
        await cancel_release.wait()
    d.cancel_group_next_timer = _block_cancel

    ctx = {}
    task = asyncio.create_task(d.on_external_url_play(context=ctx))

    try:
        await asyncio.wait_for(cancel_entered.wait(), timeout=5.0)

        if mutation_fn == "command":
            d._accept_command(updated_at=999.0)
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=999.0)

        cancel_release.set()
        token1 = await asyncio.wait_for(task, timeout=5.0)
    finally:
        if not task.done():
            cancel_release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # First call returns None (stale)
    assert token1 is None

    # Second call: same context -> must reject
    d.cancel_group_next_timer = _noop_coro
    token2 = await d.on_external_url_play(context=ctx)
    assert token2 is None  # rejected due to q/c mismatch
    assert d.get_runtime_state().track_attempt_id == a_before


# ── E3: forged marker -> rejected ──────────────────────────────────────


@pytest.mark.asyncio
async def test_e3_forged_marker_no_lifecycle_token_rejected():
    """Context with boolean marker only (no pinned LifecycleToken)
    is rejected: on_external_url_play returns None."""
    xm2 = _fake_xm()
    dev_real = Device(
        did="d1", device_id="d1", hardware="OH2P", name="T",
        play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={},
    )
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None

    ctx = {"_device_queue_session_initialized": True}  # forged
    q_before = d.get_runtime_state().queue_session_id
    c_before = d.get_runtime_state().command_generation

    token = await d.on_external_url_play(context=ctx)
    assert token is None

    # No lifecycle bumps
    assert d.get_runtime_state().queue_session_id == q_before
    assert d.get_runtime_state().command_generation == c_before


# ── E4: forged dict as token -> rejected ────────────────────────────────


@pytest.mark.asyncio
async def test_e4_forged_dict_as_token_rejected():
    """Context with marker and a plain dict (not LifecycleToken)
    is rejected: on_external_url_play returns None."""
    xm2 = _fake_xm()
    dev_real = Device(
        did="d1", device_id="d1", hardware="OH2P", name="T",
        play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={},
    )
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None

    ctx = {
        "_device_queue_session_initialized": True,
        "_device_runtime_pinned_token": {"forged": True},
    }
    q_before = d.get_runtime_state().queue_session_id
    c_before = d.get_runtime_state().command_generation

    token = await d.on_external_url_play(context=ctx)
    assert token is None

    assert d.get_runtime_state().queue_session_id == q_before
    assert d.get_runtime_state().command_generation == c_before


# ── E2a/b/c: full pipeline (T04-C2c arbiter-based) ──────────────────────


@pytest.mark.asyncio
async def test_e2_a_direct_success_playing_confirmed_track():
    """Direct dispatch success: phase PLAYING, confirmed_track set.
    Uses submit_external_url_play + arbiter."""
    xm2 = _fake_xm()
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None
    d._bootstrap_playlist_session_for_external_url = lambda context: None

    async def _spy_group(url):
        return [{"code": 0}]
    d.group_player_play = _spy_group

    started_context = {}
    started_done = asyncio.Event()

    async def _on_started_e2_a(context=None, resolved=None, *, token):
        assert isinstance(token, LifecycleToken)
        started_context["called"] = True
        started_context["context"] = context
        started_context["resolved"] = resolved
        started_context["token"] = token
        await XiaoMusicDevice.on_external_url_play_started(
            d, context=context, resolved=resolved, token=token,
        )
        started_done.set()
    d.on_external_url_play_started = _on_started_e2_a

    try:
        receipt = await d.submit_external_url_play(
            url="http://x/a.mp3",
            context={"title": "Test Song"},
            resolved={"title": "Test Song", "entity_id": "eid-1", "playlist_item_id": "pid-1"},
        )
        assert receipt["accepted"] is True

        await asyncio.wait_for(started_done.wait(), timeout=5.0)
    finally:
        await d.close_command_arbiter()

    assert started_context.get("called")
    assert started_context["token"] is not None

    s = d.get_runtime_state()
    assert s.phase == PlaybackPhase.PLAYING
    assert s.confirmed_track is not None
    assert s.confirmed_track.display_name == "Test Song"
    assert s.confirmed_track.entity_id == "eid-1"
    assert s.confirmed_track.playlist_item_id == "pid-1"
    assert s.confirmed_track.source == "external"
    assert s.desired_track is not None
    assert s.desired_track.display_name == "Test Song"
    assert s.failure is None
    assert s.queue_session_id == 1
    assert s.command_generation == 1
    assert s.track_attempt_id == 1


@pytest.mark.asyncio
async def test_e2_b_direct_all_none_phase_dispatching_no_callback():
    """Direct dispatch returns all-None: phase stays DISPATCHING,
    on_external_url_play_started NOT called."""
    xm2 = _fake_xm()
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None
    d._bootstrap_playlist_session_for_external_url = lambda context: None

    async def _spy_group(url):
        return [None]
    d.group_player_play = _spy_group

    started_called = False
    executor_done = asyncio.Event()

    async def _on_started_e2_b(context=None, resolved=None, *, token):
        nonlocal started_called
        started_called = True
    d.on_external_url_play_started = _on_started_e2_b

    _orig_exec_b = d._execute_external_play_intent
    async def _spy_exec_b(payload):
        try:
            await _orig_exec_b(payload)
        finally:
            executor_done.set()
    d._execute_external_play_intent = _spy_exec_b

    try:
        receipt = await d.submit_external_url_play(url="http://x/a.mp3")
        assert receipt["accepted"] is True

        await asyncio.wait_for(executor_done.wait(), timeout=5.0)
    finally:
        await d.close_command_arbiter()

    assert not started_called
    s = d.get_runtime_state()
    assert s.phase == PlaybackPhase.DISPATCHING
    assert s.track_attempt_id == 1
    assert s.queue_session_id == 1
    assert s.command_generation == 1


@pytest.mark.asyncio
async def test_e2_c_fallback_success_attempt_two_confirmed_fallback():
    """Two external submits: first all-None, second succeeds.
    Each submit bumps c; each physical dispatch bumps q/a.
    Second dispatch: q=2, c=2, a=2, PLAYING confirmed_track from fallback."""
    xm2 = _fake_xm()
    dev_real = Device(did="d1", device_id="d1", hardware="OH2P", name="T",
                      play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
    d = XiaoMusicDevice(xm2, dev_real, group_name="g")
    d.cancel_group_next_timer = _noop_coro
    d._invalidate_manual_navigation = lambda reason: None
    d._bootstrap_playlist_session_for_external_url = lambda context: None

    call_count = 0
    group_called = asyncio.Event()

    async def _spy_group(url):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            group_called.set()
            return [None]
        return [{"code": 0}]
    d.group_player_play = _spy_group

    started_calls = []
    started_done_c = asyncio.Event()

    async def _on_started_e2_c(context=None, resolved=None, *, token):
        assert isinstance(token, LifecycleToken)
        started_calls.append((context, resolved, token))
        await XiaoMusicDevice.on_external_url_play_started(
            d, context=context, resolved=resolved, token=token,
        )
        started_done_c.set()
    d.on_external_url_play_started = _on_started_e2_c

    try:
        r1 = await d.submit_external_url_play(
            url="http://x/a.mp3",
            resolved={"title": "Direct Fail"},
        )
        assert r1["accepted"] is True
        # Wait for first group dispatch
        await asyncio.wait_for(group_called.wait(), timeout=5.0)

        assert call_count == 1
        assert len(started_calls) == 0

        s1 = d.get_runtime_state()
        assert s1.phase == PlaybackPhase.DISPATCHING
        assert s1.track_attempt_id == 1

        r2 = await d.submit_external_url_play(
            url="http://x/b.mp3",
            resolved={"title": "Fallback Win", "entity_id": "eid-fb"},
        )
        assert r2["accepted"] is True
        await asyncio.wait_for(started_done_c.wait(), timeout=5.0)
    finally:
        await d.close_command_arbiter()

    assert len(started_calls) == 1
    assert call_count == 2

    s2 = d.get_runtime_state()
    assert s2.phase == PlaybackPhase.PLAYING
    assert s2.queue_session_id == 2
    assert s2.command_generation == 2
    assert s2.track_attempt_id == 2
    assert s2.confirmed_track is not None
    assert s2.confirmed_track.display_name == "Fallback Win"
    assert s2.confirmed_track.entity_id == "eid-fb"
    assert s2.failure is None


# ── D: stale guard after volume refresh ─────────────────────────────# ── D: stale guard after volume refresh ─────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt", "sid"])
async def test_e2_d_refresh_volume_stale_skips_duration_event_timer(mutation_fn):
    """After volume refresh: mutation -> stale -> no _duration write,
    no event, no timer, returns False."""
    import time as _time

    d = _make_device_via_new()
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            get_music_duration=lambda name: _noop(result=123.0),
        ),
    )
    d._play_session_id = 1
    d.event_bus = None
    d.device = types.SimpleNamespace(
        cur_music="", cur_playlist="全部",
        current_entity_id="", current_playlist_item_id="",
    )
    d._play_list_items = []
    d._current_index = -1
    d._duration = 0
    d.is_playing = False
    d._start_time = 0
    d._paused_time = 0
    d._last_cmd = ""
    d._duration_probe_task = None
    d._next_timer = None

    from xiaomusic.playback.runtime_state import begin_dispatch, begin_play_request
    d._set_runtime_state(
        begin_dispatch(
            begin_play_request(
                d.get_runtime_state(),
                desired_track=TrackReference(
                    display_name="s", entity_id="e", playlist_item_id="p",
                ),
                updated_at=_time.time(),
            ),
            updated_at=_time.time(),
        )
    )
    token = d._start_track_attempt(updated_at=_time.time())

    # ── block volume refresh, then mutate ───────────────────────────
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _block_volume(context=""):
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        return 50
    d._refresh_runtime_volume = _block_volume

    timer_called = False

    async def _set_timer(sec, token=None):
        nonlocal timer_called
        timer_called = True
    d.set_next_music_timeout = _set_timer

    task = asyncio.create_task(
        d.on_external_url_play_started(
            context={}, resolved={"title": "song", "duration": 200},
            token=token,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)

        if mutation_fn == "command":
            d._accept_command(updated_at=999.0)
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=999.0)
        elif mutation_fn == "attempt":
            d._start_track_attempt(updated_at=999.0)
        elif mutation_fn == "sid":
            d._bump_play_session(reason="test")

        release.set()
        result = await task

        assert result is False
        assert d._duration == 0
        assert not timer_called
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# ── E: stale guard after duration fetch ────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt", "sid"])
async def test_e2_e_duration_fetch_stale_no_duration_event_timer(mutation_fn):
    """After music duration fetch: mutation -> stale -> no _duration,
    no event, no timer, returns False."""
    import time as _time

    d = _make_device_via_new()
    d._play_session_id = 1
    d.event_bus = None
    d.device = types.SimpleNamespace(
        cur_music="", cur_playlist="全部",
        current_entity_id="", current_playlist_item_id="",
    )
    d._play_list_items = []
    d._current_index = -1
    d._duration = 0
    d.is_playing = False
    d._start_time = 0
    d._paused_time = 0
    d._last_cmd = ""
    d._duration_probe_task = None
    d._next_timer = None

    from xiaomusic.playback.runtime_state import begin_dispatch, begin_play_request
    d._set_runtime_state(
        begin_dispatch(
            begin_play_request(
                d.get_runtime_state(),
                desired_track=TrackReference(
                    display_name="s", entity_id="e", playlist_item_id="p",
                ),
                updated_at=_time.time(),
            ),
            updated_at=_time.time(),
        )
    )
    token = d._start_track_attempt(updated_at=_time.time())

    async def _vol(context=""):
        return 50
    d._refresh_runtime_volume = _vol

    entered = asyncio.Event()
    release = asyncio.Event()

    class _FakeML:
        async def get_music_duration(self, name):
            entered.set()
            await asyncio.wait_for(release.wait(), timeout=5)
            return 123.0
    d.xiaomusic = types.SimpleNamespace(music_library=_FakeML())

    timer_called = False

    async def _set_timer(sec, token=None):
        nonlocal timer_called
        timer_called = True
    d.set_next_music_timeout = _set_timer

    _duration_before = d._duration

    task = asyncio.create_task(
        d.on_external_url_play_started(
            context={}, resolved={"title": "song"},
            token=token,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)

        if mutation_fn == "command":
            d._accept_command(updated_at=999.0)
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=999.0)
        elif mutation_fn == "attempt":
            d._start_track_attempt(updated_at=999.0)
        elif mutation_fn == "sid":
            d._bump_play_session(reason="test")

        release.set()
        result = await task

        assert result is False
        assert d._duration == _duration_before
        assert not timer_called
    finally:
        if not task.done():
            release.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# ── F: duration <= 0.1 -> probe inherits same token ─────────────────

@pytest.mark.asyncio
async def test_e2_f_duration_zero_probe_receives_token():
    """duration_hint <= 0.1: _start_duration_probe called with same token."""
    import time as _time

    d = _make_device_via_new()
    d._play_session_id = 1
    d.event_bus = None
    d.device = types.SimpleNamespace(
        cur_music="", cur_playlist="全部",
        current_entity_id="", current_playlist_item_id="",
    )
    d._play_list_items = []
    d._current_index = -1
    d._duration = 0
    d.is_playing = False
    d._start_time = 0
    d._paused_time = 0
    d._last_cmd = ""
    d._duration_probe_task = None
    d._next_timer = None
    d._duration_probe_task = None
    d.config = types.SimpleNamespace(delay_sec=0, verbose=False)

    from xiaomusic.playback.runtime_state import begin_dispatch, begin_play_request
    d._set_runtime_state(
        begin_dispatch(
            begin_play_request(
                d.get_runtime_state(),
                desired_track=TrackReference(
                    display_name="s", entity_id="e", playlist_item_id="p",
                ),
                updated_at=_time.time(),
            ),
            updated_at=_time.time(),
        )
    )
    token = d._start_track_attempt(updated_at=_time.time())

    async def _vol(context=""):
        return 50
    d._refresh_runtime_volume = _vol

    probe_captured = {"name": None, "sid": None, "token": None}

    def _fake_probe(name, sid, token=None):
        probe_captured["name"] = name
        probe_captured["sid"] = sid
        probe_captured["token"] = token
    d._start_duration_probe = _fake_probe

    result = await d.on_external_url_play_started(
        context={},
        resolved={"title": "short-song", "duration": 0.0},
        token=token,
    )

    assert result is True
    assert probe_captured["name"] == "short-song"
    assert probe_captured["sid"] == 1
    assert probe_captured["token"] is token


# ── G: duration normal -> timer inherits same token ──────────────────

@pytest.mark.asyncio
async def test_e2_g_duration_normal_timer_receives_token():
    """duration > 0.1: set_next_music_timeout called with same token."""
    import time as _time

    d = _make_device_via_new()
    d._play_session_id = 1
    d.event_bus = None
    d.device = types.SimpleNamespace(
        cur_music="", cur_playlist="全部",
        current_entity_id="", current_playlist_item_id="",
    )
    d._play_list_items = []
    d._current_index = -1
    d._duration = 0
    d.is_playing = False
    d._start_time = 0
    d._paused_time = 0
    d._last_cmd = ""
    d._duration_probe_task = None
    d._next_timer = None
    d._duration_probe_task = None
    d.config = types.SimpleNamespace(delay_sec=0, verbose=False)

    from xiaomusic.playback.runtime_state import begin_dispatch, begin_play_request
    d._set_runtime_state(
        begin_dispatch(
            begin_play_request(
                d.get_runtime_state(),
                desired_track=TrackReference(
                    display_name="s", entity_id="e", playlist_item_id="p",
                ),
                updated_at=_time.time(),
            ),
            updated_at=_time.time(),
        )
    )
    token = d._start_track_attempt(updated_at=_time.time())

    async def _vol(context=""):
        return 50
    d._refresh_runtime_volume = _vol
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            get_music_duration=lambda name: _noop(result=200.0),
        ),
    )

    timer_captured = {"sec": None, "token": None}
    d.cancel_next_timer = lambda: None

    async def _fake_set_timer(sec, token=None):
        timer_captured["sec"] = sec
        timer_captured["token"] = token
    d.set_next_music_timeout = _fake_set_timer

    result = await d.on_external_url_play_started(
        context={},
        resolved={"title": "long-song", "duration": 200},
        token=token,
    )

    assert result is True
    assert timer_captured["sec"] is not None
    assert timer_captured["token"] is token
    assert d._duration == 200.0


# ══════════════════════════════════════════════════════════════════════->?
# T03-F: pause / stop runtime phase wiring ->?real integration tests A–G
# ══════════════════════════════════════════════════════════════════════->?


# ── helpers ───────────────────────────────────────────────────────────

def _make_playing_device_for_pause_stop() -> XiaoMusicDevice:
    """Build device in PLAYING phase via real wrapper/reducer chain.

    IDLE -> RESOLVING -> DISPATCHING -> CONFIRMING -> PLAYING.
    confirmed_track = desired_track, expected_end_at = 180.0.
    """
    d = _make_device_via_new()
    d._invalidate_manual_navigation = lambda reason: None
    d.do_tts = lambda v: None
    d._command_arbiter = None
    d._manual_nav_lock = asyncio.Lock()
    d._manual_nav_generation = 0
    d._manual_nav_target = None
    d._ensure_manual_navigation_state = lambda: None
    desired = TrackReference(
        entity_id="e1", display_name="test-song", source="test"
    )
    d._begin_runtime_play_request(
        desired_track=desired, updated_at=time.time()
    )
    d._begin_runtime_play_dispatch(updated_at=time.time())
    d._begin_runtime_confirmation(updated_at=time.time())
    d._confirm_runtime_playing(expected_end_at=180.0, updated_at=time.time())
    return d


# T03-F: pause / stop runtime phase wiring (adapted for deferred executor)


@pytest.mark.asyncio
async def test_a_playing_pause_visible_paused_during_cancel_await():
    """A: pause() returns True with PAUSED phase; physical work in arbiter.

    command+1, sid+1, group once, event. Adapted for deferred executor."""
    cancel_entered = asyncio.Event()
    cancel_release = asyncio.Event()

    d = _make_playing_device_for_pause_stop()

    async def _block_cancel():
        cancel_entered.set()
        await asyncio.wait_for(cancel_release.wait(), timeout=5.0)

    d.cancel_group_next_timer = _block_cancel

    group_calls = 0
    async def _spy_group(fast=False):
        nonlocal group_calls
        group_calls += 1
        return []
    d.group_force_stop_xiaoai = _spy_group

    events = []
    event_done = asyncio.Event()
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
            event_done.set()
    d.event_bus = _Bus()

    c_before = d.get_runtime_state().command_generation
    sid_before = d._play_session_id

    try:
        # pause() returns immediately with PAUSED phase
        result = await d.pause()
        assert result is True
        s = d.get_runtime_state()
        assert s.phase == PlaybackPhase.PAUSED, f"expected PAUSED, got {s.phase}"
        assert s.command_generation == c_before + 1
        assert d._play_session_id == sid_before + 1
        assert d.is_playing is False

        # Arbiter executor starts; blocks on cancel
        await asyncio.wait_for(cancel_entered.wait(), timeout=5.0)

        # During cancel await: phase already PAUSED (set by acceptance)
        s = d.get_runtime_state()
        assert s.phase == PlaybackPhase.PAUSED

        # Release cancel; executor proceeds to group_stop and event
        cancel_release.set()
        await asyncio.wait_for(event_done.wait(), timeout=5.0)

        assert group_calls == 1
        assert PLAYER_STATE_CHANGED in events
    finally:
        cancel_release.set()
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_b_pause_cancel_await_new_barrier_sid_stale_no_group():
    """B: pause accepted, but new barrier bumps sid before executor runs.

    Old pause executor detects stale (sid mismatch) -> no group/event.
    Adapted for barrier guard: sid bump kills old barrier; command-only
    bump does NOT (handled in separate barrier-persists test)."""
    cancel_entered = asyncio.Event()
    cancel_release = asyncio.Event()

    d = _make_playing_device_for_pause_stop()

    async def _block_cancel():
        cancel_entered.set()
        await asyncio.wait_for(cancel_release.wait(), timeout=5.0)

    d.cancel_group_next_timer = _block_cancel

    group_calls = 0
    async def _spy_group(fast=False):
        nonlocal group_calls
        group_calls += 1
        return []
    d.group_force_stop_xiaoai = _spy_group

    events = []
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
    d.event_bus = _Bus()

    try:
        # Wrap executor to signal completion
        exec_done_e = asyncio.Event()
        _orig_exec_e = d._execute_pause_intent
        async def _spy_exec_e(payload):
            try:
                await _orig_exec_e(payload)
            finally:
                exec_done_e.set()
        d._execute_pause_intent = _spy_exec_e

        # pause() returns immediately
        result = await d.pause()
        assert result is True
        assert d.get_runtime_state().phase == PlaybackPhase.PAUSED

        # Arbiter executor starts; blocks on cancel
        await asyncio.wait_for(cancel_entered.wait(), timeout=5.0)

        # Simulate new barrier arriving: bump sid (new STOP/PAUSE)
        d._bump_play_session(reason="new_barrier")

        # Release cancel; executor detects barrier stale (sid mismatch)
        cancel_release.set()
        await asyncio.wait_for(exec_done_e.wait(), timeout=5.0)

        assert group_calls == 0
        assert PLAYER_STATE_CHANGED not in events
        # Phase stays PAUSED (from acceptance)
        assert d.get_runtime_state().phase == PlaybackPhase.PAUSED
    finally:
        cancel_release.set()
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_c_idle_pause_zero_writes_no_side_effects():
    """C: IDLE pause zero lifecycle writes, returns False, no arbiter."""
    d = _make_device_via_new()
    d._invalidate_manual_navigation = lambda reason: None

    cancel_calls = 0
    async def _spy_cancel():
        nonlocal cancel_calls
        cancel_calls += 1
    d.cancel_group_next_timer = _spy_cancel

    group_calls = 0
    async def _spy_group(fast=False):
        nonlocal group_calls
        group_calls += 1
        return []
    d.group_force_stop_xiaoai = _spy_group

    events = []
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
    d.event_bus = _Bus()

    c_before = d.get_runtime_state().command_generation
    q_before = d.get_runtime_state().queue_session_id
    a_before = d.get_runtime_state().track_attempt_id
    sid_before = d._play_session_id
    is_playing_before = d.is_playing

    result = await d.pause()

    assert result is False
    assert d.get_runtime_state().command_generation == c_before
    assert d.get_runtime_state().queue_session_id == q_before
    assert d.get_runtime_state().track_attempt_id == a_before
    assert d._play_session_id == sid_before
    assert d.is_playing == is_playing_before
    # No arbiter created (no submit on False)
    assert d._command_arbiter is None
    assert cancel_calls == 0
    assert group_calls == 0
    assert PLAYER_STATE_CHANGED not in events


@pytest.mark.asyncio
async def test_d_playing_stop_stopping_visible_during_group_await():
    """D: stop() returns with STOPPING phase; after executor: STOPPED.

    Adapted for deferred executor: stop accepts immediately, physical
    work completes later."""
    group_entered = asyncio.Event()
    group_release = asyncio.Event()

    d = _make_playing_device_for_pause_stop()

    confirmed_before = d.get_runtime_state().confirmed_track
    assert confirmed_before is not None
    assert d.get_runtime_state().expected_end_at == 180.0

    async def _block_group(fast=False):
        group_entered.set()
        await asyncio.wait_for(group_release.wait(), timeout=5.0)
        return []

    async def _noop_cancel():
        pass

    d.group_force_stop_xiaoai = _block_group
    d.cancel_group_next_timer = _noop_cancel

    event_done = asyncio.Event()
    events = []
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
            event_done.set()
    d.event_bus = _Bus()

    try:
        # stop() returns immediately with STOPPING phase
        result = await d.stop(arg1="notts")
        assert result is True
        s = d.get_runtime_state()
        assert s.phase == PlaybackPhase.STOPPING, f"expected STOPPING, got {s.phase}"

        # Arbiter starts; enters group_stop
        await asyncio.wait_for(group_entered.wait(), timeout=5.0)

        # During group await: phase is STOPPING
        s = d.get_runtime_state()
        assert s.phase == PlaybackPhase.STOPPING

        # Release; executor completes -> STOPPED + event
        group_release.set()
        await asyncio.wait_for(event_done.wait(), timeout=5.0)

        s = d.get_runtime_state()
        assert s.phase == PlaybackPhase.STOPPED
        # confirmed_track preserved
        assert s.confirmed_track is not None
        assert s.confirmed_track.entity_id == confirmed_before.entity_id
        assert s.confirmed_track.display_name == confirmed_before.display_name
        # expected_end_at cleared
        assert s.expected_end_at is None
        assert PLAYER_STATE_CHANGED in events
    finally:
        group_release.set()
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_e_stopping_repeat_stop_old_stale_new_completes():
    """E: STOPPING repeat stop; newer command token takes over.

    First stop accepted -> executor blocks on group. Second stop accepted
    (idempotent STOPPING) -> arbiter pending replaces first. When first
    executor finishes group, token is stale -> skips complete. Second
    executor runs and completes -> STOPPED + event."""
    group_release = asyncio.Event()

    d = _make_playing_device_for_pause_stop()

    call_count = 0
    entered1 = asyncio.Event()
    entered2 = asyncio.Event()

    async def _block_group(fast=False):
        nonlocal call_count
        cnt = call_count
        call_count += 1
        if cnt == 0:
            entered1.set()
        elif cnt == 1:
            entered2.set()
        await asyncio.wait_for(group_release.wait(), timeout=5.0)
        return []

    async def _noop_cancel():
        pass

    d.group_force_stop_xiaoai = _block_group
    d.cancel_group_next_timer = _noop_cancel

    event_done = asyncio.Event()
    events = []
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
            event_done.set()
    d.event_bus = _Bus()

    try:
        # First stop: accepted, phase -> STOPPING, arbiter executor blocks on group
        result1 = await d.stop(arg1="notts")
        assert result1 is True
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING

        c_after_1 = d.get_runtime_state().command_generation

        await asyncio.wait_for(entered1.wait(), timeout=5.0)

        # Second stop from STOPPING: idempotent acceptance, new command bump
        result2 = await d.stop(arg1="notts")
        assert result2 is True
        assert d.get_runtime_state().command_generation == c_after_1 + 1

        # Release group; first executor finishes -> sees stale token -> skips complete
        # Arbiter then picks up second stop which enters group (entered2)
        group_release.set()

        # Wait for second stop's executor to enter group_stop
        await asyncio.wait_for(entered2.wait(), timeout=5.0)

        # Wait for second stop to complete (event fired)
        await asyncio.wait_for(event_done.wait(), timeout=5.0)

        assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
        # Only one event (from second stop)
        assert PLAYER_STATE_CHANGED in events
        assert call_count == 2  # both stops called group_stop
    finally:
        group_release.set()
        await d.close_command_arbiter()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [PlaybackPhase.IDLE, PlaybackPhase.STOPPED])
async def test_f_idle_stopped_stop_zero_writes(phase):
    """F: IDLE/STOPPED stop zero lifecycle writes, False, no arbiter."""
    d = _make_device_via_new()
    if phase == PlaybackPhase.STOPPED:
        # Real chain: IDLE->RESOLVING->DISPATCHING->CONFIRMING->PLAYING->STOPPING->STOPPED
        d._begin_runtime_play_request(
            desired_track=TrackReference(
                entity_id="e1", display_name="test-song", source="test"
            ),
            updated_at=time.time(),
        )
        d._begin_runtime_play_dispatch(updated_at=time.time())
        d._begin_runtime_confirmation(updated_at=time.time())
        d._confirm_runtime_playing(updated_at=time.time())
        d._begin_runtime_stop(updated_at=time.time())
        d._complete_runtime_stop(updated_at=time.time())
    # IDLE: device already starts in IDLE from _make_device_via_new
    d._invalidate_manual_navigation = lambda reason: None
    d.do_tts = lambda v: None

    cancel_calls = 0
    async def _spy_cancel():
        nonlocal cancel_calls
        cancel_calls += 1
    d.cancel_group_next_timer = _spy_cancel

    group_calls = 0
    async def _spy_group(fast=False):
        nonlocal group_calls
        group_calls += 1
        return []
    d.group_force_stop_xiaoai = _spy_group

    events = []
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
    d.event_bus = _Bus()

    c_before = d.get_runtime_state().command_generation
    q_before = d.get_runtime_state().queue_session_id
    sid_before = d._play_session_id

    result = await d.stop(arg1="notts")

    assert result is False
    assert d.get_runtime_state().command_generation == c_before
    assert d.get_runtime_state().queue_session_id == q_before
    assert d._play_session_id == sid_before
    # No arbiter created (no submit on False)
    assert d._command_arbiter is None
    assert cancel_calls == 0
    assert group_calls == 0
    assert PLAYER_STATE_CHANGED not in events


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt", "sid"])
async def test_g_pause_cancel_await_single_dimension_stale(mutation_fn):
    """G: pause accepted; bump lifecycle before executor runs cancel.

    barrier-guard: command bump does NOT stale the barrier;
    queue/attempt/sid bumps DO stale it."""
    cancel_entered = asyncio.Event()
    cancel_release = asyncio.Event()

    d = _make_playing_device_for_pause_stop()

    async def _block_cancel():
        cancel_entered.set()
        await asyncio.wait_for(cancel_release.wait(), timeout=5.0)

    d.cancel_group_next_timer = _block_cancel

    group_calls = 0
    async def _spy_group(fast=False):
        nonlocal group_calls
        group_calls += 1
        return []
    d.group_force_stop_xiaoai = _spy_group

    events = []
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
    d.event_bus = _Bus()

    try:
        # Wrap executor to signal completion
        pause_bar_done = asyncio.Event()
        _orig_pause_bar = d._execute_pause_intent
        async def _spy_pause_bar(payload):
            try:
                await _orig_pause_bar(payload)
            finally:
                pause_bar_done.set()
        d._execute_pause_intent = _spy_pause_bar

        # pause() returns immediately
        result = await d.pause()
        assert result is True
        assert d.get_runtime_state().phase == PlaybackPhase.PAUSED

        # Arbiter executor starts; enters cancel
        await asyncio.wait_for(cancel_entered.wait(), timeout=5.0)

        # Mutate a single lifecycle dimension
        if mutation_fn == "command":
            d._accept_command(updated_at=time.time())
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=time.time())
        elif mutation_fn == "attempt":
            from xiaomusic.playback.runtime_state import begin_track_attempt
            d._set_runtime_state(
                begin_track_attempt(d.get_runtime_state(), updated_at=time.time())
            )
        elif mutation_fn == "sid":
            d._bump_play_session(reason="test")

        # Release cancel; executor proceeds based on barrier guard
        cancel_release.set()
        await asyncio.wait_for(pause_bar_done.wait(), timeout=5.0)

        if mutation_fn == "command":
            # command-only bump does NOT stale the barrier → completes normally
            assert group_calls == 1
            assert PLAYER_STATE_CHANGED in events
        else:
            # queue/attempt/sid → barrier stale, skips group/event
            assert group_calls == 0
            assert PLAYER_STATE_CHANGED not in events
        # Phase stays PAUSED (set by acceptance)
        assert d.get_runtime_state().phase == PlaybackPhase.PAUSED
    finally:
        cancel_release.set()
        await d.close_command_arbiter()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt", "sid"])
async def test_g_stop_cancel_await_single_dimension_stale(mutation_fn):
    """G: stop accepted; bump lifecycle before executor runs cancel.

    barrier-guard: command bump does NOT stale the barrier;
    queue/attempt/sid bumps DO stale it."""
    cancel_entered = asyncio.Event()
    cancel_release = asyncio.Event()

    d = _make_playing_device_for_pause_stop()
    d.do_tts = lambda v: None

    async def _block_cancel():
        cancel_entered.set()
        await asyncio.wait_for(cancel_release.wait(), timeout=5.0)

    d.cancel_group_next_timer = _block_cancel

    async def _noop_group(fast=False):
        return []
    d.group_force_stop_xiaoai = _noop_group

    events = []
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
    d.event_bus = _Bus()

    try:
        # Wrap executor to signal completion
        stop_bar_done = asyncio.Event()
        _orig_stop_bar = d._execute_stop_intent
        async def _spy_stop_bar(payload):
            try:
                await _orig_stop_bar(payload)
            finally:
                stop_bar_done.set()
        d._execute_stop_intent = _spy_stop_bar

        # stop() returns immediately
        result = await d.stop(arg1="notts")
        assert result is True
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING

        # Arbiter executor starts; enters cancel
        await asyncio.wait_for(cancel_entered.wait(), timeout=5.0)

        # Mutate a single lifecycle dimension
        if mutation_fn == "command":
            d._accept_command(updated_at=time.time())
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=time.time())
        elif mutation_fn == "attempt":
            from xiaomusic.playback.runtime_state import begin_track_attempt
            d._set_runtime_state(
                begin_track_attempt(d.get_runtime_state(), updated_at=time.time())
            )
        elif mutation_fn == "sid":
            d._bump_play_session(reason="test")

        # Release cancel; executor proceeds based on barrier guard
        cancel_release.set()
        await asyncio.wait_for(stop_bar_done.wait(), timeout=5.0)

        if mutation_fn == "command":
            # command-only bump does NOT stale the barrier → completes normally
            assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
            assert PLAYER_STATE_CHANGED in events
        else:
            # queue/attempt/sid → barrier stale, no complete/event
            assert d.get_runtime_state().phase != PlaybackPhase.STOPPED
            assert d.get_runtime_state().phase == PlaybackPhase.STOPPING
            assert PLAYER_STATE_CHANGED not in events
    finally:
        cancel_release.set()
        await d.close_command_arbiter()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation_fn", ["command", "queue", "attempt", "sid"])
async def test_g_stop_group_await_single_dimension_stale(mutation_fn):
    """G: stop accepted; bump lifecycle during group_force_stop await.

    barrier-guard: command bump does NOT stale the barrier;
    queue/attempt/sid bumps DO stale it."""
    group_entered = asyncio.Event()
    group_release = asyncio.Event()

    d = _make_playing_device_for_pause_stop()
    d.do_tts = lambda v: None

    async def _noop_cancel():
        pass
    d.cancel_group_next_timer = _noop_cancel

    async def _block_group(fast=False):
        group_entered.set()
        await asyncio.wait_for(group_release.wait(), timeout=5.0)
        return []
    d.group_force_stop_xiaoai = _block_group

    events = []
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
    d.event_bus = _Bus()

    try:
        # Wrap executor to signal completion
        grp_bar_done = asyncio.Event()
        _orig_grp_bar = d._execute_stop_intent
        async def _spy_grp_bar(payload):
            try:
                await _orig_grp_bar(payload)
            finally:
                grp_bar_done.set()
        d._execute_stop_intent = _spy_grp_bar

        # stop() returns immediately
        result = await d.stop(arg1="notts")
        assert result is True
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING

        # Arbiter executor starts; enters group_stop
        await asyncio.wait_for(group_entered.wait(), timeout=5.0)

        # Mutate a single lifecycle dimension
        if mutation_fn == "command":
            d._accept_command(updated_at=time.time())
        elif mutation_fn == "queue":
            d._start_queue_session(updated_at=time.time())
        elif mutation_fn == "attempt":
            from xiaomusic.playback.runtime_state import begin_track_attempt
            d._set_runtime_state(
                begin_track_attempt(d.get_runtime_state(), updated_at=time.time())
            )
        elif mutation_fn == "sid":
            d._bump_play_session(reason="test")

        # Release group; executor proceeds based on barrier guard
        group_release.set()
        await asyncio.wait_for(grp_bar_done.wait(), timeout=5.0)

        if mutation_fn == "command":
            # command-only bump does NOT stale the barrier → completes normally
            assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
            assert PLAYER_STATE_CHANGED in events
        else:
            # queue/attempt/sid → barrier stale, no complete/event
            assert d.get_runtime_state().phase != PlaybackPhase.STOPPED
            assert d.get_runtime_state().phase == PlaybackPhase.STOPPING
            assert PLAYER_STATE_CHANGED not in events
    finally:
        group_release.set()
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_g_stop_group_await_queue_stale_no_complete():
    """stop accepted; queue_session_id bump during group_force_stop await.

    Executor sees stale token after group -> skips complete/event."""
    group_entered = asyncio.Event()
    group_release = asyncio.Event()

    d = _make_playing_device_for_pause_stop()
    d.do_tts = lambda v: None

    async def _noop_cancel():
        pass
    d.cancel_group_next_timer = _noop_cancel

    async def _block_group(fast=False):
        group_entered.set()
        await asyncio.wait_for(group_release.wait(), timeout=5.0)
        return []
    d.group_force_stop_xiaoai = _block_group

    events = []
    class _Bus:
        def publish(self, event_type, **kw):
            events.append(event_type)
    d.event_bus = _Bus()

    try:
        # Wrap executor to signal completion
        exec_done_g = asyncio.Event()
        _orig_exec_g = d._execute_stop_intent
        async def _spy_exec_g(payload):
            try:
                await _orig_exec_g(payload)
            finally:
                exec_done_g.set()
        d._execute_stop_intent = _spy_exec_g

        # stop() returns immediately
        result = await d.stop(arg1="notts")
        assert result is True
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING

        # Arbiter executor starts; enters group_stop
        await asyncio.wait_for(group_entered.wait(), timeout=5.0)

        # Bump only queue_session_id (not command_generation)
        d._start_queue_session(updated_at=time.time())

        # Release group; executor sees stale -> skips complete/event
        group_release.set()
        await asyncio.wait_for(exec_done_g.wait(), timeout=5.0)

        assert d.get_runtime_state().phase != PlaybackPhase.STOPPED
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING
        assert PLAYER_STATE_CHANGED not in events
    finally:
        group_release.set()
        await d.close_command_arbiter()

def _make_t04b_device(device_id="did-t04b"):
    """Build a lightweight device with arbiter-compatible mocks.

    Overrides ``_wait_manual_navigation_settle`` to Event-based settle
    and ``_play`` to a blocking mock for deterministic testing.
    """
    d = _make_device_via_new(device_id)
    d._command_arbiter = None
    d._manual_nav_lock = asyncio.Lock()
    d._manual_nav_generation = 0
    d._manual_nav_target = None
    d._ensure_manual_navigation_state = lambda: None
    return d


# ── T04-B: three-next burst ->?index+3, command+3, only final dispatch ──

@pytest.mark.asyncio
async def test_t04b_three_next_burst_index_plus_3_command_plus_3_one_dispatch():
    """Three next: _current_index +3, command +3, only final target dispatched."""
    d = _make_t04b_device()
    d._play_list_items = [
        {"display_name": n, "legacy_name": n, "item_id": "", "entity_id": ""}
        for n in ("A", "B", "C", "D", "E")
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )

    # Deterministic settle: release after all three intents are queued
    settle_barrier = asyncio.Event()
    d._wait_manual_navigation_settle = lambda: settle_barrier.wait()

    dispatched = []
    dispatch_done = asyncio.Event()

    async def _fake_play(name="", **kwargs):
        dispatched.append((kwargs.get("navigation_generation"), name))
        dispatch_done.set()
        return True

    d._play = _fake_play

    c_before = d.get_runtime_state().command_generation

    try:
        # Submit three next intents concurrently
        async with d._manual_nav_lock:
            pass  # ensure lock is initialised in this loop
        await asyncio.gather(
            d._queue_manual_navigation(direction="next"),
            d._queue_manual_navigation(direction="next"),
            d._queue_manual_navigation(direction="next"),
        )

        assert d._current_index == 3
        s = d.get_runtime_state()
        assert s.command_generation == c_before + 3

        # Release settle ->?only the last (coalesced) intent should dispatch
        settle_barrier.set()
        await asyncio.wait_for(dispatch_done.wait(), timeout=5)

        assert len(dispatched) == 1
        assert dispatched[0][1] == "D"  # index 3: A(0),B(1),C(2),D(3)
    finally:
        settle_barrier.set()
        dispatch_done.set()
        await d.close_command_arbiter()


# ── T04-B: next+previous ->?final index/target correct ────────────────

@pytest.mark.asyncio
async def test_t04b_next_then_previous_final_index_target_correct():
    """Next then previous: final _current_index and target are correct."""
    d = _make_t04b_device()
    d._play_list_items = [
        {"display_name": n, "legacy_name": n, "item_id": "", "entity_id": ""}
        for n in ("A", "B", "C")
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )

    settle_barrier = asyncio.Event()
    d._wait_manual_navigation_settle = lambda: settle_barrier.wait()

    dispatched = []
    dispatch_done = asyncio.Event()

    async def _fake_play(name="", **kwargs):
        dispatched.append((kwargs.get("navigation_generation"), name))
        dispatch_done.set()
        return True

    d._play = _fake_play

    try:
        await d._queue_manual_navigation(direction="next")   # A→B, index=1
        await d._queue_manual_navigation(direction="previous")  # B→A, index=0

        assert d._current_index == 0

        settle_barrier.set()
        await asyncio.wait_for(dispatch_done.wait(), timeout=5)

        # Latest-pending: only the "previous" (back to A) survives
        assert len(dispatched) == 1
        assert dispatched[0][1] == "A"
    finally:
        settle_barrier.set()
        dispatch_done.set()
        await d.close_command_arbiter()


# ── T04-B: empty playlist ->?no submit, no command bump ───────────────

@pytest.mark.asyncio
async def test_t04b_empty_playlist_no_submit_no_command():
    """Empty playlist: _queue_manual_navigation returns False, no submit."""
    d = _make_t04b_device()
    d._play_list_items = []
    d._current_index = -1

    c_before = d.get_runtime_state().command_generation

    result = await d._queue_manual_navigation(direction="next")
    assert result is False
    assert d.get_runtime_state().command_generation == c_before
    # Arbiter was never created because submit was never called
    assert d._command_arbiter is None


# ── T04-B: settle ->?_invalidate_manual_navigation -> zero dispatch ────

@pytest.mark.asyncio
async def test_t04b_settle_invalidate_old_intent_zero_dispatch():
    """During settle window, invalidate (stop) makes old intent skip.

    The first intent enters settle; while blocked there, generation is
    invalidated.  After settle, the stale intent is silently dropped.
    """
    d = _make_t04b_device()
    d._play_list_items = [
        {"display_name": n, "legacy_name": n, "item_id": "", "entity_id": ""}
        for n in ("A", "B", "C")
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )

    settle_barrier = asyncio.Event()
    settle_released = asyncio.Event()

    async def _settle():
        settle_released.set()  # signal: "entering settle"
        await asyncio.wait_for(settle_barrier.wait(), timeout=5)

    d._wait_manual_navigation_settle = _settle

    dispatched = []
    sentinel_done = asyncio.Event()

    async def _fake_play(name="", **kwargs):
        dispatched.append(name)
        sentinel_done.set()
        return True

    d._play = _fake_play

    try:
        # Submit intent
        await d._queue_manual_navigation(direction="next")
        await asyncio.wait_for(settle_released.wait(), timeout=5)

        # Invalidate while executor is blocked in settle
        d._invalidate_manual_navigation(reason="stop")

        # Release settle ->?stale intent should be dropped
        settle_barrier.set()

        # Submit a sentinel intent: it will only execute after the stale
        # intent finishes skipping, proving serial executor ordering.
        await d._queue_manual_navigation(direction="next")
        await asyncio.wait_for(sentinel_done.wait(), timeout=5)

        assert dispatched == ["C"]  # only sentinel dispatched, not stale "B"
    finally:
        settle_barrier.set()
        settle_released.set()
        sentinel_done.set()
        await d.close_command_arbiter()


# ── T04-B: serial execution ->?max 1 concurrent physical dispatch ─────

@pytest.mark.asyncio
async def test_t04b_serial_execution_max_one_concurrent():
    """While _play is blocked for intent#1, intent#2 queued -> serial only.

    Intent#2 must wait for intent#1 to finish before starting.
    """
    d = _make_t04b_device()
    d._play_list_items = [
        {"display_name": n, "legacy_name": n, "item_id": "", "entity_id": ""}
        for n in ("A", "B", "C", "D")
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )

    # Instant settle for both intents
    settle_1 = asyncio.Event()
    settle_2 = asyncio.Event()
    settle_count = 0

    async def _settle():
        nonlocal settle_count
        settle_count += 1
        if settle_count == 1:
            await asyncio.wait_for(settle_1.wait(), timeout=5)
        else:
            await asyncio.wait_for(settle_2.wait(), timeout=5)

    d._wait_manual_navigation_settle = _settle

    play_1_block = asyncio.Event()
    play_1_entered = asyncio.Event()
    play_2_entered = asyncio.Event()
    play_2_done = asyncio.Event()
    play_order = []

    async def _fake_play(name="", **kwargs):
        play_order.append(name)
        if name == "B":
            play_1_entered.set()
            await asyncio.wait_for(play_1_block.wait(), timeout=5)
        elif name == "C":
            play_2_entered.set()
            play_2_done.set()
        return True

    d._play = _fake_play

    try:
        # Intent #1: A→B
        await d._queue_manual_navigation(direction="next")
        settle_1.set()
        await asyncio.wait_for(play_1_entered.wait(), timeout=5)

        # Intent #2: B→C (while #1 is still blocked in _play)
        await d._queue_manual_navigation(direction="next")
        settle_2.set()

        # Intent #2 should NOT have entered _play yet (serial executor)
        assert not play_2_entered.is_set()

        # Release #1
        play_1_block.set()
        # Now #2 should execute
        await asyncio.wait_for(play_2_done.wait(), timeout=5)

        assert play_order == ["B", "C"]
    finally:
        settle_1.set()
        settle_2.set()
        play_1_block.set()
        play_1_entered.set()
        play_2_entered.set()
        play_2_done.set()
        await d.close_command_arbiter()


# ── T04-B: API fast accepted ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_t04b_api_submit_returns_immediately():
    """submit() returns immediately even when executor is blocked."""
    d = _make_t04b_device()
    d._play_list_items = [
        {"display_name": n, "legacy_name": n, "item_id": "", "entity_id": ""}
        for n in ("A", "B", "C")
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )

    settle_barrier = asyncio.Event()
    d._wait_manual_navigation_settle = lambda: settle_barrier.wait()

    try:
        start = time.time()
        result = await d._queue_manual_navigation(direction="next")
        elapsed = time.time() - start

        assert result is True
        assert elapsed < 0.5  # must return quickly, not wait for settle
    finally:
        settle_barrier.set()
        await d.close_command_arbiter()


# ── T04-B: two devices independent ───────────────────────────────────

@pytest.mark.asyncio
async def test_t04b_two_devices_independent_arbiters():
    """Two DevicePlayer arbiters operate independently."""
    d1 = _make_t04b_device("d1")
    d2 = _make_t04b_device("d2")
    for d in (d1, d2):
        d._play_list_items = [
            {"display_name": n, "legacy_name": n, "item_id": "", "entity_id": ""}
            for n in ("A", "B", "C")
        ]
        d._current_index = 0
        d.device.cur_music = "A"
        d.get_cur_music = lambda dd=d: dd._play_list_items[dd._current_index]["display_name"]
        d.xiaomusic = types.SimpleNamespace(
            music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
        )

    settle_1 = asyncio.Event()
    settle_2 = asyncio.Event()
    d1._wait_manual_navigation_settle = lambda: settle_1.wait()
    d2._wait_manual_navigation_settle = lambda: settle_2.wait()

    dispatched_1 = []
    dispatched_2 = []
    done_1 = asyncio.Event()
    done_2 = asyncio.Event()

    async def _play_1(name="", **kw):
        dispatched_1.append(name)
        done_1.set()
        return True

    async def _play_2(name="", **kw):
        dispatched_2.append(name)
        done_2.set()
        return True

    d1._play = _play_1
    d2._play = _play_2

    try:
        await d1._queue_manual_navigation(direction="next")
        await d2._queue_manual_navigation(direction="next")

        assert d1._current_index == 1
        assert d2._current_index == 1

        settle_1.set()
        settle_2.set()
        await asyncio.wait_for(done_1.wait(), timeout=5)
        await asyncio.wait_for(done_2.wait(), timeout=5)

        assert dispatched_1 == ["B"]
        assert dispatched_2 == ["B"]
    finally:
        settle_1.set()
        settle_2.set()
        done_1.set()
        done_2.set()
        await d1.close_command_arbiter()
        await d2.close_command_arbiter()


# ── T04-B: close ->?no worker task ────────────────────────────────────

@pytest.mark.asyncio
async def test_t04b_close_no_worker_task():
    """After close_command_arbiter, no worker task remains."""
    d = _make_t04b_device()
    d._play_list_items = [
        {"display_name": n, "legacy_name": n, "item_id": "", "entity_id": ""}
        for n in ("A", "B")
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )

    settle_barrier = asyncio.Event()
    d._wait_manual_navigation_settle = lambda: settle_barrier.wait()

    dispatched = []
    async def _fake_play(name="", **kw):
        dispatched.append(name)
        return True
    d._play = _fake_play

    try:
        await d._queue_manual_navigation(direction="next")
        arb = d._command_arbiter
        assert arb is not None
        assert not arb.is_closed

        await d.close_command_arbiter()
        assert d._command_arbiter is None
        assert arb.is_closed
        assert arb._worker_task.done()  # noqa: SLF001

        # No dispatch occurred (settle was blocked, close canceled worker)
        settle_barrier.set()
        assert dispatched == []
    finally:
        settle_barrier.set()
        if d._command_arbiter is not None:
            await d.close_command_arbiter()


# ── T04-B: AST/grep gate ->?no legacy worker symbols ─────────────────

class TestT04bGate:
    """AST gate: device_player must not define or create the old manual
    navigation worker."""

    def test_no_manual_navigation_worker_defined(self):
        """No _manual_navigation_worker function in device_player."""
        tree = ast.parse(open(dp.__file__).read())
        func_names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "_manual_navigation_worker" not in func_names

    def test_no_manual_nav_worker_task_created(self):
        """No _manual_nav_worker_task assignment (create_task or =)."""
        source = open(dp.__file__).read()
        assert "_manual_nav_worker_task" not in source

    def test_manual_next_prev_physical_entry_only_via_arbiter(self):
        """play_next / play_prev are gateways to _queue_manual_navigation
        which submits to arbiter; no direct _play call in them."""
        tree = ast.parse(open(dp.__file__).read())

        class _Checker(ast.NodeVisitor):
            def __init__(self):
                self.func = ""
                self.direct_play_calls: list[tuple[str, int]] = []

            def visit_AsyncFunctionDef(self, n):
                prev = self.func
                self.func = n.name
                self.generic_visit(n)
                self.func = prev

            def visit_Call(self, n):
                if (
                    self.func in ("play_next", "play_prev")
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "_play"
                ):
                    self.direct_play_calls.append((self.func, n.lineno))
                self.generic_visit(n)

        c = _Checker()
        c.visit(tree)
        assert not c.direct_play_calls, (
            f"play_next/play_prev must not call _play directly: {c.direct_play_calls}"
        )


# ══════════════════════════════════════════════════════════════════->?
# T04-C1: stop/pause via Command Arbiter (sync acceptance, async physical)
# ══════════════════════════════════════════════════════════════════->?


def _make_t04c1_device(device_id="did-t04c1", phase=PlaybackPhase.PLAYING):
    """Build a device in the given phase with arbiter-compatible mocks."""
    d = _make_device_via_new(device_id)
    d._command_arbiter = None
    d._manual_nav_lock = asyncio.Lock()
    d._manual_nav_generation = 0
    d._manual_nav_target = None
    d._ensure_manual_navigation_state = lambda: None
    # Set phase via real wrappers
    if phase == PlaybackPhase.PLAYING:
        d._begin_runtime_play_request(
            desired_track=TrackReference(
                entity_id="e1", display_name="test-song", source="test"
            ),
            updated_at=1.0,
        )
        d._begin_runtime_play_dispatch(updated_at=2.0)
        d._begin_runtime_confirmation(updated_at=3.0)
        d._confirm_runtime_playing(updated_at=4.0)
    elif phase == PlaybackPhase.PAUSED:
        d._begin_runtime_play_request(
            desired_track=TrackReference(
                entity_id="e1", display_name="test-song", source="test"
            ),
            updated_at=1.0,
        )
        d._begin_runtime_play_dispatch(updated_at=2.0)
        d._begin_runtime_confirmation(updated_at=3.0)
        d._confirm_runtime_playing(updated_at=4.0)
        d._pause_runtime(updated_at=5.0)
    return d


# ── Scenario A: stop return -> group not released, phase STOPPING; executor -> STOPPED ──

@pytest.mark.asyncio
async def test_t04c1_a_stop_returns_phase_stopping_group_not_released():
    """A: stop() returns with phase STOPPING, group not yet released;
    after arbiter executor runs -> phase STOPPED + event."""
    d = _make_t04c1_device(phase=PlaybackPhase.PLAYING)
    d.do_tts = lambda v: None

    group_stop_called = asyncio.Event()
    group_stop_done = asyncio.Event()

    async def _blocking_group_stop():
        group_stop_called.set()
        await asyncio.wait_for(group_stop_done.wait(), timeout=5)
        return []

    d.group_force_stop_xiaoai = _blocking_group_stop
    d.cancel_group_next_timer = _noop
    d._invalidate_manual_navigation = lambda reason: None
    events_fired = []
    event_done = asyncio.Event()
    d.event_bus = types.SimpleNamespace(
        publish=lambda event_type, **kw: (
            events_fired.append(kw),
            event_done.set(),
        )
    )

    try:
        # Call stop ->?returns immediately with phase STOPPING
        result = await d.stop(arg1="notts")
        assert result is True
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING
        assert d.is_playing is False

        # Group stop was dispatched (arbiter started executing)
        await asyncio.wait_for(group_stop_called.wait(), timeout=5)
        # Phase is still STOPPING (complete_stop not called yet)
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING
        assert len(events_fired) == 0

        # Release group stop → executor completes → STOPPED + event
        group_stop_done.set()
        # Wait for event to be published (signals executor completion)
        await asyncio.wait_for(event_done.wait(), timeout=5)

        assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
        assert len(events_fired) == 1
        assert events_fired[0]["device_id"] == d.did
    finally:
        group_stop_done.set()
        await d.close_command_arbiter()


# ── Scenario B: pause return -> phase PAUSED, event after executor ──

@pytest.mark.asyncio
async def test_t04c1_b_pause_returns_phase_paused_event_after_executor():
    """B: pause() returns with phase PAUSED; event fired after physical work."""
    d = _make_t04c1_device(phase=PlaybackPhase.PLAYING)

    group_stop_called = asyncio.Event()
    group_stop_done = asyncio.Event()

    async def _blocking_group_stop():
        group_stop_called.set()
        await asyncio.wait_for(group_stop_done.wait(), timeout=5)
        return []

    d.group_force_stop_xiaoai = _blocking_group_stop
    d.cancel_group_next_timer = _noop
    d._invalidate_manual_navigation = lambda reason: None
    events_fired = []
    event_done = asyncio.Event()
    d.event_bus = types.SimpleNamespace(
        publish=lambda event_type, **kw: (
            events_fired.append(kw),
            event_done.set(),
        )
    )

    try:
        result = await d.pause()
        assert result is True
        assert d.get_runtime_state().phase == PlaybackPhase.PAUSED
        assert d.is_playing is False

        # Physical work blocked -> no event yet
        await asyncio.wait_for(group_stop_called.wait(), timeout=5)
        assert len(events_fired) == 0

        # Release → event fired
        group_stop_done.set()
        # Wait for event to be published
        await asyncio.wait_for(event_done.wait(), timeout=5)

        assert len(events_fired) == 1
        assert events_fired[0]["device_id"] == d.did
    finally:
        group_stop_done.set()
        await d.close_command_arbiter()


# ── Scenario C: manual settle -> stop arrives, only stop physical ──

@pytest.mark.asyncio
async def test_t04c1_c_manual_settle_stop_wins():
    """C: manual next in settle -> stop arrives, next aborts, stop executes."""
    d = _make_t04c1_device(phase=PlaybackPhase.PLAYING)
    d._play_list_items = [
        {"display_name": n, "legacy_name": n, "item_id": "", "entity_id": ""}
        for n in ("A", "B", "C")
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )

    # Block settle so next intent stays pending
    settle_barrier = asyncio.Event()
    d._wait_manual_navigation_settle = lambda: settle_barrier.wait()

    # Physical mocks for stop
    d.do_tts = lambda v: None
    stop_physical_done = asyncio.Event()
    d.cancel_group_next_timer = _noop

    async def _group_stop():
        stop_physical_done.set()
        return []
    d.group_force_stop_xiaoai = _group_stop

    stop_event_done = asyncio.Event()
    d.event_bus = types.SimpleNamespace(
        publish=lambda event_type, **kw: stop_event_done.set()
    )

    play_calls = []

    async def _fake_play(name="", **kw):
        play_calls.append(name)
        return True
    d._play = _fake_play

    try:
        # Submit next (enters settle)
        await d._queue_manual_navigation(direction="next")

        # Submit stop (while next is settling)
        result = await d.stop(arg1="notts")
        assert result is True

        # Release settle -> next sees newer pending and skips
        settle_barrier.set()
        await asyncio.wait_for(stop_physical_done.wait(), timeout=5)

        # _play was never called (next was skipped)
        assert len(play_calls) == 0
        # Phase → STOPPED (stop completed)
        await asyncio.wait_for(stop_event_done.wait(), timeout=5)
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
    finally:
        settle_barrier.set()
        await d.close_command_arbiter()


# ── Scenario D: manual _play blocked -> stop pending, max concurrency 1 ──

@pytest.mark.asyncio
async def test_t04c1_d_manual_play_blocked_stop_pending():
    """D: manual _play blocked -> stop pending, max concurrency 1, stop after."""
    d = _make_t04c1_device(phase=PlaybackPhase.PLAYING)
    d._play_list_items = [
        {"display_name": n, "legacy_name": n, "item_id": "", "entity_id": ""}
        for n in ("A", "B", "C")
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )

    # Instant settle
    async def _instant_settle():
        return None

    d._wait_manual_navigation_settle = _instant_settle

    # Block _play to simulate in-flight physical work
    play_block = asyncio.Event()
    play_entered = asyncio.Event()

    async def _blocking_play(name="", **kw):
        play_entered.set()
        await asyncio.wait_for(play_block.wait(), timeout=5)
        return True
    d._play = _blocking_play

    # Physical mocks for stop
    d.do_tts = lambda v: None
    stop_physical_done = asyncio.Event()
    d.cancel_group_next_timer = _noop

    async def _group_stop():
        stop_physical_done.set()
        return []
    d.group_force_stop_xiaoai = _group_stop

    stop_event_done = asyncio.Event()
    d.event_bus = types.SimpleNamespace(
        publish=lambda event_type, **kw: stop_event_done.set()
    )

    try:
        # Submit next (will enter _play and block)
        await d._queue_manual_navigation(direction="next")
        await asyncio.wait_for(play_entered.wait(), timeout=5)

        # Submit stop ->?pending because executor busy with _play
        result = await d.stop(arg1="notts")
        assert result is True
        arb = d._command_arbiter
        assert arb.active_sequence is not None  # next still executing
        assert arb.pending_sequence is not None  # stop pending

        # Release _play -> stop executor runs next
        play_block.set()
        await asyncio.wait_for(stop_physical_done.wait(), timeout=5)
        await asyncio.wait_for(stop_event_done.wait(), timeout=5)

        assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
    finally:
        play_block.set()
        await d.close_command_arbiter()


# ── Scenario E: pause pending -> stop overrides, final STOPPED ──

@pytest.mark.asyncio
async def test_t04c1_e_pause_pending_stop_overrides():
    """E: pause pending -> stop overrides, final STOPPED."""
    d = _make_t04c1_device(phase=PlaybackPhase.PLAYING)
    d.do_tts = lambda v: None

    # Block first intent to force pending
    block_first = asyncio.Event()
    first_started = asyncio.Event()
    call_order = []

    async def _group_stop():
        call_order.append("group_stop")
        if not first_started.is_set():
            first_started.set()
            await asyncio.wait_for(block_first.wait(), timeout=5)
        return []
    d.group_force_stop_xiaoai = _group_stop
    d.cancel_group_next_timer = _noop
    d._invalidate_manual_navigation = lambda reason: None
    events_fired = []
    event_done = asyncio.Event()
    d.event_bus = types.SimpleNamespace(
        publish=lambda event_type, **kw: (
            events_fired.append(kw),
            event_done.set(),
        )
    )

    try:
        # Submit pause -> executor starts, blocks
        await d.pause()
        await asyncio.wait_for(first_started.wait(), timeout=5)
        assert d.get_runtime_state().phase == PlaybackPhase.PAUSED

        # Submit stop -> replaces pending pause
        result = await d.stop(arg1="notts")
        assert result is True
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING

        # Release first (pause) -> then stop executor runs
        block_first.set()
        # Wait for stop to complete (event fired)
        await asyncio.wait_for(event_done.wait(), timeout=5)
        # Final phase is STOPPED (stop completed, pause was overridden)
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
    finally:
        block_first.set()
        await d.close_command_arbiter()


# ── Scenario F: stop pending, pause rejected (zero writes), stop completes ──

@pytest.mark.asyncio
async def test_t04c1_f_stop_pending_pause_rejected():
    """F: stop pending/active; pause rejected (False) with zero lifecycle writes.

    pause() on STOPPING returns False without bumping command/sid.  The
    stop executor's token remains current, so after group_stop completes
    the executor proceeds to complete_stop → STOPPED and fires event.
    """
    d = _make_t04c1_device(phase=PlaybackPhase.PLAYING)
    d.do_tts = lambda v: None

    block_stop = asyncio.Event()
    stop_started = asyncio.Event()
    stop_completed = asyncio.Event()

    async def _group_stop():
        if not stop_started.is_set():
            stop_started.set()
            await asyncio.wait_for(block_stop.wait(), timeout=5)
        stop_completed.set()
        return []
    d.group_force_stop_xiaoai = _group_stop
    d.cancel_group_next_timer = _noop
    d._invalidate_manual_navigation = lambda reason: None
    events_fired = []
    event_done = asyncio.Event()
    d.event_bus = types.SimpleNamespace(
        publish=lambda event_type, **kw: (
            events_fired.append(kw),
            event_done.set(),
        )
    )

    try:
        # Submit stop; executor starts, blocks on group
        await d.stop(arg1="notts")
        c_after_stop = d.get_runtime_state().command_generation
        sid_after_stop = d._play_session_id
        await asyncio.wait_for(stop_started.wait(), timeout=5)
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING

        # Submit pause; rejected (phase is STOPPING), zero lifecycle writes
        result = await d.pause()
        assert result is False
        # command and sid unchanged by rejected pause
        assert d.get_runtime_state().command_generation == c_after_stop
        assert d._play_session_id == sid_after_stop
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING

        # Release stop; executor completes -> STOPPED + event
        block_stop.set()
        await asyncio.wait_for(stop_completed.wait(), timeout=5)
        # Wait for complete_stop and event
        await asyncio.wait_for(event_done.wait(), timeout=5)

        assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
        assert len(events_fired) == 1
        assert events_fired[0]["device_id"] == d.did
    finally:
        block_stop.set()
        await d.close_command_arbiter()


# ── Scenario G: executor exception -> last_error, no fake completion ──

@pytest.mark.asyncio
async def test_t04c1_g_executor_exception_last_error_no_fake_completion():
    """G: executor exception -> last_error, no fake STOPPED/event."""
    d = _make_t04c1_device(phase=PlaybackPhase.PLAYING)
    d.do_tts = lambda v: None

    class StopError(Exception):
        pass

    async def _raise():
        raise StopError("group stop failed")
    d.group_force_stop_xiaoai = _raise
    d.cancel_group_next_timer = _noop
    d._invalidate_manual_navigation = lambda reason: None
    events_fired = []
    d.event_bus = types.SimpleNamespace(
        publish=lambda event_type, **kw: events_fired.append(kw)
    )

    try:
        # Wrap executor
        exc_stop_done = asyncio.Event()
        _orig_exc_stop = d._execute_stop_intent
        async def _spy_exc_stop(payload):
            try:
                await _orig_exc_stop(payload)
            finally:
                exc_stop_done.set()
        d._execute_stop_intent = _spy_exc_stop

        # stop() returns True (acceptance phase)
        result = await d.stop(arg1="notts")
        assert result is True
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING

        await asyncio.wait_for(exc_stop_done.wait(), timeout=5.0)
        arb = d._command_arbiter
        assert arb is not None
        assert isinstance(arb.last_error, StopError)

        # Phase stays STOPPING (no fake completion)
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING
        # No event fired
        assert len(events_fired) == 0
    finally:
        await d.close_command_arbiter()


# ── Scenario H: IDLE/STOPPED stop no submit; non-PLAYING pause no submit ──

@pytest.mark.asyncio
async def test_t04c1_h_idle_stopped_stop_no_submit():
    """H: IDLE/STOPPED stop returns False, no arbiter submit."""
    for phase in (PlaybackPhase.IDLE, PlaybackPhase.STOPPED):
        d = _make_device_via_new()
        if phase == PlaybackPhase.STOPPED:
            # Set phase to STOPPED cleanly
            d._begin_runtime_play_request(
                desired_track=TrackReference(
                    entity_id="e1", display_name="t", source="test"
                ),
                updated_at=1.0,
            )
            d._begin_runtime_play_dispatch(updated_at=2.0)
            d._begin_runtime_confirmation(updated_at=3.0)
            d._confirm_runtime_playing(updated_at=4.0)
            d._begin_runtime_stop(updated_at=5.0)
            d._complete_runtime_stop(updated_at=6.0)

        d.do_tts = lambda v: None
        d.cancel_group_next_timer = _noop
        d.group_force_stop_xiaoai = _noop_list
        d._invalidate_manual_navigation = lambda reason: None
        d.event_bus = None

        c_before = d.get_runtime_state().command_generation
        result = await d.stop(arg1="notts")
        assert result is False
        # Zero lifecycle writes: command unchanged
        assert d.get_runtime_state().command_generation == c_before
        # No arbiter created
        assert d._command_arbiter is None


@pytest.mark.asyncio
async def test_t04c1_h_non_playing_pause_no_submit():
    """H: non-PLAYING/non-PAUSED pause returns False, no arbiter submit, zero writes."""
    for phase in (PlaybackPhase.IDLE, PlaybackPhase.STOPPED, PlaybackPhase.STOPPING, PlaybackPhase.FAILED):
        d = _make_device_via_new()
        if phase == PlaybackPhase.STOPPED:
            d._begin_runtime_play_request(
                desired_track=TrackReference(
                    entity_id="e1", display_name="t", source="test"
                ),
                updated_at=1.0,
            )
            d._begin_runtime_play_dispatch(updated_at=2.0)
            d._begin_runtime_confirmation(updated_at=3.0)
            d._confirm_runtime_playing(updated_at=4.0)
            d._begin_runtime_stop(updated_at=5.0)
            d._complete_runtime_stop(updated_at=6.0)
        elif phase == PlaybackPhase.STOPPING:
            d._begin_runtime_play_request(
                desired_track=TrackReference(
                    entity_id="e1", display_name="t", source="test"
                ),
                updated_at=1.0,
            )
            d._begin_runtime_play_dispatch(updated_at=2.0)
            d._begin_runtime_confirmation(updated_at=3.0)
            d._confirm_runtime_playing(updated_at=4.0)
            d._begin_runtime_stop(updated_at=5.0)
        elif phase == PlaybackPhase.FAILED:
            d._begin_runtime_play_request(
                desired_track=TrackReference(
                    entity_id="e1", display_name="t", source="test"
                ),
                updated_at=1.0,
            )
            d._begin_runtime_play_dispatch(updated_at=2.0)
            d._begin_runtime_confirmation(updated_at=3.0)
            d._confirm_runtime_playing(updated_at=4.0)
            d._report_runtime_failure(reason="test", updated_at=5.0)

        d.cancel_group_next_timer = _noop
        d.group_force_stop_xiaoai = _noop_list
        d._invalidate_manual_navigation = lambda reason: None
        d.event_bus = None

        c_before = d.get_runtime_state().command_generation
        result = await d.pause()
        assert result is False
        assert d.get_runtime_state().command_generation == c_before
        assert d._command_arbiter is None


# ── Scenario I: API control blocked physical -> fast accepted ──

@pytest.mark.asyncio
async def test_t04c1_i_api_control_blocked_physical_fast_accepted():
    """I: API control -> physical work blocked but stop() returns quickly accepted."""
    d = _make_t04c1_device(phase=PlaybackPhase.PLAYING)
    d.do_tts = lambda v: None

    # Block physical work to simulate slow I/O
    block_physical = asyncio.Event()
    physical_started = asyncio.Event()

    async def _blocking_group_stop():
        physical_started.set()
        await asyncio.wait_for(block_physical.wait(), timeout=5)
        return []
    d.group_force_stop_xiaoai = _blocking_group_stop
    d.cancel_group_next_timer = _noop
    d._invalidate_manual_navigation = lambda reason: None
    event_done = asyncio.Event()
    d.event_bus = types.SimpleNamespace(
        publish=lambda event_type, **kw: event_done.set()
    )

    try:
        t0 = time.time()
        result = await d.stop(arg1="notts")
        elapsed = time.time() - t0

        # Returns quickly (< 0.5s) even though physical work is blocked
        assert result is True
        assert elapsed < 0.5
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPING

        # Physical work is running in background
        await asyncio.wait_for(physical_started.wait(), timeout=5)

        # Release → complete
        block_physical.set()
        await asyncio.wait_for(event_done.wait(), timeout=5)
        assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
    finally:
        block_physical.set()
        await d.close_command_arbiter()


# ── Scenario J: two devices independent ──

@pytest.mark.asyncio
async def test_t04c1_j_two_devices_independent():
    """J: Two devices independent; arbiter finally close."""
    d1 = _make_t04c1_device("d1", phase=PlaybackPhase.PLAYING)
    d2 = _make_t04c1_device("d2", phase=PlaybackPhase.PLAYING)

    for d in (d1, d2):
        d.do_tts = lambda v: None
        d.cancel_group_next_timer = _noop
        d._invalidate_manual_navigation = lambda reason: None
        d.event_bus = None

    d1_completed = asyncio.Event()
    d2_completed = asyncio.Event()

    async def _group_stop_d1():
        d1_completed.set()
        return []
    async def _group_stop_d2():
        d2_completed.set()
        return []

    d1.group_force_stop_xiaoai = _group_stop_d1
    d2.group_force_stop_xiaoai = _group_stop_d2

    try:
        # Both devices accept stop
        r1 = await d1.stop(arg1="notts")
        r2 = await d2.stop(arg1="notts")
        assert r1 is True
        assert r2 is True

        # Both complete independently
        await asyncio.wait_for(d1_completed.wait(), timeout=5)
        await asyncio.wait_for(d2_completed.wait(), timeout=5)

        assert d1.get_runtime_state().phase == PlaybackPhase.STOPPED
        assert d2.get_runtime_state().phase == PlaybackPhase.STOPPED
    finally:
        await d1.close_command_arbiter()
        await d2.close_command_arbiter()


# ── Arbiter close guarantee ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_t04c1_arbiter_finally_close_no_worker_leaks():
    """After close_command_arbiter(), worker task is done and arbiter is cleaned."""
    d = _make_t04c1_device(phase=PlaybackPhase.PLAYING)
    d.do_tts = lambda v: None
    d.cancel_group_next_timer = _noop
    d.group_force_stop_xiaoai = _noop_list
    d._invalidate_manual_navigation = lambda reason: None
    d.event_bus = None

    close_stop_done = asyncio.Event()
    _orig_close_stop = d._execute_stop_intent
    async def _spy_close_stop(payload):
        try:
            await _orig_close_stop(payload)
        finally:
            close_stop_done.set()
    d._execute_stop_intent = _spy_close_stop

    await d.stop(arg1="notts")
    await asyncio.wait_for(close_stop_done.wait(), timeout=5.0)

    arb = d._command_arbiter
    assert arb is not None
    assert not arb.is_closed

    await d.close_command_arbiter()
    assert d._command_arbiter is None
    assert arb.is_closed
    assert arb._worker_task.done()  # noqa: SLF001


# ── Stop with TTS (arg1="") still handled in executor ─────────────────

@pytest.mark.asyncio
async def test_t04c1_stop_with_tts_physical_work_in_executor():
    """stop() with arg1="" (TTS path) runs TTS in executor, not in stop()."""
    d = _make_t04c1_device(phase=PlaybackPhase.PLAYING)
    d.config = types.SimpleNamespace(stop_tts_msg="再见", delay_sec=0, verbose=False)

    tts_called = asyncio.Event()
    tts_done = asyncio.Event()
    calls_log = []

    async def _tts(msg):
        calls_log.append(("tts", msg))
        tts_called.set()
        await asyncio.wait_for(tts_done.wait(), timeout=5)

    d.do_tts = _tts

    sleep_done = asyncio.Event()
    original_sleep = asyncio.sleep

    async def _tracked_sleep(s):
        if s == 3:
            calls_log.append(("sleep", s))
            await asyncio.wait_for(sleep_done.wait(), timeout=5)
        else:
            await original_sleep(s)
    asyncio.sleep = _tracked_sleep

    d.cancel_group_next_timer = _noop
    d.group_force_stop_xiaoai = _noop_list
    d._invalidate_manual_navigation = lambda reason: None
    event_done = asyncio.Event()
    d.event_bus = types.SimpleNamespace(
        publish=lambda event_type, **kw: event_done.set()
    )

    try:
        t0 = time.time()
        result = await d.stop(arg1="")  # no "notts" -> TTS path
        elapsed = time.time() - t0

        # stop() returns immediately, TTS not called yet
        assert result is True
        assert elapsed < 0.5

        # TTS runs in background (arbiter executor)
        await asyncio.wait_for(tts_called.wait(), timeout=5)
        assert len(calls_log) >= 1
        assert calls_log[0] == ("tts", "再见")

        # Release sleep & TTS → executor completes
        tts_done.set()
        sleep_done.set()
        # Wait for executor completion (event fired)
        await asyncio.wait_for(event_done.wait(), timeout=5)

        assert d.get_runtime_state().phase == PlaybackPhase.STOPPED
    finally:
        asyncio.sleep = original_sleep
        tts_done.set()
        sleep_done.set()
        await d.close_command_arbiter()


# ── Pause idempotent: PAUSED phase already, still submits ─────────────

@pytest.mark.asyncio
async def test_t04c1_pause_idempotent_submits():
    """pause() on PAUSED: phase stays PAUSED, still submits to arbiter."""
    d = _make_t04c1_device(phase=PlaybackPhase.PAUSED)
    d.cancel_group_next_timer = _noop
    d.group_force_stop_xiaoai = _noop_list
    d._invalidate_manual_navigation = lambda reason: None
    d.event_bus = None

    c_before = d.get_runtime_state().command_generation
    pause_idem_done = asyncio.Event()
    _orig_pause_idem = d._execute_pause_intent
    async def _spy_pause_idem(payload):
        try:
            await _orig_pause_idem(payload)
        finally:
            pause_idem_done.set()
    d._execute_pause_intent = _spy_pause_idem

    result = await d.pause()
    assert result is True
    assert d.get_runtime_state().phase == PlaybackPhase.PAUSED
    assert d.get_runtime_state().command_generation == c_before + 1

    await asyncio.wait_for(pause_idem_done.wait(), timeout=5.0)
    await d.close_command_arbiter()


# ── Explicit zero-write tests (requirement 5) ───────────────────────────

@pytest.mark.asyncio
async def test_t04c1_idle_pause_zero_writes():
    """IDLE pause returns False, zero lifecycle writes, no arbiter."""
    d = _make_device_via_new()
    d.cancel_group_next_timer = _noop
    d.group_force_stop_xiaoai = _noop_list
    d._invalidate_manual_navigation = lambda reason: None
    d.event_bus = None

    c_before = d.get_runtime_state().command_generation
    sid_before = d._play_session_id

    result = await d.pause()
    assert result is False
    # Zero writes: command and sid unchanged
    assert d.get_runtime_state().command_generation == c_before
    assert d._play_session_id == sid_before
    assert d._command_arbiter is None


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [PlaybackPhase.IDLE, PlaybackPhase.STOPPED])
async def test_t04c1_idle_stopped_stop_zero_writes(phase):
    """IDLE/STOPPED stop returns False, zero lifecycle writes, no arbiter."""
    d = _make_device_via_new()
    if phase == PlaybackPhase.STOPPED:
        d._begin_runtime_play_request(
            desired_track=TrackReference(
                entity_id="e1", display_name="t", source="test"
            ),
            updated_at=1.0,
        )
        d._begin_runtime_play_dispatch(updated_at=2.0)
        d._begin_runtime_confirmation(updated_at=3.0)
        d._confirm_runtime_playing(updated_at=4.0)
        d._begin_runtime_stop(updated_at=5.0)
        d._complete_runtime_stop(updated_at=6.0)
    d.do_tts = lambda v: None
    d.cancel_group_next_timer = _noop
    d.group_force_stop_xiaoai = _noop_list
    d._invalidate_manual_navigation = lambda reason: None
    d.event_bus = None

    c_before = d.get_runtime_state().command_generation
    sid_before = d._play_session_id

    result = await d.stop(arg1="notts")
    assert result is False
    # Zero writes: command and sid unchanged
    assert d.get_runtime_state().command_generation == c_before
    assert d._play_session_id == sid_before
    assert d._command_arbiter is None


@pytest.mark.asyncio
async def test_t04c1_stopping_repeat_stop_accepted():
    """STOPPING repeat stop is accepted with command +1."""
    d = _make_device_via_new()
    # Put in PLAYING first
    d._begin_runtime_play_request(
        desired_track=TrackReference(
            entity_id="e1", display_name="t", source="test"
        ),
        updated_at=1.0,
    )
    d._begin_runtime_play_dispatch(updated_at=2.0)
    d._begin_runtime_confirmation(updated_at=3.0)
    d._confirm_runtime_playing(updated_at=4.0)
    d.do_tts = lambda v: None
    d.cancel_group_next_timer = _noop
    d.group_force_stop_xiaoai = _noop_list
    d._invalidate_manual_navigation = lambda reason: None
    d.event_bus = None

    # First stop: PLAYING -> STOPPING, command +1
    c0 = d.get_runtime_state().command_generation
    stop_count_done = asyncio.Event()
    _orig_stop_count = d._execute_stop_intent
    async def _spy_stop_count(payload):
        try:
            await _orig_stop_count(payload)
        finally:
            stop_count_done.set()
    d._execute_stop_intent = _spy_stop_count

    result = await d.stop(arg1="notts")
    assert result is True
    assert d.get_runtime_state().phase == PlaybackPhase.STOPPING
    c1 = d.get_runtime_state().command_generation
    assert c1 == c0 + 1

    # Second stop: STOPPING idempotent, still accepted, command +1
    stop_count_done = asyncio.Event()  # reset for second call
    result2 = await d.stop(arg1="notts")
    assert result2 is True
    assert d.get_runtime_state().phase == PlaybackPhase.STOPPING
    assert d.get_runtime_state().command_generation == c1 + 1

    await asyncio.wait_for(stop_count_done.wait(), timeout=5.0)
    await d.close_command_arbiter()


# ═══════════════════════════════════════════════════════════════════════════════
# T05-A: completion_policy module + bg confirmation pure-observation refactor
# ═══════════════════════════════════════════════════════════════════════════════

# ── helpers ────────────────────────────────────────────────────────────


def _event_timer() -> tuple[asyncio.Task, asyncio.Event]:
    """Return (task, done_event) cancellable timer that blocks until done_event."""
    done = asyncio.Event()
    async def _run():
        await asyncio.wait_for(done.wait(), timeout=999.0)
    return asyncio.create_task(_run()), done


def _event_waiter(event: asyncio.Event):
    """Return async callable that blocks until event set (wait_for-wrapped)."""
    async def _wait():
        await asyncio.wait_for(event.wait(), timeout=5.0)
    return _wait


# ── I: observation pure function ───────────────────────────────────────


def test_observation_map_pure_function():
    """I: map_to_observation pure — no I/O, deterministic."""
    import dataclasses

    from xiaomusic.playback.completion_policy import (
        ObservationKind,
        map_to_observation,
    )

    t = 1.0
    obs = map_to_observation(True, observed_at=t, source="test")
    assert obs.kind == ObservationKind.STARTED
    assert obs.observed_at == t
    assert obs.raw_value is True
    assert obs.source == "test"

    obs = map_to_observation(False, observed_at=t, source="p1")
    assert obs.kind == ObservationKind.NOT_STARTED
    assert obs.raw_value is False

    obs = map_to_observation(None, observed_at=t, source="timeout")
    assert obs.kind == ObservationKind.UNKNOWN
    assert obs.raw_value is None

    exc = RuntimeError("boom")
    obs = map_to_observation(exc, observed_at=t, source="err")
    assert obs.kind == ObservationKind.UNKNOWN
    assert obs.raw_value is None
    assert obs.source == "err"

    obs2 = map_to_observation(True, observed_at=t + 1, source="")
    assert obs2.kind == ObservationKind.STARTED
    assert obs2.observed_at == t + 1
    assert obs2.source == ""

    with pytest.raises(dataclasses.FrozenInstanceError):
        obs.kind = ObservationKind.NOT_STARTED  # type: ignore[misc]


# ── A: None → UNKNOWN ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bg_confirm_none_yields_unknown():
    """A: None → UNKNOWN.  No confirm playing, timer preserved, counter unchanged."""
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._is_jellyfin_auto_candidate = lambda **kw: False

    timer_sentinel, timer_done = _event_timer()
    d._next_timer = timer_sentinel

    grace_event = asyncio.Event()
    d._wait_confirmation_grace = _event_waiter(grace_event)

    d._begin_runtime_play_request(
        desired_track=TrackReference(entity_id="e1", display_name="T", source="test"),
        updated_at=2.0,
    )
    d._begin_runtime_play_dispatch(updated_at=3.0)
    d._begin_runtime_confirmation(updated_at=4.0)

    async def _confirm_none(name, sid, **kw):
        return None
    d._confirm_playback_started = _confirm_none
    d._bg_confirm_false_count = 7

    await d._background_confirm_playback_started(
        name="A", sid=1, cur_playlist="BGM",
        origin_url="http://x/a.mp3", current_url="http://x/a.mp3",
        fast_stop=False, token=token,
    )

    s = d.get_runtime_state()
    assert s.phase == PlaybackPhase.CONFIRMING
    assert s.failure is None
    assert s.queue_session_id == token.queue_session_id
    assert s.command_generation == token.command_generation
    assert s.track_attempt_id == token.track_attempt_id
    assert d._bg_confirm_false_count == 7
    assert d._next_timer is timer_sentinel
    assert not timer_sentinel.done()
    timer_done.set()
    timer_sentinel.cancel()
    try:
        await timer_sentinel
    except asyncio.CancelledError:
        pass


# ── B: Exception → UNKNOWN ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bg_confirm_exception_yields_unknown():
    """B: Exception during confirm → UNKNOWN, same invariants as A."""
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._is_jellyfin_auto_candidate = lambda **kw: False

    timer_sentinel, timer_done = _event_timer()
    d._next_timer = timer_sentinel

    grace_event = asyncio.Event()
    d._wait_confirmation_grace = _event_waiter(grace_event)

    d._begin_runtime_play_request(
        desired_track=TrackReference(entity_id="e1", display_name="T", source="test"),
        updated_at=2.0,
    )
    d._begin_runtime_play_dispatch(updated_at=3.0)
    d._begin_runtime_confirmation(updated_at=4.0)

    async def _confirm_error(name, sid, **kw):
        raise RuntimeError("status probe failed")
    d._confirm_playback_started = _confirm_error
    d._bg_confirm_false_count = 7

    await d._background_confirm_playback_started(
        name="A", sid=1, cur_playlist="BGM",
        origin_url="http://x/a.mp3", current_url="http://x/a.mp3",
        fast_stop=False, token=token,
    )

    s = d.get_runtime_state()
    assert s.phase == PlaybackPhase.CONFIRMING
    assert s.failure is None
    assert s.queue_session_id == token.queue_session_id
    assert s.command_generation == token.command_generation
    assert s.track_attempt_id == token.track_attempt_id
    assert d._bg_confirm_false_count == 7
    assert d._next_timer is timer_sentinel
    assert not timer_sentinel.done()
    timer_done.set()
    timer_sentinel.cancel()
    try:
        await timer_sentinel
    except asyncio.CancelledError:
        pass


# ── C: Two False → NOT_STARTED ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_bg_confirm_two_false_yields_not_started_invariants():
    """C: Two consecutive False → NOT_STARTED. Timer preserved, c/q/a unchanged."""
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._is_jellyfin_auto_candidate = lambda **kw: False

    timer_sentinel, timer_done = _event_timer()
    d._next_timer = timer_sentinel

    grace_event = asyncio.Event()
    d._wait_confirmation_grace = _event_waiter(grace_event)

    probe_done = asyncio.Event()
    async def _confirm_false_done(name, sid, **kw):
        probe_done.set()
        return False
    d._confirm_playback_started = _confirm_false_done

    auto_retry_calls: list = []
    _orig = d._submit_auto_retry
    d._submit_auto_retry = lambda kind, *, source_token, sid, reason="", payload=None: (
        auto_retry_calls.append((kind, reason)) or True
    )

    task = asyncio.create_task(d._background_confirm_playback_started(
        name="A", sid=1, cur_playlist="BGM",
        origin_url="http://x/a.mp3", current_url="http://x/a.mp3",
        fast_stop=False, token=token,
    ))
    try:
        await asyncio.wait_for(probe_done.wait(), timeout=5.0)
        probe_done.clear()
        grace_event.set()
        await asyncio.wait_for(task, timeout=5.0)
    finally:
        grace_event.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    s = d.get_runtime_state()
    assert d._bg_confirm_false_count == 2
    assert s.queue_session_id == token.queue_session_id
    assert s.command_generation == token.command_generation
    assert s.track_attempt_id == token.track_attempt_id
    assert d._next_timer is timer_sentinel
    assert not timer_sentinel.done()
    assert auto_retry_calls == []
    assert s.phase == PlaybackPhase.IDLE

    d._submit_auto_retry = _orig
    timer_done.set()
    timer_sentinel.cancel()
    try:
        await timer_sentinel
    except asyncio.CancelledError:
        pass


# ── D: False then True → STARTED ───────────────────────────────────────


@pytest.mark.asyncio
async def test_bg_confirm_false_then_true_clears_counter():
    """D: first False, grace release, second True → STARTED, counter=0, timer preserved."""
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._is_jellyfin_auto_candidate = lambda **kw: False

    timer_sentinel, timer_done = _event_timer()
    d._next_timer = timer_sentinel

    d._begin_runtime_play_request(
        desired_track=TrackReference(entity_id="e1", display_name="T", source="test"),
        updated_at=2.0,
    )
    d._begin_runtime_play_dispatch(updated_at=3.0)
    d._begin_runtime_confirmation(updated_at=4.0)

    grace_event = asyncio.Event()
    d._wait_confirmation_grace = _event_waiter(grace_event)

    probe_done = asyncio.Event()
    call_count = 0
    async def _false_then_true(name, sid, **kw):
        nonlocal call_count
        call_count += 1
        probe_done.set()
        return False if call_count == 1 else True
    d._confirm_playback_started = _false_then_true

    d._bg_confirm_false_count = 3

    task = asyncio.create_task(d._background_confirm_playback_started(
        name="A", sid=1, cur_playlist="BGM",
        origin_url="http://x/a.mp3", current_url="http://x/a.mp3",
        fast_stop=False, token=token,
    ))
    try:
        await asyncio.wait_for(probe_done.wait(), timeout=5.0)
        probe_done.clear()
        grace_event.set()
        await asyncio.wait_for(task, timeout=5.0)
    finally:
        grace_event.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    s = d.get_runtime_state()
    assert s.phase == PlaybackPhase.PLAYING
    assert d._bg_confirm_false_count == 0
    assert s.queue_session_id == token.queue_session_id
    assert s.command_generation == token.command_generation
    assert s.track_attempt_id == token.track_attempt_id
    assert d._next_timer is timer_sentinel
    assert not timer_sentinel.done()

    timer_done.set()
    timer_sentinel.cancel()
    try:
        await timer_sentinel
    except asyncio.CancelledError:
        pass


# ── E: bg confirm two-False vs timer expiry race → timer wins ──────────


@pytest.mark.asyncio
async def test_bg_confirm_two_false_timer_expiry_race():
    """E: bg confirm two-False does NOT submit; timer expiry via real
    set_next_music_timeout → real _submit_auto_retry → arbiter executor.
    Command generation exactly +1, physical next exactly 1,
    arbiter pending/after_barrier both None."""
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._is_jellyfin_auto_candidate = lambda **kw: False

    d._timer_expiry_false_count = 1

    grace_event = asyncio.Event()
    bg_probe_done = asyncio.Event()
    d._wait_confirmation_grace = _event_waiter(grace_event)

    async def _confirm_false_done(name, sid, **kw):
        bg_probe_done.set()
        return False
    d._confirm_playback_started = _confirm_false_done

    async def _status_false():
        return False
    d.get_if_xiaoai_is_playing = _status_false

    phys_done = asyncio.Event()
    phys_calls: list[str] = []

    async def _fake_play_next(command_already_accepted=False):
        phys_calls.append("play_next")
        phys_done.set()
    d._play_next = _fake_play_next
    d._play = _fake_play_next

    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )
    d._play_list_items = [
        {"display_name": "A", "legacy_name": "A", "item_id": "", "entity_id": ""},
        {"display_name": "B", "legacy_name": "B", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "A"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]

    c_before = d.get_runtime_state().command_generation

    # Start timer (delay 0)
    await d.set_next_music_timeout(0, token=token)
    timer = d._next_timer
    assert timer is not None

    # Start bg confirm
    bg_task = asyncio.create_task(d._background_confirm_playback_started(
        name="A", sid=1, cur_playlist="BGM",
        origin_url="http://x/a.mp3", current_url="http://x/a.mp3",
        fast_stop=False, token=token,
    ))

    try:
        # Wait for bg first probe, then release grace
        await asyncio.wait_for(bg_probe_done.wait(), timeout=5.0)
        grace_event.set()

        await asyncio.wait_for(bg_task, timeout=10.0)
        await asyncio.wait_for(timer, timeout=10.0)
        await asyncio.wait_for(phys_done.wait(), timeout=5.0)

        assert d._bg_confirm_false_count == 0
        assert d.get_runtime_state().command_generation == c_before + 1
        assert len(phys_calls) == 1
        assert phys_calls[0] == "play_next"

        arbiter = d._command_arbiter
        if arbiter is not None:
            assert arbiter.pending_sequence is None
            assert arbiter.after_barrier_sequence is None
    finally:
        grace_event.set()
        for t in [bg_task]:
            if not t.done():
                t.cancel()
        await asyncio.gather(bg_task, return_exceptions=True)


# ── F: stale observation → zero writes ─────────────────────────────────


@pytest.mark.asyncio
async def test_bg_confirm_stale_observation_zero_writes():
    """F: stale token at handler entry → zero writes (guard at top of handler)."""
    entered = asyncio.Event()
    release = asyncio.Event()

    d = _make_device_via_new()
    d._play_session_id = 1
    d._is_jellyfin_auto_candidate = lambda **kw: False
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._bg_confirm_false_count = 7

    timer_sentinel, timer_done = _event_timer()
    d._next_timer = timer_sentinel

    async def _blocking_true(name, sid, **kw):
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=5.0)
        return True
    d._confirm_playback_started = _blocking_true

    task = asyncio.create_task(d._background_confirm_playback_started(
        name="A", sid=1, cur_playlist="BGM",
        origin_url="http://x/a.mp3", current_url="http://x/a.mp3",
        fast_stop=False, token=token,
    ))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5.0)
        d._accept_command(updated_at=999.0)
        assert d._is_lifecycle_token_stale(token)
        release.set()
        await asyncio.wait_for(task, timeout=5.0)

        assert d.get_runtime_state().phase != PlaybackPhase.PLAYING
        assert d.get_runtime_state().failure is None
        assert d.get_runtime_state().queue_session_id == token.queue_session_id
        assert d.get_runtime_state().track_attempt_id == token.track_attempt_id
        assert d._bg_confirm_false_count == 7
        assert d._next_timer is timer_sentinel
        assert not timer_sentinel.done()
    finally:
        release.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        timer_done.set()
        timer_sentinel.cancel()
        try:
            await timer_sentinel
        except asyncio.CancelledError:
            pass


# ── G: Jellyfin fallback success → q/c unchanged, a+1, PLAYING ────────


@pytest.mark.asyncio
async def test_bg_confirm_jellyfin_fallback_success():
    """G: NOT_STARTED handler + Jellyfin proxy fallback success.

    Stub _try_proxy_fallback to simulate attempt+1 and return URL.
    Assert q/c unchanged, a exactly +1, handoff confirm PLAYING, timer preserved.
    """
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._is_jellyfin_auto_candidate = lambda **kw: True

    timer_sentinel, timer_done = _event_timer()
    d._next_timer = timer_sentinel

    grace_event = asyncio.Event()
    d._wait_confirmation_grace = _event_waiter(grace_event)

    d._begin_runtime_play_request(
        desired_track=TrackReference(entity_id="e1", display_name="T", source="test"),
        updated_at=2.0,
    )
    d._begin_runtime_play_dispatch(updated_at=3.0)
    d._begin_runtime_confirmation(updated_at=4.0)

    probe_done = asyncio.Event()
    async def _confirm_false_done(name, sid, **kw):
        probe_done.set()
        return False
    d._confirm_playback_started = _confirm_false_done

    async def _stub_proxy(**kw):
        d._start_track_attempt(updated_at=time.time())
        return "http://proxy/a.mp3"
    d._try_proxy_fallback = _stub_proxy
    async def _status_true():
        return True
    d.get_if_xiaoai_is_playing = _status_true

    mark_done = asyncio.Event()
    async def _spy_mark(**kw):
        mark_done.set()
    d._mark_play_started = _spy_mark

    task = asyncio.create_task(d._background_confirm_playback_started(
        name="A", sid=1, cur_playlist="BGM",
        origin_url="http://x/a.mp3", current_url="http://x/a.mp3",
        fast_stop=False, token=token,
    ))
    try:
        await asyncio.wait_for(probe_done.wait(), timeout=5.0)
        probe_done.clear()
        grace_event.set()
        await asyncio.wait_for(task, timeout=5.0)
    finally:
        grace_event.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    s = d.get_runtime_state()
    assert s.queue_session_id == token.queue_session_id
    assert s.command_generation == token.command_generation
    assert s.track_attempt_id == token.track_attempt_id + 1
    assert s.phase == PlaybackPhase.PLAYING
    assert d._bg_confirm_false_count == 0
    assert d._next_timer is timer_sentinel
    assert not timer_sentinel.done()
    # _mark_play_started was called (may be async, check Event)
    assert mark_done.is_set()

    timer_done.set()
    timer_sentinel.cancel()
    try:
        await timer_sentinel
    except asyncio.CancelledError:
        pass


# ── H: Jellyfin fallback failure → timer preserved, no next/retry ──────


@pytest.mark.asyncio
async def test_bg_confirm_jellyfin_fallback_failure_preserves_timer():
    """H: Jellyfin NOT_STARTED fallback fails → timer preserved, no next/retry."""
    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._is_jellyfin_auto_candidate = lambda **kw: True

    timer_sentinel, timer_done = _event_timer()
    d._next_timer = timer_sentinel

    grace_event = asyncio.Event()
    d._wait_confirmation_grace = _event_waiter(grace_event)

    d._begin_runtime_play_request(
        desired_track=TrackReference(entity_id="e1", display_name="T", source="test"),
        updated_at=2.0,
    )
    d._begin_runtime_play_dispatch(updated_at=3.0)
    d._begin_runtime_confirmation(updated_at=4.0)

    probe_done = asyncio.Event()
    async def _confirm_false_done(name, sid, **kw):
        probe_done.set()
        return False
    d._confirm_playback_started = _confirm_false_done

    async def _proxy_fails(**kw):
        return ""
    d._try_proxy_fallback = _proxy_fails

    play_next_calls = 0
    async def _count_play_next():
        nonlocal play_next_calls
        play_next_calls += 1
    d._play_next = _count_play_next

    task = asyncio.create_task(d._background_confirm_playback_started(
        name="A", sid=1, cur_playlist="BGM",
        origin_url="http://x/a.mp3", current_url="http://x/a.mp3",
        fast_stop=False, token=token,
    ))
    try:
        await asyncio.wait_for(probe_done.wait(), timeout=5.0)
        probe_done.clear()
        grace_event.set()
        await asyncio.wait_for(task, timeout=5.0)
    finally:
        grace_event.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    assert d._bg_confirm_false_count == 2
    assert d._next_timer is timer_sentinel
    assert not timer_sentinel.done()
    assert play_next_calls == 0
    s = d.get_runtime_state()
    assert s.queue_session_id == token.queue_session_id
    assert s.command_generation == token.command_generation
    assert s.phase != PlaybackPhase.PLAYING

    timer_done.set()
    timer_sentinel.cancel()
    try:
        await timer_sentinel
    except asyncio.CancelledError:
        pass


# ── stale handler: UNKNOWN/NOT_STARTED direct call ──────────────────


@pytest.mark.asyncio
async def test_apply_observation_sid_stale_zero_effects():
    """Stale sid: handler entry guard → zero counter/phase/IDs/timer changes."""
    from xiaomusic.playback.completion_policy import (
        ConfirmationObservation,
        ObservationKind,
    )

    d = _make_device_via_new()
    d._play_session_id = 99
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)

    timer_sentinel, timer_done = _event_timer()
    d._next_timer = timer_sentinel

    d._bg_confirm_false_count = 7
    fallback_calls = 0
    async def _count_fb(**kw):
        nonlocal fallback_calls
        fallback_calls += 1
        return ""
    d._try_proxy_fallback = _count_fb

    obs = ConfirmationObservation(kind=ObservationKind.NOT_STARTED, observed_at=1.0, source="test")
    await d._apply_confirmation_observation(
        obs, name="A", sid=1, cur_playlist="BGM",
        origin_url="http://x", current_url="http://x",
        fast_stop=False, token=token, jellyfin_auto_candidate=True,
    )

    assert d._bg_confirm_false_count == 7
    assert d._next_timer is timer_sentinel
    assert not timer_sentinel.done()
    assert fallback_calls == 0

    timer_done.set()
    timer_sentinel.cancel()
    try:
        await timer_sentinel
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_apply_observation_token_stale_zero_effects():
    """Stale token: handler entry guard → zero counter/phase/IDs/timer/fallback."""
    from xiaomusic.playback.completion_policy import (
        ConfirmationObservation,
        ObservationKind,
    )

    d = _make_device_via_new()
    d._play_session_id = 1
    d._start_queue_session(updated_at=1.0)
    d._accept_command(updated_at=1.0)
    token = d._start_track_attempt(updated_at=1.0)
    d._accept_command(updated_at=2.0)

    timer_sentinel, timer_done = _event_timer()
    d._next_timer = timer_sentinel

    d._bg_confirm_false_count = 7
    fallback_calls = 0
    async def _count_fb(**kw):
        nonlocal fallback_calls
        fallback_calls += 1
        return ""
    d._try_proxy_fallback = _count_fb

    for kind in (ObservationKind.UNKNOWN, ObservationKind.NOT_STARTED):
        obs = ConfirmationObservation(kind=kind, observed_at=1.0, source="test")
        await d._apply_confirmation_observation(
            obs, name="A", sid=1, cur_playlist="BGM",
            origin_url="http://x", current_url="http://x",
            fast_stop=False, token=token, jellyfin_auto_candidate=True,
        )
        assert d._bg_confirm_false_count == 7
        assert fallback_calls == 0

    assert d._next_timer is timer_sentinel
    assert not timer_sentinel.done()

    timer_done.set()
    timer_sentinel.cancel()
    try:
        await timer_sentinel
    except asyncio.CancelledError:
        pass


# ── J: AST verification — bg function has no destructive ops ───────────


def test_ast_bg_confirm_no_destructive_ops():
    """J: AST: _background_confirm_playback_started has no cancel,
    _submit_auto_retry, _play_next, _play calls.
    _apply_confirmation_observation has no _submit_auto_retry,
    _play_next, _play calls, and no IntentKind reference."""
    import ast as _ast

    with open("xiaomusic/device_player.py") as f:
        tree = _ast.parse(f.read())

    funcs: dict[str, _ast.AsyncFunctionDef | _ast.FunctionDef] = {}
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            funcs[node.name] = node

    bg_func = funcs.get("_background_confirm_playback_started")
    handler_func = funcs.get("_apply_confirmation_observation")
    assert bg_func is not None
    assert handler_func is not None

    def _has_call(node, attr_name):
        for n in _ast.walk(node):
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute) and n.func.attr == attr_name:
                return True
        return False

    assert not _has_call(bg_func, "cancel")
    assert not _has_call(bg_func, "_submit_auto_retry")
    assert not _has_call(bg_func, "_play_next")
    assert not _has_call(bg_func, "_play")

    assert not _has_call(handler_func, "_submit_auto_retry")
    assert not _has_call(handler_func, "_play_next")
    assert not _has_call(handler_func, "_play")

    handler_source = _ast.unparse(handler_func)
    assert "AUTO_NEXT" not in handler_source
    assert "RETRY" not in handler_source

    assert funcs.get("_wait_confirmation_grace") is not None
    assert funcs.get("_wait_jellyfin_confirmation_probe") is not None
