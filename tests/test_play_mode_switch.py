import asyncio
import sys
import types

import pytest

from xiaomusic.const import PLAY_TYPE_ONE, PLAY_TYPE_SIN
from xiaomusic.playback.runtime_state import PlaybackRuntimeState

if "miservice" not in sys.modules:
    sys.modules["miservice"] = types.SimpleNamespace(
        miio_command=lambda *args, **kwargs: None
    )

if "opencc" not in sys.modules:

    class _OpenCC:
        def __init__(self, *_args, **_kwargs):
            pass

        def convert(self, text):
            return text

    sys.modules["opencc"] = types.SimpleNamespace(OpenCC=_OpenCC)

from xiaomusic.device_player import XiaoMusicDevice


@pytest.mark.asyncio
async def test_device_set_play_type_can_skip_playlist_refresh():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.device = types.SimpleNamespace(play_type=2)

    published = {"count": 0}

    class _EventBus:
        def publish(self, _event):
            published["count"] += 1

    d.event_bus = _EventBus()
    d.config = types.SimpleNamespace(get_play_type_tts=lambda _pt: "tts")

    calls = {"update": 0, "tts": 0}

    async def _do_tts(_value):
        calls["tts"] += 1

    def _update_playlist():
        calls["update"] += 1

    d.do_tts = _do_tts
    d.update_playlist = _update_playlist

    await d.set_play_type(play_type=1, dotts=False, refresh_playlist=False)

    assert d.device.play_type == 1
    assert published["count"] == 1
    assert calls["tts"] == 0
    assert calls["update"] == 0


@pytest.mark.asyncio
async def test_manual_play_next_advances_even_in_one_mode():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.device = types.SimpleNamespace(play_type=PLAY_TYPE_ONE)
    d.log = types.SimpleNamespace(info=lambda *args, **kwargs: None)
    d._play_list_items = [{"display_name": "song-a"}, {"display_name": "song-b"}]
    d._current_index = 0
    d.get_cur_music = lambda: "song-a"
    d.get_next_music = lambda **kwargs: "song-b"
    d._runtime_state = PlaybackRuntimeState()
    d._command_arbiter = None
    d._manual_nav_lock = asyncio.Lock()
    d._manual_nav_generation = 0
    d._manual_nav_target = None
    d._ensure_manual_navigation_state = lambda: None
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )

    played: list[str] = []
    dispatch_done = asyncio.Event()

    async def _play(name="", search_key="", preserve_playlist=False,
                    confirm_start_in_background=False, fast_stop=False,
                    **kwargs):  # noqa: ARG001
        played.append(name)
        dispatch_done.set()

    d._play = _play
    d._stage_playlist_navigation_transition = lambda name, *, reason: None
    # Override settle to be instant
    settle_done = asyncio.Event()
    d._wait_manual_navigation_settle = lambda: settle_done.wait()

    try:
        await d.play_next()
        settle_done.set()
        await asyncio.wait_for(dispatch_done.wait(), timeout=5)
        assert played == ["song-b"]
    finally:
        settle_done.set()
        dispatch_done.set()
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_manual_play_next_preserves_current_playlist():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.device = types.SimpleNamespace(play_type=PLAY_TYPE_ONE, cur_playlist="BGM")
    d.log = types.SimpleNamespace(info=lambda *args, **kwargs: None, debug=lambda *args, **kwargs: None)
    d._play_list_items = [{"display_name": "song-a"}, {"display_name": "song-b"}]
    d._current_index = 0
    d.get_cur_music = lambda: "song-a"
    d.get_next_music = lambda **kwargs: "song-b"
    d._runtime_state = PlaybackRuntimeState()
    d._command_arbiter = None
    d._manual_nav_lock = asyncio.Lock()
    d._manual_nav_generation = 0
    d._manual_nav_target = None
    d._ensure_manual_navigation_state = lambda: None

    async def _playmusic(name, *, confirm_start_in_background=False, fast_stop=False):  # noqa: ARG001
        d.device.cur_music = name

    async def _check_and_download_music(name, search_key, allow_download):  # noqa: ARG001
        return True

    d._playmusic = _playmusic
    d._check_and_download_music = _check_and_download_music
    d.update_playlist = lambda: (_ for _ in ()).throw(AssertionError("playlist should not be rebuilt"))
    d.find_cur_playlist = lambda name: (_ for _ in ()).throw(AssertionError("playlist should not be changed"))
    d._stage_playlist_navigation_transition = lambda name, *, reason: None
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(
            find_real_music_name=lambda name, n=1: [name],
            is_music_exist=lambda n: True,
        ),
    )
    d.config = types.SimpleNamespace(verbose=False)

    try:
        await d.play_next()
        assert d.device.cur_playlist == "BGM"
    finally:
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_manual_play_next_stages_target_index_and_resets_progress_before_dispatch():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)

    d.device = types.SimpleNamespace(
        did="did-1",
        play_type=PLAY_TYPE_ONE,
        cur_playlist="BGM",
        cur_music="song-a",
        playlist2music={"BGM": "song-a"},
    )
    d.event_bus = types.SimpleNamespace(
        publish=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("staging should not publish duplicate player_state events")
        )
    )
    d.log = types.SimpleNamespace(info=lambda *args, **kwargs: None)
    d._play_list_items = [{"display_name": "song-a"}, {"display_name": "song-b"}]
    d._current_index = 0
    d.is_playing = True
    d._start_time = 123.0
    d._paused_time = 5.0
    d._duration = 99.0
    d._runtime_state = PlaybackRuntimeState()
    d._command_arbiter = None
    d._manual_nav_lock = asyncio.Lock()
    d._manual_nav_generation = 0
    d._manual_nav_target = None
    d._ensure_manual_navigation_state = lambda: None
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )
    d.get_cur_music = lambda: d.device.cur_music
    d.get_next_music = lambda **kwargs: "song-b"

    captured: list[tuple[str, bool]] = []
    dispatch_done = asyncio.Event()

    async def _play(name="", search_key="", preserve_playlist=False,
                    confirm_start_in_background=False, fast_stop=False,
                    **kwargs):  # noqa: ARG001
        captured.append((name, preserve_playlist))
        dispatch_done.set()
        return True

    d._play = _play
    # Override settle to be instant for deterministic test
    settle_done = asyncio.Event()
    d._wait_manual_navigation_settle = lambda: settle_done.wait()

    try:
        await d.play_next()
        settle_done.set()
        # Wait for arbiter executor to dispatch
        await asyncio.wait_for(dispatch_done.wait(), timeout=5)

        assert captured == [("song-b", True)]
        assert d.device.cur_music == "song-b"
        assert d.device.playlist2music["BGM"] == "song-b"
        assert d._current_index == 1
        assert d.is_playing is False
        assert d._start_time == 0
        assert d._paused_time == 0
        assert d._duration == 0
        assert d._last_cmd == "play_next"
    finally:
        settle_done.set()
        dispatch_done.set()
        await d.close_command_arbiter()


