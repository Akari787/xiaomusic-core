import sys
import types

import pytest

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
