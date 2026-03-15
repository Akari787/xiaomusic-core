import sys
import types

import pytest
from xiaomusic.const import PLAY_TYPE_ONE, PLAY_TYPE_SIN

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
    d._play_list = ["song-a", "song-b"]
    d.get_cur_music = lambda: "song-a"
    d.get_next_music = lambda: "song-b"

    played: list[str] = []

    async def _play(name="", search_key="", preserve_playlist=False):  # noqa: ARG001
        played.append(name)

    d._play = _play

    await d.play_next()
    assert played == ["song-b"]


@pytest.mark.asyncio
async def test_manual_play_next_preserves_current_playlist():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.device = types.SimpleNamespace(play_type=PLAY_TYPE_ONE, cur_playlist="BGM")
    d.log = types.SimpleNamespace(info=lambda *args, **kwargs: None, debug=lambda *args, **kwargs: None)
    d._play_list = ["song-a", "song-b"]
    d.get_cur_music = lambda: "song-a"
    d.get_next_music = lambda: "song-b"

    async def _playmusic(name):
        d.device.cur_music = name

    async def _check_and_download_music(name, search_key, allow_download):  # noqa: ARG001
        return True

    d._playmusic = _playmusic
    d._check_and_download_music = _check_and_download_music
    d.update_playlist = lambda: (_ for _ in ()).throw(AssertionError("playlist should not be rebuilt"))
    d.find_cur_playlist = lambda name: (_ for _ in ()).throw(AssertionError("playlist should not be changed"))
    d.xiaomusic = types.SimpleNamespace(
        music_library=types.SimpleNamespace(find_real_music_name=lambda name, n=1: [name])
    )
    d.config = types.SimpleNamespace(verbose=False)

    await d.play_next()
    assert d.device.cur_playlist == "BGM"


@pytest.mark.asyncio
async def test_manual_play_prev_advances_even_in_single_mode():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.device = types.SimpleNamespace(play_type=PLAY_TYPE_SIN)
    d.log = types.SimpleNamespace(info=lambda *args, **kwargs: None)
    d._play_list = ["song-a", "song-b"]
    d.get_cur_music = lambda: "song-b"
    d.get_prev_music = lambda: "song-a"

    played: list[str] = []

    async def _play(name="", search_key="", preserve_playlist=False):  # noqa: ARG001
        played.append(name)

    d._play = _play

    await d.play_prev()
    assert played == ["song-a"]

