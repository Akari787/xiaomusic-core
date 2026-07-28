"""T07 structural and behavioral guardrails for legacy inference cleanup."""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.playback.facade import PlaybackFacade
from xiaomusic.playback.runtime_state import PlaybackPhase, PlaybackRuntimeState

ROOT = Path(__file__).parents[2]


def _tree_for(obj) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(obj)))


def _called_attrs(tree: ast.AST) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }


def test_facade_snapshot_and_player_state_are_read_only_structurally():
    for method in (PlaybackFacade.build_player_state_snapshot, PlaybackFacade.player_state):
        tree = _tree_for(method)
        source = inspect.getsource(method)
        assert "_last_cmd" not in source
        assert "create_task" not in _called_attrs(tree)
        assert "cancel" not in _called_attrs(tree)
    snapshot_source = inspect.getsource(PlaybackFacade.build_player_state_snapshot).lower()
    assert "miio" not in snapshot_source
    assert "mina" not in snapshot_source


def test_offset_query_has_no_mutation_or_device_status_probe():
    tree = _tree_for(XiaoMusicDevice.get_offset_duration)
    source = inspect.getsource(XiaoMusicDevice.get_offset_duration).lower()
    assert "create_task" not in _called_attrs(tree)
    assert "cancel" not in _called_attrs(tree)
    assert "mina" not in source
    assert "status" not in source


def test_last_cmd_is_not_state_failure_or_completion_authority():
    forbidden = (
        "_derive_transport_state",
        "_handle_play_failure",
        "_background_confirm_playback_started",
        "_apply_confirmation_observation",
        "set_next_music_timeout",
    )
    for name in forbidden:
        owner = PlaybackFacade if name == "_derive_transport_state" else XiaoMusicDevice
        method = getattr(owner, name)
        assert "_last_cmd" not in inspect.getsource(method)


def test_device_has_no_manual_worker_or_bare_task_creation():
    source = inspect.getsource(XiaoMusicDevice)
    assert "manual_worker" not in source
    assert "manual-only" not in source
    for node in ast.walk(_tree_for(XiaoMusicDevice)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "create_task"


def test_runtime_stopped_cannot_be_overridden_by_legacy_isplaying():
    class Device:
        def get_runtime_state(self):
            return PlaybackRuntimeState(phase=PlaybackPhase.STOPPED)

        is_playing = True

    projection = PlaybackFacade._project_runtime_snapshot(Device())
    assert projection is not None
    assert projection["transport_state"] == "stopped"
    assert projection["is_playing"] is False


def test_legacy_stale_timer_projection_is_read_only():
    class Timer:
        cancel_calls = 0

        def cancel(self):
            self.cancel_calls += 1

    timer = Timer()

    class Device:
        _degraded = False
        _play_failed_cnt = 0
        _next_timer = timer
        _current_index = -1
        is_playing = False

        @staticmethod
        def _get_playlist_names():
            return []

        @staticmethod
        def get_cur_music():
            return ""

    facade = PlaybackFacade.__new__(PlaybackFacade)
    state = facade._derive_transport_state(Device(), False, {})

    assert state == "idle"
    assert timer.cancel_calls == 0
    assert Device._next_timer is timer


def test_required_playback_docs_facts_exist():
    paths = [
        ROOT / "docs/architecture/playback-control-model.md",
        ROOT / "docs/adr/0004-state-authority.md",
        ROOT / "docs/architecture/state-authority.md",
        ROOT / "docs/api/api_v1_spec.md",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for fact in (
        "PlaybackRuntimeState",
        "LifecycleToken",
        "PlaybackTaskRegistry",
        "completion",
        "accepted=true",
        "started=null",
        "STOPPED",
        "two-false",
        "_play_session_id",
        "_last_cmd",
    ):
        assert fact in text, fact