@pytest.mark.asyncio
async def test_manual_play_prev_advances_even_in_single_mode():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.device = types.SimpleNamespace(play_type=PLAY_TYPE_SIN)
    d.log = types.SimpleNamespace(info=lambda *args, **kwargs: None)
    d._play_list_items = [{"display_name": "song-a"}, {"display_name": "song-b"}]
    d._current_index = 1
    d._find_playlist_index = lambda display_name="": (1 if display_name == "song-b" else -1)
    d.get_cur_music = lambda: "song-b"
    d.get_prev_music = lambda: "song-a"
    d._runtime_state = PlaybackRuntimeState()
    d._command_arbiter = None
    d._manual_nav_lock = asyncio.Lock()
    d._manual_nav_generation = 0
    d._manual_nav_target = None
    d._ensure_manual_navigation_state = lambda: None
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(is_music_exist=lambda n: True),
    )

    played: list[str] = []
    dispatch_done = asyncio.Event()

    async def _play(name="", search_key="", preserve_playlist=False,
                    confirm_start_in_background=False, fast_stop=False,
                    **kwargs):  # noqa: ARG001
        played.append(name)
        dispatch_done.set()

    d._play = _play
    d._stage_playlist_navigation_transition = lambda name, *, reason: None
    # Override settle to be instant for deterministic test
    settle_done = asyncio.Event()
    d._wait_manual_navigation_settle = lambda: settle_done.wait()

    try:
        await d.play_prev()
        settle_done.set()
        await asyncio.wait_for(dispatch_done.wait(), timeout=5)
        assert played == ["song-a"]
    finally:
        settle_done.set()
        dispatch_done.set()
        await d.close_command_arbiter()

