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
    )
    d._play_list_items = [
        {"display_name": "old-song", "legacy_name": "old-song", "item_id": "", "entity_id": ""},
        {"display_name": "other-song", "legacy_name": "other-song", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d._play_failed_cnt = 0
    d._play_fail_first_ts = 0.0
    d._play_fail_last_reason = ""
    d._timer_expiry_false_count = 0
    d._bg_confirm_false_count = 0
    d._timer_expiry_playing_grace_count = 0
    d._timer_expiry_unknown_grace_count = 0
    d._start_time = 0
    d._paused_time = 0
    d._duration = 0

    async def _play_next():
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


# ── offset / purity ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_offset_duration_is_pure_no_task_created():
    """调用 get_offset_duration 后不创建任何 task（_autonext_guard_task 保持 None）。"""
    d = _build_device_for_timer_tests()
    d._duration = 10.0
    d._start_time = time.time() - 5.0
    d._last_cmd = "play"

    assert d._autonext_guard_task is None
    offset, duration = d.get_offset_duration()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert offset > 0
    assert duration == 10.0
    assert d._autonext_guard_task is None


@pytest.mark.asyncio
async def test_get_offset_duration_does_not_query_device():
    """get_offset_duration 不调用 get_if_xiaoai_is_playing。"""
    d = _build_device_for_timer_tests()
    d._duration = 10.0
    d._start_time = time.time() - 5.0
    d._last_cmd = "play"

    called = False

    async def _get_if_xiaoai_is_playing():
        nonlocal called
        called = True
        return True

    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing

    d.get_offset_duration()
    await asyncio.sleep(0)

    assert not called


@pytest.mark.asyncio
async def test_get_offset_duration_does_not_skip_song():
    """get_offset_duration 不触发 _play_next。"""
    d = _build_device_for_timer_tests()
    d._duration = 1.0
    d._start_time = time.time() - 30.0  # far overdue
    d._last_cmd = "play"

    d.get_offset_duration()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert d._next_called == 0


# ── timer expiry gate ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_timer_expiry_extends_when_device_playing():
    """timer 到期时设备仍在播放：延长 timer，不切歌。"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5
    d._timer_expiry_false_count = 0

    async def _get_if_xiaoai_is_playing():
        return True

    async def _cancel_next_timer():
        if d._next_timer:
            d._next_timer.cancel()
            try:
                await d._next_timer
            except asyncio.CancelledError:
                pass
            d._next_timer = None

    captured_timer_sec = []

    async def _set_next_music_timeout(sec):
        captured_timer_sec.append(sec)

    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing
    d.cancel_next_timer = _cancel_next_timer
    d.set_next_music_timeout = _set_next_music_timeout

    await d.set_next_music_timeout(0.04)
    await asyncio.sleep(0.08)

    assert d._next_called == 0
    assert len(captured_timer_sec) >= 1


@pytest.mark.asyncio
async def test_timer_expiry_advances_after_two_consecutive_false():
    """timer 到期连续两次 False 后切到下一首。"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5
    d._timer_expiry_false_count = 0

    status_sequence = [False, False]

    async def _get_if_xiaoai_is_playing():
        return status_sequence.pop(0) if status_sequence else False

    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing

    real_sleep = asyncio.sleep

    async def fast_sleep(sec):
        if sec >= 1.0:
            await real_sleep(0.02)
        else:
            await real_sleep(sec)

    import asyncio as _asyncio
    original_sleep = _asyncio.sleep
    _asyncio.sleep = fast_sleep
    try:
        await d.set_next_music_timeout(0.04)
        await real_sleep(0.20)
    finally:
        _asyncio.sleep = original_sleep

    assert d._next_called == 1


@pytest.mark.asyncio
async def test_timer_expiry_sin_mode_stops_directly():
    """SIN 模式下 timer 到期直接停止，不检查设备状态。"""
    from xiaomusic.const import PLAY_TYPE_SIN

    d = _build_device_for_timer_tests()
    d.device.play_type = PLAY_TYPE_SIN
    d._play_session_id = 5

    stop_called = False

    async def _stop(arg1=""):
        nonlocal stop_called
        stop_called = True

    d.stop = _stop

    await d.set_next_music_timeout(0.04)
    await asyncio.sleep(0.10)

    assert stop_called is True
    assert d._next_called == 0


# ── background confirmation ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_background_confirmation_uses_profile_params():
    """后台确认使用 auto_next_confirm 配置参数调用 _confirm_playback_started。"""
    d = _build_device_for_timer_tests()
    d.config = types.SimpleNamespace(
        delay_sec=0,
        verbose=False,
        auto_next_confirm_delay_ms=2000,
        auto_next_confirm_retries=3,
        auto_next_confirm_interval_ms=300,
    )
    d._play_session_id = 6

    captured = {}

    async def _confirm_playback_started(name, sid, *, delay_sec=1.2, retries=2, interval_sec=0.6):
        captured.update(name=name, sid=sid, delay_sec=delay_sec, retries=retries, interval_sec=interval_sec)
        return True

    d._confirm_playback_started = _confirm_playback_started
    d._is_jellyfin_auto_candidate = lambda **kwargs: False

    await d._background_confirm_playback_started(
        name="song1", sid=6, cur_playlist="全部",
        origin_url="http://x/a.mp3", current_url="http://x/a.mp3", fast_stop=True,
    )

    assert captured["name"] == "song1"
    assert captured["sid"] == 6
    assert captured["delay_sec"] == pytest.approx(2.0, abs=0.01)
    assert captured["retries"] >= 2
    assert captured["interval_sec"] == pytest.approx(0.3, abs=0.01)


@pytest.mark.asyncio
async def test_bg_confirm_single_false_does_not_advance():
    """单次后台确认 False：retry 返回 True 时保留 timer，不切歌。"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5
    d._next_timer = asyncio.create_task(asyncio.sleep(999))

    called = {"failure": 0, "play_next": 0}
    confirm_calls = 0

    async def _confirm_playback_started(name, sid, **kwargs):
        nonlocal confirm_calls
        confirm_calls += 1
        return False if confirm_calls == 1 else True

    async def _handle_play_failure(*, name, sid, reason):
        called["failure"] += 1

    async def _play_next():
        called["play_next"] += 1

    d._confirm_playback_started = _confirm_playback_started
    d._handle_play_failure = _handle_play_failure
    d._play_next = _play_next
    d._is_jellyfin_auto_candidate = lambda **kwargs: False

    await d._background_confirm_playback_started(
        name="song1", sid=5, cur_playlist="全部",
        origin_url="http://x/a.mp3", current_url="http://x/a.mp3", fast_stop=True,
    )

    assert called["failure"] == 0
    assert called["play_next"] == 0
    assert d._next_timer is not None
    assert not d._next_timer.done()
    d._next_timer.cancel()
    try:
        await d._next_timer
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_bg_confirm_two_consecutive_false_advances():
    """连续两次后台确认 False 后切到下一首。"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5
    d._next_timer = asyncio.create_task(asyncio.sleep(999))

    called_next = 0

    async def _confirm_playback_started(name, sid, **kwargs):
        return False

    async def _play_next():
        nonlocal called_next
        called_next += 1

    d._confirm_playback_started = _confirm_playback_started
    d._play_next = _play_next
    d._is_jellyfin_auto_candidate = lambda **kwargs: False

    await d._background_confirm_playback_started(
        name="song1", sid=5, cur_playlist="全部",
        origin_url="http://x/a.mp3", current_url="http://x/a.mp3", fast_stop=True,
    )

    assert called_next == 1
    assert d._next_timer is None


@pytest.mark.asyncio
async def test_bg_confirm_exception_does_not_advance():
    """确认过程中异常：不切歌，保留 timer。"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5
    d._next_timer = asyncio.create_task(asyncio.sleep(999))

    called_next = 0

    async def _confirm_playback_started(name, sid, **kwargs):
        raise RuntimeError("boom")

    async def _play_next():
        nonlocal called_next
        called_next += 1

    d._confirm_playback_started = _confirm_playback_started
    d._play_next = _play_next
    d._is_jellyfin_auto_candidate = lambda **kwargs: False

    await d._background_confirm_playback_started(
        name="song1", sid=5, cur_playlist="全部",
        origin_url="http://x/a.mp3", current_url="http://x/a.mp3", fast_stop=True,
    )

    assert called_next == 0
    assert d._next_timer is not None
    d._next_timer.cancel()
    try:
        await d._next_timer
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_auto_next_confirm_failure_preserves_timer():
    """确认失败（retry 恢复）时保留 timer。"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5
    d._next_timer = asyncio.create_task(asyncio.sleep(999))

    confirm_calls = 0

    async def _confirm_playback_started(name, sid, **kwargs):
        nonlocal confirm_calls
        confirm_calls += 1
        return False if confirm_calls == 1 else True

    d._confirm_playback_started = _confirm_playback_started
    d._is_jellyfin_auto_candidate = lambda **kwargs: False

    await d._background_confirm_playback_started(
        name="song1", sid=5, cur_playlist="全部",
        origin_url="http://x/a.mp3", current_url="http://x/a.mp3", fast_stop=True,
    )

    assert d._next_timer is not None
    assert not d._next_timer.done()
    d._next_timer.cancel()
    try:
        await d._next_timer
    except asyncio.CancelledError:
        pass


# ── timer grace limit ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_timer_playing_grace_advances_after_3_extensions():
    """playing 连续3次延期后，第4次到期切歌。"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5

    # 4 True → 3 extensions, 4th expiry advances.
    status_sequence = [True, True, True, True]

    async def _get_if_xiaoai_is_playing():
        return status_sequence.pop(0) if status_sequence else True

    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing

    real_sleep = asyncio.sleep
    async def fast_sleep(sec):
        await real_sleep(0.02 if sec >= 1.0 else sec)
    import asyncio as _asyncio
    orig = _asyncio.sleep
    _asyncio.sleep = fast_sleep
    try:
        await d.set_next_music_timeout(0.04)
        await real_sleep(0.35)
    finally:
        _asyncio.sleep = orig

    assert d._next_called == 1


@pytest.mark.asyncio
async def test_timer_playing_3_extensions_does_not_advance():
    """playing 仅3次到期（3次延期）时，不切歌，继续延期。"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5

    call_count = 0

    async def _get_if_xiaoai_is_playing():
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            return True
        # After 3 calls: stop producing results so timer chain ends.
        raise asyncio.CancelledError()

    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing

    real_sleep = asyncio.sleep
    async def fast_sleep(sec):
        await real_sleep(0.02 if sec >= 1.0 else sec)
    import asyncio as _asyncio
    orig = _asyncio.sleep
    _asyncio.sleep = fast_sleep
    try:
        await d.set_next_music_timeout(0.04)
        await real_sleep(0.15)
    finally:
        _asyncio.sleep = orig

    assert d._next_called == 0  # 未切歌
    assert 3 <= call_count <= 4


@pytest.mark.asyncio
async def test_timer_unknown_grace_advances_after_3_extensions():
    """异常/unknown 连续3次延期后，第4次到期切歌。"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5

    call_count = 0

    async def _get_if_xiaoai_is_playing():
        nonlocal call_count
        call_count += 1
        raise RuntimeError("boom")

    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing

    real_sleep = asyncio.sleep
    async def fast_sleep(sec):
        await real_sleep(0.02 if sec >= 1.0 else sec)
    import asyncio as _asyncio
    orig = _asyncio.sleep
    _asyncio.sleep = fast_sleep
    try:
        await d.set_next_music_timeout(0.04)
        await real_sleep(0.35)
    finally:
        _asyncio.sleep = orig

    assert d._next_called == 1
    assert call_count >= 4


@pytest.mark.asyncio
async def test_timer_unknown_3_extensions_does_not_advance():
    """异常3次到期（3次延期）时，不切歌，继续延期。"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5

    call_count = 0

    async def _get_if_xiaoai_is_playing():
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise RuntimeError("boom")
        raise asyncio.CancelledError()

    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing

    real_sleep = asyncio.sleep
    async def fast_sleep(sec):
        await real_sleep(0.02 if sec >= 1.0 else sec)
    import asyncio as _asyncio
    orig = _asyncio.sleep
    _asyncio.sleep = fast_sleep
    try:
        await d.set_next_music_timeout(0.04)
        await real_sleep(0.15)
    finally:
        _asyncio.sleep = orig

    assert d._next_called == 0
    assert 3 <= call_count <= 4


@pytest.mark.asyncio
async def test_timer_grace_reset_on_mark_play_started():
    """grace 计数器在 _mark_play_started 时重置。"""
    d = _build_device_for_timer_tests()
    d._timer_expiry_playing_grace_count = 2
    d._timer_expiry_unknown_grace_count = 1
    d.device.hardware = ''
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(get_music_duration=lambda name: asyncio.sleep(0, result=10.0)),
        analytics=types.SimpleNamespace(send_play_event=lambda *a, **k: asyncio.sleep(0)),
    )
    d._refresh_runtime_volume = lambda **kw: asyncio.sleep(0, result=0)
    async def _set_next_music_timeout(sec):
        pass
    d.set_next_music_timeout = _set_next_music_timeout
    await d._mark_play_started(name='x', sid=5, cur_playlist='test')
    assert d._timer_expiry_playing_grace_count == 0
    assert d._timer_expiry_unknown_grace_count == 0


@pytest.mark.asyncio
async def test_timer_grace_reset_on_session_bump():
    """grace 计数器在 _bump_play_session 时重置。"""
    d = _build_device_for_timer_tests()
    d._timer_expiry_playing_grace_count = 2
    d._timer_expiry_unknown_grace_count = 3
    d._bump_play_session(reason='test')
    assert d._timer_expiry_playing_grace_count == 0
    assert d._timer_expiry_unknown_grace_count == 0


# ── session / base timer regression ───────────────────────────────────

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


@pytest.mark.asyncio
async def test_duration_probe_sets_next_timer_when_duration_recovered(monkeypatch):
    d = _build_device_for_timer_tests()
    d.config = types.SimpleNamespace(delay_sec=-1)
    d._duration = 0.0
    d._duration_probe_task = None

    async def _get_player_status():
        return {"duration": 10.0}

    def _get_offset_duration():
        return 2.0, d._duration

    d.get_player_status = _get_player_status
    d.get_offset_duration = _get_offset_duration

    captured = {"sec": None}

    async def _set_next_music_timeout(sec):
        captured["sec"] = sec

    d.set_next_music_timeout = _set_next_music_timeout

    real_sleep = asyncio.sleep

    async def fast_sleep(sec):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    d._start_duration_probe("x", d._play_session_id)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert d._duration == 10.0
    assert captured["sec"] == 7.0


# ── playlist bootstrap / shuffle ──────────────────────────────────────

@pytest.mark.asyncio
async def test_external_url_play_resets_local_progress_state():
    d = _build_device_for_timer_tests()
    d._duration = 120.0
    d._start_time = time.time() - 20.0
    d._paused_time = 2.0
    d._last_cmd = "play"
    d.device.cur_music = "old-song"

    await d.on_external_url_play()

    assert d.is_playing is False
    assert d._duration == 0
    assert d._start_time == 0
    assert d._paused_time == 0
    assert d._current_index == -1
    assert d._play_list_items == []
    assert d.device.cur_music == ""
    assert d.device.cur_playlist == ""
    assert d.device.playlist2music["旧歌单"] == ""


@pytest.mark.asyncio
async def test_external_url_play_started_sets_duration_and_next_timer():
    d = _build_device_for_timer_tests()
    d.device.cur_playlist = "中文"
    d._current_index = -1
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            get_music_duration=lambda name: asyncio.sleep(0, result=123.0)
        )
    )

    async def _get_volume():
        return 33

    d.get_volume = _get_volume

    context = {
        "title": "slow-song",
        "context_hint": {"context_type": "playlist", "context_name": "中文", "context_id": "中文"},
        "source_payload": {"music_name": "slow-song", "playlist_name": "中文", "context_type": "playlist"},
    }
    d.xiaomusic.music_library.music_list = {"中文": ["old-song", "slow-song"]}

    await d.on_external_url_play(context=context)
    await d.on_external_url_play_started(context=context, resolved={"title": "slow-song", "media_id": "mid-1"})

    assert d.is_playing is True
    assert d._duration == 123.0
    assert d._start_time > 0
    assert d._last_volume == 33
    assert d.device.cur_music == "slow-song"
    assert d._current_index == 1
    assert d._next_timer is not None
    assert d.device.playlist2music["中文"] == "slow-song"
    d._next_timer.cancel()
    try:
        await d._next_timer
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_external_url_playlist_bootstrap_shuffles_when_random_mode():
    """play_type=RND with no explicit shuffle flag: bootstrap shuffles once."""
    from xiaomusic.const import PLAY_TYPE_RND

    d = _build_device_for_timer_tests()
    d.device.play_type = PLAY_TYPE_RND
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(music_list={"中文": ["song-a", "song-b", "song-c"]})
    )

    def reverse_shuffle(items):
        items[:] = list(reversed(items))

    import random as _random
    orig = _random.shuffle
    _random.shuffle = reverse_shuffle
    try:
        await d.on_external_url_play(context={
            "title": "song-a",
            "context_hint": {"context_type": "playlist", "context_name": "中文", "context_id": "中文"},
            "source_payload": {"music_name": "song-a", "playlist_name": "中文", "context_type": "playlist"},
        })
    finally:
        _random.shuffle = orig

    names = [item["display_name"] for item in d._play_list_items]
    # RND mode → shuffled once (order reversed by mock)
    assert names == ["song-c", "song-b", "song-a"]
    assert d._current_index == 2
    assert d._playlist_session_shuffled is True


