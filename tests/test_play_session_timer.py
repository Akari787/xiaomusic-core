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

    async def _play_next():
        d._next_called += 1

    async def _group_force_stop_xiaoai():
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
