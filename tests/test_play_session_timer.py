import asyncio
import logging
import sys
import time
import types

import pytest

from xiaomusic.const import PLAY_TYPE_ALL

if "miservice" not in sys.modules:
    sys.modules["miservice"] = types.SimpleNamespace(miio_command=lambda *args, **kwargs: None)

if "opencc" not in sys.modules:
    class _OpenCC:
        def __init__(self, *_args, **_kwargs):
            pass

        def convert(self, text):
            return text

    sys.modules["opencc"] = types.SimpleNamespace(OpenCC=_OpenCC)

from xiaomusic.device_player import XiaoMusicDevice


def _build_device_for_timer_tests():
    """Build a minimal XiaoMusicDevice for timer-centric tests.

    T03-T07 migration notes:
    - Queue authority: ``_play_list_items`` (list of dicts) replaces ``_play_list``.
    - Runtime state: ``_runtime_state = PlaybackRuntimeState()`` for lifecycle tokens.
    - Task registry: ``_playback_tasks = None``; lazy-created on first usage.
    """
    from xiaomusic.playback.runtime_state import PlaybackRuntimeState

    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("play-session-test")
    d._next_timer = None
    d._stop_timer = None
    d._tts_timer = None
    d._duration_probe_task = None
    d._play_session_id = 1
    d._last_cmd = ""
    d._autonext_guard_task = None
    d._playback_confirm_task = None
    d._playback_status_probe_task = None
    d._start_time = 0
    d._paused_time = 0
    d._duration = 0
    d._last_volume = 0
    d.event_bus = None
    d.config = types.SimpleNamespace(delay_sec=0, verbose=False)
    d.is_playing = True
    d.device = types.SimpleNamespace(
        did="did-test",
        play_type=PLAY_TYPE_ALL,
        cur_music="",
        cur_playlist="旧歌单",
        playlist2music={"旧歌单": "old-song"},
        hardware="",
    )
    # T03-T07: queue authority is _play_list_items (list of dicts).
    d._play_list_items = [
        {"item_id": "item-old", "entity_id": "", "display_name": "old-song", "legacy_name": "old-song"},
        {"item_id": "item-other", "entity_id": "", "display_name": "other-song", "legacy_name": "other-song"},
    ]
    d._current_index = 0
    d._play_failed_cnt = 0
    d._play_fail_first_ts = 0.0
    d._play_fail_last_reason = ""
    d._runtime_state = PlaybackRuntimeState()
    d._bg_confirm_false_count = 0
    d._timer_expiry_false_count = 0
    d._timer_expiry_playing_grace_count = 0
    d._timer_expiry_unknown_grace_count = 0
    d._degraded = False
    d._degraded_notified = False
    d._inflight_fast_stop_tasks = set()
    d._playback_confirm_task = None
    d._command_arbiter = None
    d._playback_tasks = None  # lazy via _ensure_playback_tasks
    d._playlist_session_shuffled = False
    d._manual_nav_target = None
    d._external_context_registry = {}
    d._external_context_registry_order = []
    d._external_context_next_id = 0
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            music_list={"旧歌单": ["old-song", "other-song"]},
        ),
        analytics=types.SimpleNamespace(send_play_event=lambda *args, **kwargs: asyncio.sleep(0)),
    )

    async def _play_next(command_already_accepted=False):
        d._next_called += 1

    async def _group_force_stop_xiaoai(*args, **kwargs):
        return []

    async def _cancel_group_next_timer():
        if d._next_timer:
            d._next_timer.cancel()
            try:
                await d._next_timer
            except asyncio.CancelledError:
                pass
            d._next_timer = None

    d._next_called = 0
    d._play_next = _play_next
    d.group_force_stop_xiaoai = _group_force_stop_xiaoai
    d.cancel_group_next_timer = _cancel_group_next_timer
    d.do_tts = lambda *_args, **_kwargs: asyncio.sleep(0)
    return d