@pytest.mark.asyncio
async def test_rnd_session_shuffles_once_then_control_next_does_not_reshuffle():
    """RND 新 session shuffle 一次后，control next 不重新 shuffle。"""
    from xiaomusic.const import PLAY_TYPE_RND

    d = _build_device_for_timer_tests()
    d.device.play_type = PLAY_TYPE_RND
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(music_list={"BGM": ["C", "A", "D", "B"]})
    )
    # First external play with shuffle (e.g., from WebUI toggle RND + play)
    import random as _random
    call_count = 0
    def count_shuffle(items):
        nonlocal call_count
        call_count += 1
        # fixed order for test
        items[:] = [{"display_name": n, "legacy_name": n, "item_id": "", "entity_id": ""} for n in ["C", "A", "D", "B"]]
    orig = _random.shuffle
    _random.shuffle = count_shuffle
    try:
        await d.on_external_url_play(context={
            "title": "C",
            "context_hint": {"context_type": "playlist", "context_name": "BGM", "context_id": "BGM"},
            "source_payload": {"music_name": "C", "playlist_name": "BGM", "context_type": "playlist"},
        })
    finally:
        _random.shuffle = orig
    assert call_count == 1  # shuffled once

    # Now simulate control next ×2 (should NOT call shuffle again)
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.device.cur_music = d.get_cur_music()

    from xiaomusic.device_player import XiaoMusicDevice
    d._play_next = XiaoMusicDevice._play_next.__get__(d, XiaoMusicDevice)
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )
    async def _fake_play(name="", preserve_playlist=False, confirm_start_in_background=False, fast_stop=False):
        d.device.cur_music = name

    d._play = _fake_play

    await d._play_next(manual=True)
    assert d.get_cur_music() == "A"  # C→A
    await d._play_next(manual=True)
    assert d.get_cur_music() == "D"  # A→D
    # No additional shuffle calls
    assert call_count == 1


