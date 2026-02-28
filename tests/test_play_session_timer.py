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
    d._play_session_id = 1
    d._last_cmd = ""
    d._autonext_guard_task = None
    d.is_playing = True
    d.device = types.SimpleNamespace(did="did-test", play_type=PLAY_TYPE_ALL)

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
