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
    # remaining = duration - offset - TRACK_END_LEAD_SEC = 10 - 2 - 2 = 6
    assert captured["sec"] == pytest.approx(6.0, abs=0.01)


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
async def test_mark_play_started_applies_track_end_lead_to_next_timer(monkeypatch):
    """_mark_play_started requires token arg; mock set_next accepts token.

    Migration: _mark_play_started signature is now ``(*, name, sid, cur_playlist, token, ...)``.
    """
    d = _build_device_for_timer_tests()
    d.config = types.SimpleNamespace(verbose=False)
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
    assert captured["sec"] == pytest.approx(8.0, abs=0.01)  # 10 s - 2 s lead


# ─────────────────────────────────────────────────────────────────────────────
# T03-T07 migrated: external_url_play_started with track-end lead + token
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_external_url_play_started_applies_track_end_lead_to_next_timer():
    """external_url_play_started sends the next track 2 s early; requires token.

    Migration: requires token + mock set_next with token acceptance.
    """
    d = _build_device_for_timer_tests()
    d.config = types.SimpleNamespace(verbose=False)
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

    assert captured["sec"] == pytest.approx(121.0, abs=0.01)  # 123 s - 2 s lead
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

# ─────────────────────────────────────────────────────────────────────────────
# T03-T07 migrated: _mark_play_started requires token
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# UNCHANGED tests (already passing under T03-T07)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_if_xiaoai_is_playing_stops_target_without_status_probe():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("stop-device-id-test")
    d.device_id = "self-device"

    calls = []

    async def _mina_call(command, device_id, *args, **kwargs):
        calls.append((command, device_id))
        if command == "player_get_status":
            return {"data": {"info": '{"status": 1}'}}
        return {"code": 0}

    d.auth_manager = types.SimpleNamespace(mina_call=_mina_call)

    await d.stop_if_xiaoai_is_playing("target-device")

    assert calls == [("player_stop", "target-device")]


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


# ─────────────────────────────────────────────────────────────────────────────
# T03-T07 migrated: autonext guard (still playing check via timer expiry gate)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_autonext_guard_respects_still_playing_check(monkeypatch):
    """Timer expiry advances immediately, without a device status probe.

    2026-11: the expiry status probe was removed. It never changed the
    decision (a looping speaker always reports playing) and only delayed the
    switch by a cloud round trip, letting the speaker restart the track.

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

    assert d._next_called >= 1
    assert still_playing_calls == []  # no status round trip before advancing