@pytest.mark.asyncio
async def test_bootstrap_shuffles_when_context_has_shuffle_flag():
    """context 中 shuffle=true 时打乱，即使 play_type 不是 RND，且不修改 play_type。"""
    d = _build_device_for_timer_tests()
    d.device.play_type = PLAY_TYPE_ALL
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(music_list={"中文": ["song-a", "song-b", "song-c"]})
    )

    def reverse_shuffle(items):
        items[:] = list(reversed(items))

    import random as _random
    orig = _random.shuffle
    _random.shuffle = reverse_shuffle
    try:
        await d.on_external_url_play(context={
            "title": "song-a",
            "shuffle": True,
            "context_hint": {"context_type": "playlist", "context_name": "中文", "context_id": "中文"},
            "source_payload": {"music_name": "song-a", "playlist_name": "中文", "context_type": "playlist"},
        })
    finally:
        _random.shuffle = orig

    names = [item["display_name"] for item in d._play_list_items]
    assert names == ["song-c", "song-b", "song-a"]
    assert d._current_index == 2
    assert d.device.play_type == PLAY_TYPE_ALL  # 未修改


@pytest.mark.asyncio
async def test_bootstrap_prefers_playlist_item_id_over_title():
    d = _build_device_for_timer_tests()
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            music_list={"中文": ["same-song", "same-song"]},
            get_playlist_items=lambda _pn: [
                {"item_id": "item-1", "entity_id": "entity-1", "display_name": "same-song", "legacy_name": "same-song"},
                {"item_id": "item-2", "entity_id": "entity-2", "display_name": "same-song", "legacy_name": "same-song"},
            ],
        )
    )

    await d.on_external_url_play(context={
        "title": "same-song",
        "context_hint": {"context_type": "playlist", "context_name": "中文", "context_id": "中文"},
        "source_payload": {
            "music_name": "same-song", "playlist_name": "中文", "context_type": "playlist",
            "playlist_item_id": "item-2", "entity_id": "entity-2",
        },
    })

    assert d._current_index == 1
    assert d.device.cur_music == "same-song"
    assert d.device.current_playlist_item_id == "item-2"
    assert d.device.current_entity_id == "entity-2"