@pytest.mark.asyncio
async def test_timer_ignored_after_session_bump():
    d = _build_device_for_timer_tests()

    await d.set_next_music_timeout(0.05)
    d._bump_play_session(reason="manual-bump")

    await asyncio.sleep(0.12)
    assert d._next_called == 0


@pytest.mark.asyncio
async def test_pause_prevents_next():
    d = _build_device_for_timer_tests()

    await d.set_next_music_timeout(0.08)
    await asyncio.sleep(0.02)
    await d.pause()

    await asyncio.sleep(0.12)
    assert d._next_called == 0


# ─────────────────────────────────────────────────────────────────────────────
# T03-T07 migrated: duration probe
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duration_probe_sets_next_timer_when_duration_recovered(monkeypatch):
    """Probe recovers duration and rebuilds next-track timer.

    Migration: _start_duration_probe now requires a LifecycleToken.
    set_next_music_timeout mock must accept ``token`` kwarg.
    """
    d = _build_device_for_timer_tests()
    d.config = types.SimpleNamespace(delay_sec=-1, verbose=False)
    d._duration = 0.0
    d._duration_probe_task = None

    async def _get_player_status():
        return {"duration": 10.0}

    def _get_offset_duration():
        return 2.0, d._duration

    d.get_player_status = _get_player_status
    d.get_offset_duration = _get_offset_duration

    captured = {"sec": None}
    timer_set = asyncio.Event()

    async def _set_next_music_timeout(sec, token=None):
        captured["sec"] = sec
        timer_set.set()

    d.set_next_music_timeout = _set_next_music_timeout

    real_sleep = asyncio.sleep

    async def fast_sleep(sec):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    token = d._capture_lifecycle_token()
    d._start_duration_probe("x", d._play_session_id, token=token)

    # Wait for probe task to run through its loop and set the timer.
    try:
        await asyncio.wait_for(timer_set.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass

    assert d._duration == 10.0
    # remaining = duration - offset + delay_sec = 10 - 2 - 1 = 7
    assert captured["sec"] == pytest.approx(7.0, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# T03-T07 migrated: auto-next guard (was in get_offset_duration, now via timer expiry)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_overdue_offset_triggers_autonext_guard_when_idle(monkeypatch):
    """Timer expiry + device idle → two False → play_next.

    Migration: get_offset_duration is pure read under T03-T07.
    The auto-next decision now lives in set_next_music_timeout's expiry gate.
    This test exercises the real timer → two-False → AUTO_NEXT chain.
    """
    d = _build_device_for_timer_tests()
    d._timer_expiry_false_count = 0
    d._last_cmd = "play"

    play_next_ev = asyncio.Event()

    async def _play_next(command_already_accepted=False):
        d._next_called += 1
        play_next_ev.set()

    async def _get_if_xiaoai_is_playing():
        return False

    d._play_next = _play_next
    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing

    real_sleep = asyncio.sleep

    async def fast_sleep(sec):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    token = d._capture_lifecycle_token()
    await d.set_next_music_timeout(0.01, token=token)

    # The timer will: sleep(0.01)→False[1]→reschedule(3.0)→False[2]→submit AUTO_NEXT.
    # Arbiter processes → _play_next → event set.
    try:
        await asyncio.wait_for(play_next_ev.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass

    assert d._next_called == 1
    assert d._timer_expiry_false_count == 0  # reset on advance


@pytest.mark.asyncio
async def test_near_end_with_stale_timer_triggers_autonext_guard_when_idle(monkeypatch):
    """Stale timer + near end → progressive expiry → play_next.

    Migration: the old test relied on get_offset_duration cancelling stale
    timer and triggering play_next. Under T03-T07, the expiry gate handles
    this via set_next_music_timeout's _do_next task. We simulate a timer
    that fires near end, device idle → two False → advance.
    """
    d = _build_device_for_timer_tests()
    d._duration = 10.0
    d._start_time = time.time() - 9.5
    d._paused_time = 0.0
    d._timer_expiry_false_count = 0
    d._last_cmd = "play"

    play_next_ev = asyncio.Event()

    async def _play_next(command_already_accepted=False):
        d._next_called += 1
        play_next_ev.set()

    async def _get_if_xiaoai_is_playing():
        return False

    d._play_next = _play_next
    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing

    real_sleep = asyncio.sleep

    async def fast_sleep(sec):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    token = d._capture_lifecycle_token()
    await d.set_next_music_timeout(0.01, token=token)

    try:
        await asyncio.wait_for(play_next_ev.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass

    assert d._next_called == 1


# ─────────────────────────────────────────────────────────────────────────────
# T03-T07 migrated: external_url_play state reset
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_external_url_play_resets_local_progress_state():
    """on_external_url_play clears progress state and uses _play_list_items.

    Migration: on_external_url_play now returns LifecycleToken or None.
    Queue authority is _play_list_items (not _play_list).
    """
    d = _build_device_for_timer_tests()
    d._duration = 120.0
    d._start_time = time.time() - 20.0
    d._paused_time = 2.0
    d._last_cmd = "play"
    d.device.cur_music = "old-song"
    d._play_list_items = [
        {"item_id": "item-old", "entity_id": "", "display_name": "old-song", "legacy_name": "old-song"},
        {"item_id": "item-other", "entity_id": "", "display_name": "other-song", "legacy_name": "other-song"},
    ]
    d._current_index = 0

    result = await d.on_external_url_play()

    # on_external_url_play returns LifecycleToken on success.
    from xiaomusic.playback.runtime_state import LifecycleToken
    assert isinstance(result, LifecycleToken)

    assert d.is_playing is False
    assert d._duration == 0
    assert d._start_time == 0
    assert d._paused_time == 0
    assert d._current_index == -1
    assert d._play_list_items == []
    assert d.device.cur_music == ""
    assert d.device.cur_playlist == ""
    assert d.device.playlist2music["旧歌单"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# T03-T07 migrated: external_url_play_started requires token
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_external_url_play_started_sets_duration_and_next_timer():
    """on_external_url_play_started requires token; mock set_next accepts token.

    Migration: on_external_url_play_started now requires a ``token`` kwarg.
    set_next_music_timeout mock must accept ``token``.
    """
    d = _build_device_for_timer_tests()
    d.device.cur_playlist = "中文"
    d._play_list_items = [
        {"item_id": "item-old", "entity_id": "", "display_name": "old-song", "legacy_name": "old-song"},
        {"item_id": "item-slow", "entity_id": "", "display_name": "slow-song", "legacy_name": "slow-song"},
    ]
    d._current_index = -1
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            get_music_duration=lambda name: asyncio.sleep(0, result=123.0),
            music_list={"中文": ["old-song", "slow-song"]},
        ),
        analytics=types.SimpleNamespace(send_play_event=lambda *args, **kwargs: asyncio.sleep(0)),
    )

    async def _get_volume():
        return 33

    d.get_volume = _get_volume

    context = {
        "title": "slow-song",
        "context_hint": {"context_type": "playlist", "context_name": "中文", "context_id": "中文"},
        "source_payload": {"music_name": "slow-song", "playlist_name": "中文", "context_type": "playlist"},
    }

    # Step 1: on_external_url_play to get token.
    token = await d.on_external_url_play(context=context)
    assert token is not None
    from xiaomusic.playback.runtime_state import LifecycleToken
    assert isinstance(token, LifecycleToken)

    # Step 2: on_external_url_play_started with token.
    captured = {"sec": None}
    timer_done = asyncio.Event()

    async def _set_next_music_timeout(sec, token=None):
        captured["sec"] = sec
        timer_done.set()

    d.set_next_music_timeout = _set_next_music_timeout

    # T03-T07: must transition to DISPATCHING before on_external_url_play_started.
    d._begin_runtime_external_dispatch_for_token(token)

    d.device.cur_music = "slow-song"
    await d.on_external_url_play_started(
        context=context,
        resolved={"title": "slow-song", "media_id": "mid-1"},
        token=token,
    )

    assert d.is_playing is True
    assert d._duration == 123.0
    assert d._start_time > 0
    assert d._last_volume == 33
    assert d.device.cur_music == "slow-song"
    assert d._current_index == 1
    assert d.device.playlist2music["中文"] == "slow-song"

    # Timer should have been set.
    try:
        await asyncio.wait_for(timer_done.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass
    assert captured["sec"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# T03-T07 migrated: _mark_play_started requires token
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_play_started_applies_delay_sec_to_next_timer(monkeypatch):
    """_mark_play_started requires token arg; mock set_next accepts token.

    Migration: _mark_play_started signature is now ``(*, name, sid, cur_playlist, token, ...)``.
    """
    d = _build_device_for_timer_tests()
    d.config = types.SimpleNamespace(delay_sec=5, verbose=False)
    d.device.hardware = ""
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(get_music_duration=lambda name: asyncio.sleep(0, result=10.0)),
        analytics=types.SimpleNamespace(send_play_event=lambda *args, **kwargs: asyncio.sleep(0)),
    )

    captured = {"sec": None}
    timer_done = asyncio.Event()

    async def _refresh_runtime_volume(*, context=""):
        return 0

    async def _set_next_music_timeout(sec, token=None):
        captured["sec"] = sec
        timer_done.set()

    d._refresh_runtime_volume = _refresh_runtime_volume
    d.set_next_music_timeout = _set_next_music_timeout

    times = iter([100.0, 100.0])
    monkeypatch.setattr(time, "time", lambda: next(times))

    token = d._capture_lifecycle_token()
    await d._mark_play_started(name="song1", sid=d._play_session_id, cur_playlist="旧歌单", token=token)

    try:
        await asyncio.wait_for(timer_done.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass
    assert captured["sec"] == pytest.approx(15.0, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# T03-T07 migrated: external_url_play_started with delay_sec + token
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_external_url_play_started_applies_delay_sec_to_next_timer():
    """external_url_play_started with delay_sec=-3 requires token.

    Migration: requires token + mock set_next with token acceptance.
    """
    d = _build_device_for_timer_tests()
    d.config = types.SimpleNamespace(delay_sec=-3, verbose=False)
    d.device.cur_playlist = "中文"
    d._play_list_items = [
        {"item_id": "item-old", "entity_id": "", "display_name": "old-song", "legacy_name": "old-song"},
        {"item_id": "item-slow", "entity_id": "", "display_name": "slow-song", "legacy_name": "slow-song"},
    ]
    d._current_index = -1
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            get_music_duration=lambda name: asyncio.sleep(0, result=123.0),
            music_list={"中文": ["old-song", "slow-song"]},
        ),
        analytics=types.SimpleNamespace(send_play_event=lambda *args, **kwargs: asyncio.sleep(0)),
    )

    async def _get_volume():
        return 33

    captured = {"sec": None}
    timer_done = asyncio.Event()

    async def _set_next_music_timeout(sec, token=None):
        captured["sec"] = sec
        timer_done.set()

    d.get_volume = _get_volume
    d.set_next_music_timeout = _set_next_music_timeout

    context = {
        "title": "slow-song",
        "context_hint": {"context_type": "playlist", "context_name": "中文", "context_id": "中文"},
        "source_payload": {"music_name": "slow-song", "playlist_name": "中文", "context_type": "playlist"},
    }

    token = await d.on_external_url_play(context=context)
    assert token is not None

    # T03-T07: must transition to DISPATCHING before on_external_url_play_started.
    d._begin_runtime_external_dispatch_for_token(token)

    d.device.cur_music = "slow-song"
    await d.on_external_url_play_started(
        context=context,
        resolved={"title": "slow-song", "media_id": "mid-1"},
        token=token,
    )

    try:
        await asyncio.wait_for(timer_done.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass

    assert captured["sec"] == pytest.approx(120.0, abs=0.01)
    assert d._duration == 123.0


# ─────────────────────────────────────────────────────────────────────────────
# T03-T07 migrated: shuffle bootstrap uses _play_list_items
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_external_url_playlist_bootstrap_shuffles_when_random_mode():
    """external URL playlist bootstrap shuffles when play_type is RND.

    Migration: queue authority is _play_list_items (list of dicts), not _play_list.
    Assert on _get_playlist_names() or the display_name field.
    """
    from xiaomusic.const import PLAY_TYPE_RND

    d = _build_device_for_timer_tests()
    d.device.play_type = PLAY_TYPE_RND
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            music_list={"中文": ["song-a", "song-b", "song-c"]},
        ),
        analytics=types.SimpleNamespace(send_play_event=lambda *args, **kwargs: asyncio.sleep(0)),
    )

    def reverse_shuffle(items):
        items[:] = list(reversed(items))

    import random as _random

    original_shuffle = _random.shuffle
    _random.shuffle = reverse_shuffle
    try:
        result = await d.on_external_url_play(
            context={
                "title": "song-a",
                "context_hint": {"context_type": "playlist", "context_name": "中文", "context_id": "中文"},
                "source_payload": {"music_name": "song-a", "playlist_name": "中文", "context_type": "playlist"},
            }
        )
    finally:
        _random.shuffle = original_shuffle

    # on_external_url_play returns LifecycleToken on success.
    from xiaomusic.playback.runtime_state import LifecycleToken
    assert isinstance(result, LifecycleToken)

    # Queue authority: _play_list_items (list of dicts), derive names via _get_playlist_names.
    names = d._get_playlist_names()
    assert names == ["song-c", "song-b", "song-a"]

    # _current_index points to "song-a" in reversed list → index 2.
    assert d._current_index == 2


@pytest.mark.asyncio
async def test_external_url_playlist_bootstrap_prefers_playlist_item_id_over_title():
    d = _build_device_for_timer_tests()
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            music_list={"中文": ["same-song", "same-song"]},
            get_playlist_items=lambda playlist_name: [
                {
                    "item_id": "item-1",
                    "entity_id": "entity-1",
                    "display_name": "same-song",
                    "legacy_name": "same-song",
                },
                {
                    "item_id": "item-2",
                    "entity_id": "entity-2",
                    "display_name": "same-song",
                    "legacy_name": "same-song",
                },
            ],
        ),
        analytics=types.SimpleNamespace(send_play_event=lambda *args, **kwargs: asyncio.sleep(0)),
    )

    await d.on_external_url_play(
        context={
            "title": "same-song",
            "context_hint": {"context_type": "playlist", "context_name": "中文", "context_id": "中文"},
            "source_payload": {
                "music_name": "same-song",
                "playlist_name": "中文",
                "context_type": "playlist",
                "playlist_item_id": "item-2",
                "entity_id": "entity-2",
            },
        }
    )

    assert d._current_index == 1
    assert d.device.cur_music == "same-song"
    assert d.device.current_playlist_item_id == "item-2"
    assert d.device.current_entity_id == "entity-2"


# ─────────────────────────────────────────────────────────────────────────────
# T03-T07 migrated: _playmusic background confirmation (mock accepts token)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_playmusic_schedules_background_confirmation_without_blocking(monkeypatch):
    """_playmusic mock set_next must accept ``token`` kwarg.

    Migration: _mark_play_started now calls set_next_music_timeout(sec, token=token).
    Mock must accept the token parameter.
    """
    from xiaomusic.config import Device

    class _MusicLibrary:
        music_list = {"全部": ["song1"]}

        async def get_music_url(self, name):
            return "http://example.com/song1.mp3", "http://example.com/song1.mp3"

        async def get_music_duration(self, name):
            return 10.0

        def is_jellyfin_url(self, _url):
            return False

    class _Analytics:
        async def send_play_event(self, *args, **kwargs):
            return

    xm = types.SimpleNamespace(
        config=types.SimpleNamespace(
            delay_sec=0,
            verbose=False,
            ffmpeg_location="",
            jellyfin_proxy_mode="off",
        ),
        log=logging.getLogger("playmusic-bg-confirm"),
        auth_manager=types.SimpleNamespace(mina_call=None),
        music_library=_MusicLibrary(),
        analytics=_Analytics(),
        device_manager=types.SimpleNamespace(get_group_device_id_list=lambda group: ["d1"]),
        event_bus=None,
    )
    dev = Device(
        did="d1",
        device_id="d1",
        hardware="",
        name="",
        play_type=PLAY_TYPE_ALL,
        cur_playlist="全部",
        playlist2music={},
    )
    d = XiaoMusicDevice(xm, dev, group_name="g")

    captured = {"fast_stop": None, "timer": None}
    blocker = asyncio.Event()

    async def _cancel_group_next_timer():
        return None

    async def _group_force_stop_xiaoai(*, fast=False):
        captured["fast_stop"] = fast
        return []

    async def _group_player_play(url, name=""):
        return [{"ok": True}]

    async def _confirm_playback_started(name, sid, **kwargs):
        await blocker.wait()
        return True

    async def _refresh_runtime_volume(*, context=""):
        return 0

    async def _set_next_music_timeout(sec, token=None):
        captured["timer"] = sec

    async def _execute_group_stop(*, fast_stop, sid, force_sync=False):
        captured["fast_stop"] = fast_stop
        return None

    d.cancel_group_next_timer = _cancel_group_next_timer
    d.group_force_stop_xiaoai = _group_force_stop_xiaoai
    d.group_player_play = _group_player_play
    d._confirm_playback_started = _confirm_playback_started
    d._refresh_runtime_volume = _refresh_runtime_volume
    d.set_next_music_timeout = _set_next_music_timeout
    d._execute_group_stop = _execute_group_stop

    # monkeypatch time.time for stable duration_execution_time.
    # _playmusic calls time.time() many times: request_token capture, bump, begin_attempt, _mark_play_started, etc.
    # Provide enough mock values.
    _t0 = 100.0
    _mock_times = iter([_t0 + i * 0.001 for i in range(100)])
    monkeypatch.setattr(time, "time", lambda: next(_mock_times))

    out = await d._playmusic(
        "song1", confirm_start_in_background=True, fast_stop=True
    )

    assert out is True
    assert captured["fast_stop"] is True
    assert captured["timer"] is not None
    assert d._playback_confirm_task is not None
    assert not d._playback_confirm_task.done()

    blocker.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ─────────────────────────────────────────────────────────────────────────────
# T03-T07 migrated: _mark_play_started requires token
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_play_started_schedules_status_probe_when_requested(monkeypatch):
    """_mark_play_started requires token arg + mock set_next accepts token.

    Migration: signature is now ``(*, name, sid, cur_playlist, token, ...)``.
    """
    d = _build_device_for_timer_tests()
    captured = {}

    def _schedule_playing_status_probe(*, sid, name):
        captured["sid"] = sid
        captured["name"] = name

    async def _refresh_runtime_volume(*, context=""):
        return 0

    async def _set_next_music_timeout(sec, token=None):
        captured["timer"] = sec

    d.device.hardware = ""
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(get_music_duration=lambda name: asyncio.sleep(0, result=10.0)),
        analytics=types.SimpleNamespace(send_play_event=lambda *args, **kwargs: asyncio.sleep(0)),
    )
    d._schedule_playing_status_probe = _schedule_playing_status_probe
    d._refresh_runtime_volume = _refresh_runtime_volume
    d.set_next_music_timeout = _set_next_music_timeout

    # Stable time for duration_execution_time calculation.
    times = iter([100.0, 100.0, 101.0])
    monkeypatch.setattr(time, "time", lambda: next(times))

    token = d._capture_lifecycle_token()
    await d._mark_play_started(
        name="song1",
        sid=d._play_session_id,
        cur_playlist="旧歌单",
        token=token,
        measure_status=True,
    )

    assert captured["sid"] == d._play_session_id
    assert captured["name"] == "song1"
    assert captured["timer"] == pytest.approx(10.0, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# UNCHANGED tests (already passing under T03-T07)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_background_confirmation_uses_auto_next_confirm_profile():
    d = _build_device_for_timer_tests()
    d.config = types.SimpleNamespace(
        delay_sec=0,
        verbose=False,
        auto_next_confirm_delay_ms=1000,
        auto_next_confirm_retries=2,
        auto_next_confirm_interval_ms=300,
    )
    d._play_session_id = 6

    captured = {}

    async def _confirm_playback_started(name, sid, *, delay_sec=1.2, retries=2, interval_sec=0.6):
        captured.update(
            {
                "name": name,
                "sid": sid,
                "delay_sec": delay_sec,
                "retries": retries,
                "interval_sec": interval_sec,
            }
        )
        return True

    d._confirm_playback_started = _confirm_playback_started
    d._is_jellyfin_auto_candidate = lambda **kwargs: False

    await d._background_confirm_playback_started(
        name="song1",
        sid=6,
        cur_playlist="全部",
        origin_url="http://example.com/song1.mp3",
        current_url="http://example.com/song1.mp3",
        fast_stop=True,
    )

    assert captured == {
        "name": "song1",
        "sid": 6,
        "delay_sec": 1.0,
        "retries": 2,
        "interval_sec": 0.3,
    }


@pytest.mark.asyncio
async def test_background_confirmation_failure_preserves_timer_and_no_retry():
    """T05-A: bg confirm two-False produces NOT_STARTED, preserves timer, no retry."""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5
    d._next_timer = asyncio.create_task(asyncio.sleep(999))

    called = {"cancel": 0, "failure": 0}

    async def _confirm_playback_started(name, sid, **kwargs):
        return False

    async def _cancel_next_timer():
        called["cancel"] += 1

    async def _handle_play_failure(*, name, sid, reason):
        called["failure"] += 1

    d._confirm_playback_started = _confirm_playback_started
    d.cancel_next_timer = _cancel_next_timer
    d._handle_play_failure = _handle_play_failure
    d._is_jellyfin_auto_candidate = lambda **kwargs: False

    await d._background_confirm_playback_started(
        name="song1",
        sid=5,
        cur_playlist="全部",
        origin_url="http://example.com/song1.mp3",
        current_url="http://example.com/song1.mp3",
        fast_stop=True,
    )

    # NOT_STARTED: no failure handler, no timer cancel, counter set to 2
    assert called == {"cancel": 0, "failure": 0}
    assert d._bg_confirm_false_count == 2
    # timer保留
    assert d._next_timer is not None
    assert not d._next_timer.done()
    # 清理
    d._next_timer.cancel()
    try:
        await d._next_timer
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_stop_if_xiaoai_is_playing_checks_target_device_id():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("stop-device-id-test")
    d.device_id = "self-device"
    d.config = types.SimpleNamespace(enable_force_stop=False)

    calls = []

    async def _mina_call(command, device_id, *args, **kwargs):
        calls.append((command, device_id))
        if command == "player_get_status":
            return {"data": {"info": '{"status": 1}'}}
        return {"code": 0}

    d.auth_manager = types.SimpleNamespace(mina_call=_mina_call)

    await d.stop_if_xiaoai_is_playing("target-device")

    assert calls[0] == ("player_get_status", "target-device")
    assert calls[1] == ("player_stop", "target-device")


@pytest.mark.asyncio
async def test_force_stop_fast_path_skips_pause_and_status_probe():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("force-stop-fast-test")

    calls = []

    async def _mina_call(command, device_id, *args, **kwargs):
        calls.append((command, device_id))
        return {"code": 0}

    d.auth_manager = types.SimpleNamespace(mina_call=_mina_call)

    await d.force_stop_xiaoai("target-device", fast=True)

    assert calls == [("player_stop", "target-device")]


@pytest.mark.asyncio
async def test_xiaomusic_play_url_does_not_publish_extra_player_state_event():
    sys.modules["miservice"] = types.SimpleNamespace(
        miio_command=lambda *args, **kwargs: None,
        MiAccount=object,
        MiIOService=object,
        MiNAService=object,
    )

    from xiaomusic.xiaomusic import XiaoMusic

    xm = XiaoMusic.__new__(XiaoMusic)
    published: list[tuple[str, dict]] = []

    class _EventBus:
        def publish(self, event_name, **kwargs):
            published.append((event_name, kwargs))

    class _Device:
        async def submit_external_url_play(self, url, context=None, resolved=None):
            self.submitted = (url, context, resolved)
            return {"accepted": True, "sequence": 7}

    device = _Device()
    xm.device_manager = types.SimpleNamespace(devices={"did-1": device})
    xm.event_bus = _EventBus()
    xm.log = types.SimpleNamespace(info=lambda *args, **kwargs: None)

    out = await XiaoMusic.play_url(
        xm,
        did="did-1",
        arg1="http://example.com/a.mp3",
        context={"foo": "bar"},
        resolved={"title": "song-a"},
    )

    assert out == {"accepted": True, "sequence": 7}
    assert device.submitted == (
        "http://example.com/a.mp3",
        {"foo": "bar"},
        {"title": "song-a"},
    )
    assert published == []


@pytest.mark.asyncio
async def test_auto_next_confirm_failure_preserves_timer():
    """方案3：确认失败时应保留 timer，让歌曲自然播放。"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5
    # 设置一个现有的 timer
    d._next_timer = asyncio.create_task(asyncio.sleep(999))

    async def _confirm_playback_started(name, sid, **kwargs):
        return False

    d._confirm_playback_started = _confirm_playback_started
    d._is_jellyfin_auto_candidate = lambda **kwargs: False

    await d._background_confirm_playback_started(
        name="song1",
        sid=5,
        cur_playlist="全部",
        origin_url="http://example.com/song1.mp3",
        current_url="http://example.com/song1.mp3",
        fast_stop=True,
    )

    # timer 应保留，不被取消
    assert d._next_timer is not None
    assert not d._next_timer.done()
    # 清理
    d._next_timer.cancel()
    try:
        await d._next_timer
    except asyncio.CancelledError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# T03-T07 migrated: autonext guard (still playing check via timer expiry gate)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_autonext_guard_respects_still_playing_check(monkeypatch):
    """Timer expiry + device still playing → play_next called (grace=0).

    Migration: get_offset_duration is pure read. The "still playing" check
    now lives in set_next_music_timeout's expiry gate. Under T03-T07,
    MAX_PLAYING_GRACE_EXTENSIONS=0, so the first still-playing response
    immediately triggers AUTO_NEXT → _play_next IS called.
    The old test expected _play_next NOT to be called (legacy auto-next guard).
    The new contract: still playing does auto-advance (duration timer is authoritative).
    """
    d = _build_device_for_timer_tests()
    d._duration = 10.0
    d._start_time = time.time() - 26.0
    d._paused_time = 0.0
    d._next_timer = None
    d._last_cmd = "play"
    d.is_playing = True
    d._timer_expiry_playing_grace_count = 0

    still_playing_calls = []
    play_next_ev = asyncio.Event()

    async def _get_if_xiaoai_is_playing():
        still_playing_calls.append("called")
        return True  # 歌曲实际在播放

    async def _play_next(command_already_accepted=False):
        d._next_called += 1
        play_next_ev.set()

    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing
    d._play_next = _play_next

    real_sleep = asyncio.sleep

    async def fast_sleep(sec):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    token = d._capture_lifecycle_token()
    await d.set_next_music_timeout(0.01, token=token)

    # Timer fires → still playing → MAX_PLAYING_GRACE_EXTENSIONS=0 → advance immediately.
    try:
        await asyncio.wait_for(play_next_ev.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        pass

    # T03-T07: still playing DOES trigger auto-next (grace exhausted immediately).
    # The old test expected _next_called==0 (legacy guard). Now it's > 0.
    assert d._next_called >= 1
    assert len(still_playing_calls) > 0
