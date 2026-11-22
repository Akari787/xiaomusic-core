import logging
import sys
import types

import pytest

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


@pytest.mark.parametrize(
    ("hardware", "expected"),
    [("OH2P", "play_by_music_url"), ("OTHER", "play_by_url")],
)
def test_resolve_play_url_dispatch_mode_follows_hardware(hardware, expected):
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.device = types.SimpleNamespace(hardware=hardware)

    assert d._resolve_play_url_dispatch_mode() == expected


@pytest.mark.asyncio
async def test_play_one_url_play_by_url_hardware_uses_expected_mina_call():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("test-play-one-url")
    d.device = types.SimpleNamespace(hardware="OTHER")

    calls = []

    async def _mina_call(method_name, *args, **kwargs):
        calls.append((method_name, args, kwargs))
        return {"code": 0}

    async def _get_audio_id(_name):
        return "aid"

    d.auth_manager = types.SimpleNamespace(mina_call=_mina_call)
    d._get_audio_id = _get_audio_id

    out = await d.play_one_url("device-1", "http://example.com/a.mp3", "song-a")

    assert out == {"code": 0}
    assert calls == [
        (
            "play_by_url",
            ("device-1", "http://example.com/a.mp3"),
            {"retry": 1, "ctx": "play_one_url"},
        )
    ]


@pytest.mark.asyncio
async def test_play_one_url_music_api_hardware_uses_expected_mina_call():
    d = XiaoMusicDevice.__new__(XiaoMusicDevice)
    d.log = logging.getLogger("test-play-one-music-url")
    d.device = types.SimpleNamespace(hardware="OH2P")

    calls = []

    async def _mina_call(method_name, *args, **kwargs):
        calls.append((method_name, args, kwargs))
        return {"code": 0}

    async def _get_audio_id(_name):
        return "aid"

    d.auth_manager = types.SimpleNamespace(mina_call=_mina_call)
    d._get_audio_id = _get_audio_id

    out = await d.play_one_url("device-1", "http://example.com/a.mp3", "song-a")

    assert out == {"code": 0}
    assert calls == [
        (
            "play_by_music_url",
            ("device-1", "http://example.com/a.mp3"),
            {"audio_id": "aid", "retry": 1, "ctx": "play_one_url_music_api"},
        )
    ]