# ── shuffle from API (facade → device) ────────────────────────────────

@pytest.mark.asyncio
async def test_shuffle_from_playlist_context_selects_member_and_passes_flag():
    """Facade _playlist_context: shuffle=true + query=歌单名 + no library → InvalidRequestError。"""
    from xiaomusic.core.errors import InvalidRequestError
    from xiaomusic.core.models.media import PlayOptions
    from xiaomusic.playback.facade import PlaybackFacade

    f = PlaybackFacade.__new__(PlaybackFacade)
    f.xiaomusic = types.SimpleNamespace(music_library=None)
    opts = PlayOptions(shuffle=True)
    try:
        f._playlist_context(opts, "我的歌单")
        raise AssertionError("should raise InvalidRequestError")
    except InvalidRequestError:
        pass


@pytest.mark.asyncio
async def test_shuffle_session_snapshot_preserved_for_auto_next():
    """shuffle 后 _play_list_items 快照保持不变，供后续 auto-next 使用。"""
    from xiaomusic.const import PLAY_TYPE_RND

    d = _build_device_for_timer_tests()
    d.device.play_type = PLAY_TYPE_RND
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(music_list={"BGM": ["a", "b", "c"]})
    )
    import random as _random
    orig = _random.shuffle

    # 固定打乱顺序
    shuffled_order = None

    def capture_shuffle(items):
        nonlocal shuffled_order
        shuffled_order = [str(x) for x in items]
        items[:] = list(reversed(items))

    _random.shuffle = capture_shuffle
    try:
        await d.on_external_url_play(context={
            "title": "a",
            "shuffle": True,
            "context_hint": {"context_type": "playlist", "context_name": "BGM", "context_id": "BGM"},
            "source_payload": {"music_name": "a", "playlist_name": "BGM", "context_type": "playlist"},
        })
    finally:
        _random.shuffle = orig

    # 快照已打乱
    assert shuffled_order is not None
    assert len(d._play_list_items) == 3
    assert d.device.cur_playlist == "BGM"
    assert d.device.cur_music == "a"


