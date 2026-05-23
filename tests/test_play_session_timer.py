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
    d._play_list = ["old-song", "other-song"]
    d._current_index = 0
    d._play_failed_cnt = 0
    d._play_fail_first_ts = 0.0
    d._play_fail_last_reason = ""

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
    # remaining = duration - offset + delay_sec = 10 - 2 - 1 = 7
    assert captured["sec"] == 7.0


@pytest.mark.asyncio
async def test_overdue_offset_triggers_autonext_guard_when_idle():
    d = _build_device_for_timer_tests()
    d._duration = 1.0
    d._start_time = time.time() - 30.0
    d._paused_time = 0.0
    d._next_timer = None
    d._last_cmd = "play"

    async def _get_if_xiaoai_is_playing():
        return False

    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing

    d.get_offset_duration()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert d._next_called == 1


@pytest.mark.asyncio
async def test_near_end_with_stale_timer_triggers_autonext_guard_when_idle():
    d = _build_device_for_timer_tests()
    d._duration = 10.0
    d._start_time = time.time() - 9.5
    d._paused_time = 0.0

    async def _stale_timer():
        await asyncio.sleep(999)

    d._next_timer = asyncio.create_task(_stale_timer())
    d._last_cmd = "play"

    async def _get_if_xiaoai_is_playing():
        return False

    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing

    d.get_offset_duration()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert d._next_called == 1
    assert d._next_timer is None


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
    assert d._play_list == []
    assert d.device.cur_music == ""
    assert d.device.cur_playlist == ""
    assert d.device.playlist2music["旧歌单"] == ""


@pytest.mark.asyncio
async def test_external_url_play_started_sets_duration_and_next_timer():
    d = _build_device_for_timer_tests()
    d.device.cur_playlist = "中文"
    d._play_list = ["old-song", "slow-song"]
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
    await d.on_external_url_play_started(
        context=context,
        resolved={"title": "slow-song", "media_id": "mid-1"},
    )

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
    d._play_list = ["old-song", "slow-song"]
    d._current_index = -1
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            get_music_duration=lambda name: asyncio.sleep(0, result=123.0)
        )
    )

    async def _get_volume():
        return 33

    captured = {"sec": None}

    async def _set_next_music_timeout(sec):
        captured["sec"] = sec

    d.get_volume = _get_volume
    d.set_next_music_timeout = _set_next_music_timeout

    context = {
        "title": "slow-song",
        "context_hint": {"context_type": "playlist", "context_name": "中文", "context_id": "中文"},
        "source_payload": {"music_name": "slow-song", "playlist_name": "中文", "context_type": "playlist"},
    }
    d.xiaomusic.music_library.music_list = {"中文": ["old-song", "slow-song"]}

    await d.on_external_url_play(context=context)
    await d.on_external_url_play_started(
        context=context,
        resolved={"title": "slow-song", "media_id": "mid-1"},
    )

    assert captured["sec"] == 120.0
    assert d._duration == 123.0


@pytest.mark.asyncio
async def test_external_url_playlist_bootstrap_shuffles_when_random_mode():
    from xiaomusic.const import PLAY_TYPE_RND

    d = _build_device_for_timer_tests()
    d.device.play_type = PLAY_TYPE_RND
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            music_list={"中文": ["song-a", "song-b", "song-c"]},
        )
    )

    def reverse_shuffle(items):
        items[:] = list(reversed(items))

    import random as _random

    original_shuffle = _random.shuffle
    _random.shuffle = reverse_shuffle
    try:
        await d.on_external_url_play(
            context={
                "title": "song-a",
                "context_hint": {"context_type": "playlist", "context_name": "中文", "context_id": "中文"},
                "source_payload": {"music_name": "song-a", "playlist_name": "中文", "context_type": "playlist"},
            }
        )
    finally:
        _random.shuffle = original_shuffle

    assert d._play_list == ["song-c", "song-b", "song-a"]
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
        )
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


@pytest.mark.asyncio
async def test_auto_playmusic_schedules_background_confirmation_without_blocking():
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

    async def _set_next_music_timeout(sec):
        captured["timer"] = sec

    d.cancel_group_next_timer = _cancel_group_next_timer
    d.group_force_stop_xiaoai = _group_force_stop_xiaoai
    d.group_player_play = _group_player_play
    d._confirm_playback_started = _confirm_playback_started
    d._refresh_runtime_volume = _refresh_runtime_volume
    d.set_next_music_timeout = _set_next_music_timeout

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

    await d._mark_play_started(
        name="song1",
        sid=d._play_session_id,
        cur_playlist="旧歌单",
        measure_status=True,
    )

    assert captured["sid"] == d._play_session_id
    assert captured["name"] == "song1"
    assert captured["timer"] == pytest.approx(10.0, abs=0.01)


@pytest.mark.asyncio
async def test_background_confirmation_uses_auto_next_confirm_profile():
    d = _build_device_for_timer_tests()
    d.config = types.SimpleNamespace(
        delay_sec=0,
        verbose=False,
        auto_next_confirm_delay_ms=800,
        auto_next_confirm_retries=0,
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
        "delay_sec": 0.8,
        "retries": 0,
        "interval_sec": 0.3,
    }


@pytest.mark.asyncio
async def test_background_confirmation_failure_preserves_timer_and_no_retry():
    """确认失败时触发 playback failure 处理（song selection 修复后能正确重试）。"""
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

    # 确认失败时触发 playback failure 处理（会调度 _retry_next）
    assert called == {"cancel": 0, "failure": 1}
    # timer 保留（由 _retry_next 在延迟后触发切歌）
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

    out = await XiaoMusic.play_url(
        xm,
        did="did-1",
        arg1="http://example.com/a.mp3",
        context={"foo": "bar"},
        resolved={"title": "song-a"},
    )

    assert out == {"code": 0}
    assert getattr(device, "url", "") == "http://example.com/a.mp3"
    assert getattr(device, "before", None) == {"foo": "bar"}
    assert getattr(device, "after", None) == ({"foo": "bar"}, {"title": "song-a"})
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


@pytest.mark.asyncio
async def test_autonext_guard_respects_still_playing_check():
    """autonext_guard 应在 still_playing=True 时不触发切歌。"""
    d = _build_device_for_timer_tests()
    d._duration = 10.0
    d._start_time = time.time() - 26.0  # offset = 26.0 > duration + 15.0
    d._paused_time = 0.0
    d._next_timer = None
    d._last_cmd = "play"
    d.is_playing = True

    still_playing_calls = []

    async def _get_if_xiaoai_is_playing():
        still_playing_calls.append("called")
        return True  # 歌曲实际在播放

    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing

    d.get_offset_duration()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # still_playing=True 时不应切歌
    assert d._next_called == 0
    assert len(still_playing_calls) > 0