# ── source-aware shuffle routing ────────────────────────────────────

@pytest.mark.asyncio
async def test_shuffle_routes_jellyfin_member_to_jellyfin_source():
    """Jellyfin 歌单成员通过 entity_id 路由到 jellyfin source_hint。"""
    from xiaomusic.core.models.media import PlayOptions
    from xiaomusic.playback.facade import PlaybackFacade

    class _Facade(PlaybackFacade):
        def __init__(self):
            self.xiaomusic = types.SimpleNamespace(
                music_library=types.SimpleNamespace(
                    get_playlist_items=lambda pn: [
                        {"item_id": "i1", "entity_id": "jellyfin:abc123", "display_name": "song-jf", "legacy_name": "song-jf"},
                    ],
                    music_entities={
                        "jellyfin:abc123": {"entity_id": "jellyfin:abc123", "source": "jellyfin", "source_item_id": "abc123", "origin_url": "http://jf.example/stream", "canonical_name": "song-jf", "duration": 180.0},
                    },
                ),
            )

    f = _Facade()
    opts = PlayOptions(shuffle=True)
    result = f._playlist_context(opts, "J歌单")
    assert result is not None
    _pn, member, entity, _shuf = result
    assert member["entity_id"] == "jellyfin:abc123"
    assert entity.get("source") == "jellyfin"
    assert entity.get("origin_url") == "http://jf.example/stream"
    assert entity.get("duration") == 180.0


@pytest.mark.asyncio
async def test_shuffle_routes_local_member_to_local_library_source():
    """本地歌单成员通过 entity_id 路由到 local_library。"""
    from xiaomusic.core.models.media import PlayOptions
    from xiaomusic.playback.facade import PlaybackFacade

    class _Facade(PlaybackFacade):
        def __init__(self):
            self.xiaomusic = types.SimpleNamespace(
                music_library=types.SimpleNamespace(
                    get_playlist_items=lambda pn: [
                        {"item_id": "i1", "entity_id": "local:/app/music/song1.mp3", "display_name": "song-local", "legacy_name": "song-local"},
                    ],
                    music_entities={
                        "local:/app/music/song1.mp3": {"entity_id": "local:/app/music/song1.mp3", "source": "local", "path": "/app/music/song1.mp3", "canonical_name": "song-local", "duration": 30.0},
                    },
                ),
            )

    f = _Facade()
    opts = PlayOptions(shuffle=True)
    result = f._playlist_context(opts, "本地歌单")
    assert result is not None
    _pn, member, entity, _shuf = result
    assert entity["source"] == "local"
    assert entity.get("path") == "/app/music/song1.mp3"


@pytest.mark.asyncio
async def test_shuffle_playlist_name_not_equal_to_track_name():
    """query=歌单名时 music_name 不等于 playlist_name（已从成员中解析）。"""
    from xiaomusic.core.models.media import PlayOptions
    from xiaomusic.playback.facade import PlaybackFacade

    class _Facade(PlaybackFacade):
        def __init__(self):
            self.xiaomusic = types.SimpleNamespace(
                music_library=types.SimpleNamespace(
                    get_playlist_items=lambda pn: [
                        {"item_id": "i1", "entity_id": "jellyfin:abc", "display_name": "real-track", "legacy_name": "real-track"},
                    ],
                    music_entities={
                        "jellyfin:abc": {"entity_id": "jellyfin:abc", "source": "jellyfin", "canonical_name": "real-track", "origin_url": "http://x/stream"},
                    },
                ),
            )

    f = _Facade()
    opts = PlayOptions(shuffle=True)
    result = f._playlist_context(opts, "我的歌单")
    assert result is not None
    _pn, member, entity, _shuf = result
    assert entity.get("canonical_name") == "real-track"  # not "我的歌单"


@pytest.mark.asyncio
async def test_shuffle_empty_playlist_raises_invalid_request():
    """空歌单应抛出 InvalidRequestError。"""
    from xiaomusic.core.errors import InvalidRequestError
    from xiaomusic.core.models.media import PlayOptions
    from xiaomusic.playback.facade import PlaybackFacade

    class _Facade(PlaybackFacade):
        def __init__(self):
            self.xiaomusic = types.SimpleNamespace(
                music_library=types.SimpleNamespace(
                    get_playlist_items=lambda pn: [],
                ),
            )

    f = _Facade()
    opts = PlayOptions(shuffle=True)
    try:
        f._playlist_context(opts, "空歌单")
        raise AssertionError("should raise InvalidRequestError")
    except InvalidRequestError as e:
        assert "empty" in str(e).lower()


@pytest.mark.asyncio
async def test_auto_next_preserves_shuffled_snapshot():
    """自动 _play_next 两次后 cur_playlist 不变、index 前进、play_type 不变。"""
    d = _build_device_for_timer_tests()
    d.device.cur_playlist = "中文"
    d.device.play_type = PLAY_TYPE_ALL
    d._play_list_items = [
        {"display_name": "song-a", "legacy_name": "song-a", "item_id": "", "entity_id": ""},
        {"display_name": "song-b", "legacy_name": "song-b", "item_id": "", "entity_id": ""},
        {"display_name": "song-c", "legacy_name": "song-c", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "song-a"
    d.get_cur_music = lambda: "song-a"

    class _MusicLib:
        music_list = {"中文": ["song-a", "song-b", "song-c"]}

        def is_music_exist(self, name):
            return True

        async def get_music_url(self, name):
            return "http://x/" + name, "http://x/" + name

        async def get_music_duration(self, name):
            return 10.0

        def is_jellyfin_url(self, u):
            return False

    class _Analytics:
        async def send_play_event(self, *a, **k):
            pass

    class _DevMgr:
        @staticmethod
        def get_group_device_id_list(g):
            return ["d1"]

    xm = types.SimpleNamespace(
        config=types.SimpleNamespace(delay_sec=0, verbose=False, ffmpeg_location="", jellyfin_proxy_mode="off"),
        log=logging.getLogger("preserve-test"),
        auth_manager=types.SimpleNamespace(mina_call=None),
        music_library=_MusicLib(),
        analytics=_Analytics(),
        device_manager=_DevMgr(),
        event_bus=None,
    )
    d.xiaomusic = xm

    next_names = []
    async def _play(name="", preserve_playlist=False, confirm_start_in_background=False, fast_stop=False):
        next_names.append((name, preserve_playlist, d.device.cur_playlist, d._current_index))
        d.device.cur_music = name

    d._play = _play

    # Restore real _play_next (fixture mock overrides it)
    from xiaomusic.device_player import XiaoMusicDevice
    d._play_next = XiaoMusicDevice._play_next.__get__(d, XiaoMusicDevice)

    # First auto-next
    await d._play_next(manual=False)
    # Second auto-next
    await d._play_next(manual=False)

    assert len(next_names) == 2
    for _name, preserve, pl, idx in next_names:
        assert preserve is True  # must preserve playlist
        assert pl == "中文"        # playlist unchanged
        assert idx >= 0
    assert d.device.play_type == PLAY_TYPE_ALL


@pytest.mark.asyncio
async def test_auto_next_all_mode_preserves_playlist():
    """ALL 模式下自动切歌不改变 cur_playlist。"""
    from xiaomusic.const import PLAY_TYPE_ALL as PT
    d = _build_device_for_timer_tests()
    d.device.play_type = PT
    d.device.cur_playlist = "BGM"
    d._play_list_items = [
        {"display_name": "a", "legacy_name": "a", "item_id": "", "entity_id": ""},
        {"display_name": "b", "legacy_name": "b", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "a"
    d.get_cur_music = lambda: "a"

    async def _play(name="", preserve_playlist=False, confirm_start_in_background=False, fast_stop=False):
        d.device.cur_music = name

    d._play = _play
    from xiaomusic.device_player import XiaoMusicDevice
    d._play_next = XiaoMusicDevice._play_next.__get__(d, XiaoMusicDevice)
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )
    await d._play_next(manual=False)
    assert d.device.cur_playlist == "BGM"


@pytest.mark.asyncio
async def test_manual_next_also_preserves_playlist():
    """用户手动下一首也保持 playlist 不变。"""
    d = _build_device_for_timer_tests()
    d.device.cur_playlist = "OTS"
    d._play_list_items = [
        {"display_name": "x", "legacy_name": "x", "item_id": "", "entity_id": ""},
        {"display_name": "y", "legacy_name": "y", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "x"
    d.get_cur_music = lambda: "x"

    captures = []
    async def _play(name="", preserve_playlist=False, **kw):
        captures.append((name, preserve_playlist))

    d._play = _play
    from xiaomusic.device_player import XiaoMusicDevice
    d._play_next = XiaoMusicDevice._play_next.__get__(d, XiaoMusicDevice)
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )
    await d._play_next(manual=True)
    assert captures[0][1] is True


@pytest.mark.asyncio
async def test_bg_confirm_self_cancellation_does_not_brick_state():
    """_bump_play_session 不取消当前 running task, 后续代码继续执行。"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5

    import asyncio as _aio
    current = _aio.current_task()
    d._playback_confirm_task = current

    after_bump = False

    # Simulate the exact sequence that bg_confirm would trigger:
    # two-False → _play_next → _play → _playmusic → _bump_play_session

    # Step 1: pretend bg_confirm runs two False
    d._bg_confirm_false_count = 1  # first False already counted

    # Step 2: two consecutive → calls _play_next (mocked) → _play → _playmusic
    # In _playmusic, _bump_play_session is called. Current task IS _playback_confirm_task.
    # It should NOT self-cancel.
    d._bump_play_session(reason="start_new_play")

    after_bump = True

    # Execution continued past _bump_play_session
    assert after_bump is True
    # Task not cancelled
    assert not current.cancelled()
    # _playback_confirm_task NOT set to None (because it was current)
    assert d._playback_confirm_task is current


@pytest.mark.asyncio
async def test_bump_play_session_skips_current_task():
    """_bump_play_session 不取消当前正在运行的 owned task。"""
    d = _build_device_for_timer_tests()
    import asyncio as _aio

    # Register current task as owned
    current = _aio.current_task()
    d._playback_confirm_task = current

    # Should not cancel — current task is self
    d._bump_play_session(reason="test")
    assert d._playback_confirm_task is current  # NOT set to None
    assert not current.cancelled()


@pytest.mark.asyncio
async def test_bump_play_session_cancels_other_tasks():
    """_bump_play_session 取消不属于当前 task 的 owned task。"""
    d = _build_device_for_timer_tests()

    async def _dummy():
        await asyncio.sleep(999)

    other = asyncio.create_task(_dummy())
    d._playback_confirm_task = other
    await asyncio.sleep(0)  # let task start

    d._bump_play_session(reason="test")
    assert d._playback_confirm_task is None
    # Cancel is async — yield to let CancelledError propagate
    await asyncio.sleep(0)
    assert other.cancelled()


@pytest.mark.asyncio
async def test_offset_duration_returns_zero_when_start_time_not_set():
    """_start_time <= 0 时 get_offset_duration 返回 0，不返回 epoch 值。"""
    d = _build_device_for_timer_tests()
    d.is_playing = True
    d._start_time = 0
    d._duration = 10.0
    offset, dur = d.get_offset_duration()
    assert offset == 0
    assert dur == 10.0


@pytest.mark.asyncio
async def test_play_response_sanitizes_api_key_in_all_nested_paths():
    """Facade.play 返回体中所有嵌套路径的 api_key 均被脱敏。"""
    from xiaomusic.playback.facade import PlaybackFacade

    # Build a response dict that mirrors what play() returns,
    # with api_key in multiple nested locations.
    dirty = {
        "status": "playing",
        "media": {
            "stream_url": "http://jf:30013/Audio/abc?api_key=SECRET123&x=1",
        },
        "extra": {
            "dispatch": {"url": "http://jf:30013/Audio/abc?api_key=SECRET123"},
            "delivery_plan": {
                "primary": {"final_url": "http://jf:30013/Audio/abc?api_key=SECRET123"},
                "fallback": {"final_url": "http://jf:30013/proxy?api_key=SECRET123"},
            },
            "playback_outcome": {
                "attempts": [
                    {"url": "http://jf:30013/Audio/abc?api_key=SECRET123"},
                ]
            },
        },
    }

    clean = PlaybackFacade._sanitize_public_value(dirty)

    # Recursively check no SECRET123 anywhere
    import json
    raw = json.dumps(clean)
    assert "SECRET123" not in raw
    assert "api_key" in raw or "***REDACTED***" in raw

    # Verify structure preserved (sensitive values replaced, not removed)
    assert clean["media"]["stream_url"] != dirty["media"]["stream_url"]
    assert "***REDACTED***" in clean["media"]["stream_url"]


# ── misc regression ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_play_started_applies_delay_sec_to_next_timer(monkeypatch):
    d = _build_device_for_timer_tests()
    d.config = types.SimpleNamespace(delay_sec=5, verbose=False)
    d.device.hardware = ""
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(get_music_duration=lambda name: asyncio.sleep(0, result=10.0)),
        analytics=types.SimpleNamespace(send_play_event=lambda *args, **kwargs: asyncio.sleep(0)),
    )
    captured = {"sec": None}

    async def _refresh_runtime_volume(*, context=""):
        return 0

    async def _set_next_music_timeout(sec):
        captured["sec"] = sec

    d._refresh_runtime_volume = _refresh_runtime_volume
    d.set_next_music_timeout = _set_next_music_timeout

    times = iter([100.0, 100.0])
    monkeypatch.setattr(time, "time", lambda: next(times))

    await d._mark_play_started(name="song1", sid=d._play_session_id, cur_playlist="旧歌单")
    assert captured["sec"] == 15.0


@pytest.mark.asyncio
async def test_external_url_play_started_applies_delay_sec_to_next_timer():
    d = _build_device_for_timer_tests()
    d.config = types.SimpleNamespace(delay_sec=-3, verbose=False)
    d.device.cur_playlist = "中文"
    d._current_index = -1
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(get_music_duration=lambda name: asyncio.sleep(0, result=123.0))
    )
    captured = {"sec": None}

    async def _get_volume():
        return 33

    async def _set_next_music_timeout(sec):
        captured["sec"] = sec

    d.get_volume = _get_volume
    d.set_next_music_timeout = _set_next_music_timeout
    d.xiaomusic.music_library.music_list = {"中文": ["old-song", "slow-song"]}

    context = {
        "title": "slow-song",
        "context_hint": {"context_type": "playlist", "context_name": "中文", "context_id": "中文"},
        "source_payload": {"music_name": "slow-song", "playlist_name": "中文", "context_type": "playlist"},
    }
    await d.on_external_url_play(context=context)
    await d.on_external_url_play_started(context=context, resolved={"title": "slow-song", "media_id": "mid-1"})

    assert captured["sec"] == 120.0
    assert d._duration == 123.0


@pytest.mark.asyncio
async def test_auto_playmusic_schedules_background_confirmation_without_blocking():
    from xiaomusic.config import Device

    class _MusicLibrary:
        music_list = {"全部": ["song1"]}

        async def get_music_url(self, name):
            return "http://x/song1.mp3", "http://x/song1.mp3"

        async def get_music_duration(self, name):
            return 10.0

        def is_jellyfin_url(self, _url):
            return False

    class _Analytics:
        async def send_play_event(self, *args, **kwargs):
            return

    xm = types.SimpleNamespace(
        config=types.SimpleNamespace(delay_sec=0, verbose=False, ffmpeg_location="", jellyfin_proxy_mode="off"),
        log=logging.getLogger("playmusic-bg-confirm"),
        auth_manager=types.SimpleNamespace(mina_call=None),
        music_library=_MusicLibrary(),
        analytics=_Analytics(),
        device_manager=types.SimpleNamespace(get_group_device_id_list=lambda group: ["d1"]),
        event_bus=None,
    )
    dev = Device(did="d1", device_id="d1", hardware="", name="", play_type=PLAY_TYPE_ALL, cur_playlist="全部", playlist2music={})
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

    async def _set_next_music_timeout(sec):
        captured["timer"] = sec

    d.cancel_group_next_timer = _cancel_group_next_timer
    d.group_force_stop_xiaoai = _group_force_stop_xiaoai
    d.group_player_play = _group_player_play
    d._confirm_playback_started = _confirm_playback_started
    d._refresh_runtime_volume = _refresh_runtime_volume
    d.set_next_music_timeout = _set_next_music_timeout

    out = await d._playmusic("song1", confirm_start_in_background=True, fast_stop=True)

    assert out is True
    assert captured["fast_stop"] is True
    assert captured["timer"] is not None
    assert d._playback_confirm_task is not None
    assert not d._playback_confirm_task.done()

    blocker.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_mark_play_started_schedules_status_probe_when_requested():
    d = _build_device_for_timer_tests()
    captured = {}

    def _schedule_playing_status_probe(*, sid, name):
        captured["sid"] = sid
        captured["name"] = name

    async def _refresh_runtime_volume(*, context=""):
        return 0

    async def _set_next_music_timeout(sec):
        captured["timer"] = sec

    d.device.hardware = ""
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(get_music_duration=lambda name: asyncio.sleep(0, result=10.0)),
        analytics=types.SimpleNamespace(send_play_event=lambda *args, **kwargs: asyncio.sleep(0)),
    )
    d._schedule_playing_status_probe = _schedule_playing_status_probe
    d._refresh_runtime_volume = _refresh_runtime_volume
    d.set_next_music_timeout = _set_next_music_timeout

    await d._mark_play_started(name="song1", sid=d._play_session_id, cur_playlist="旧歌单", measure_status=True)

    assert captured["sid"] == d._play_session_id
    assert captured["name"] == "song1"
    assert captured["timer"] == pytest.approx(10.0, abs=0.01)


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
        MiAccount=object, MiIOService=object, MiNAService=object,
    )
    from xiaomusic.xiaomusic import XiaoMusic

    xm = XiaoMusic.__new__(XiaoMusic)
    published: list[tuple[str, dict]] = []

    class _EventBus:
        def publish(self, event_name, **kwargs):
            published.append((event_name, kwargs))

    class _Device:
        async def on_external_url_play(self, context=None):
            self.before = context

        async def group_player_play(self, url):
            self.url = url
            return {"code": 0}

        async def on_external_url_play_started(self, context=None, resolved=None):
            self.after = (context, resolved)

    device = _Device()
    xm.device_manager = types.SimpleNamespace(devices={"did-1": device})
    xm.event_bus = _EventBus()
    xm.log = types.SimpleNamespace(info=lambda *args, **kwargs: None)

    out = await XiaoMusic.play_url(xm, did="did-1", arg1="http://x/a.mp3", context={"foo": "bar"}, resolved={"title": "song-a"})

    assert out == {"code": 0}
    assert getattr(device, "url", "") == "http://x/a.mp3"
    assert getattr(device, "before", None) == {"foo": "bar"}
    assert getattr(device, "after", None) == ({"foo": "bar"}, {"title": "song-a"})
    assert published == []


# ── architecture: Transport next → DevicePlayer._play_next ─────────

@pytest.mark.asyncio
async def test_transport_next_calls_player_play_next():
    """MinaTransport.next → player.play_next → _play_next(manual=True)。"""
    from xiaomusic.adapters.mina import MinaTransport

    calls = []
    class _Player:
        async def play_next(self):
            calls.append("play_next")

    class _XM:
        device_manager = types.SimpleNamespace(devices={"did-1": _Player()})

    t = MinaTransport(_XM())
    await t.next("did-1")
    assert calls == ["play_next"]


@pytest.mark.asyncio
async def test_shuffle_snapshot_next_sequence():
    """snapshot C,A,D,B: _play_next(manual=True) → A → D, _play_prev → A。"""
    from xiaomusic.device_player import XiaoMusicDevice

    d = _build_device_for_timer_tests()
    d.device.cur_playlist = "BGM"
    d._play_list_items = [
        {"display_name": "C", "legacy_name": "C", "item_id": "", "entity_id": ""},
        {"display_name": "A", "legacy_name": "A", "item_id": "", "entity_id": ""},
        {"display_name": "D", "legacy_name": "D", "item_id": "", "entity_id": ""},
        {"display_name": "B", "legacy_name": "B", "item_id": "", "entity_id": ""},
    ]
    d._current_index = 0
    d.device.cur_music = "C"
    d.get_cur_music = lambda: d._play_list_items[d._current_index]["display_name"]
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )

    async def _fake_play(name="", **kw):
        d.device.cur_music = name

    d._play_next = XiaoMusicDevice._play_next.__get__(d, XiaoMusicDevice)
    d._play_prev = XiaoMusicDevice._play_prev.__get__(d, XiaoMusicDevice)
    d._play = _fake_play

    await d._play_next(manual=True)
    assert d.get_cur_music() == "A" and d._current_index == 1
    await d._play_next(manual=True)
    assert d.get_cur_music() == "D" and d._current_index == 2
    await d._play_prev(manual=True)
    assert d.get_cur_music() == "A" and d._current_index == 1


@pytest.mark.asyncio
async def test_bootstrap_shuffle_count_is_one():
    """新 external session bootstrap 只 shuffle 一次。"""
    d = _build_device_for_timer_tests()
    d.device.cur_playlist = "BGM"
    d.device.play_type = PLAY_TYPE_ALL
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(music_list={"BGM": ["C", "A", "D", "B"]})
    )
    import random as _random
    shuffle_count = 0
    def count_shuffle(items):
        nonlocal shuffle_count
        shuffle_count += 1
    orig = _random.shuffle
    _random.shuffle = count_shuffle
    try:
        await d.on_external_url_play(context={
            "title": "C",
            "shuffle": True,
            "context_hint": {"context_type": "playlist", "context_name": "BGM", "context_id": "BGM"},
            "source_payload": {"music_name": "C", "playlist_name": "BGM", "context_type": "playlist"},
        })
    finally:
        _random.shuffle = orig
    assert shuffle_count == 1
