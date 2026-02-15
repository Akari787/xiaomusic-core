import asyncio
import logging
from types import SimpleNamespace

from xiaomusic.config import Device
from xiaomusic.const import PLAY_TYPE_RND
from xiaomusic.device_player import XiaoMusicDevice


class DummyMusicLibrary:
    def __init__(self, direct_url: str, proxy_url: str):
        self._direct_url = direct_url
        self._proxy_url = proxy_url
        self.music_list = {"全部": ["song1"]}

    async def get_music_url(self, name: str):
        assert name == "song1"
        # (play_url, origin_url) where origin_url == play_url indicates
        # jellyfin auto-fallback candidate.
        return self._direct_url, self._direct_url

    def get_proxy_url(self, origin_url: str, *, name: str = "") -> str:
        assert origin_url == self._direct_url
        return self._proxy_url

    async def get_music_duration(self, name: str) -> float:
        return 0.0


class DummyAnalytics:
    async def send_play_event(self, *args, **kwargs):
        return


def test_jellyfin_auto_falls_back_to_proxy_when_not_playing(monkeypatch):
    direct = "http://jellyfin.local/Audio/stream"
    proxy = "http://host:58090/proxy/music?urlb64=xxx"

    cfg = SimpleNamespace(
        verbose=False,
        delay_sec=0,
        continue_play=False,
        use_music_api=False,
        ffmpeg_location="",
        jellyfin_proxy_mode="auto",
        jellyfin_base_url="http://jellyfin.local",
    )

    xm = SimpleNamespace(
        config=cfg,
        log=logging.getLogger("t"),
        auth_manager=SimpleNamespace(mina_service=None),
        music_library=DummyMusicLibrary(direct, proxy),
        analytics=DummyAnalytics(),
        device_manager=SimpleNamespace(get_group_device_id_list=lambda group: ["d1"]),
    )

    dev = Device(
        did="d1",
        device_id="d1",
        hardware="",
        name="",
        play_type=PLAY_TYPE_RND,
        cur_playlist="全部",
        playlist2music={},
    )

    d = XiaoMusicDevice(xm, dev, group_name="g")

    async def _noop(*args, **kwargs):
        return

    calls = []

    async def fake_group_player_play(url, name=""):
        calls.append(url)
        return [{"ok": True}]

    # First status check false -> trigger fallback; second true -> accept proxy.
    status_iter = iter([False, True])

    async def fake_get_if_xiaoai_is_playing():
        return next(status_iter)

    async def fast_sleep(_sec):
        return

    monkeypatch.setattr(d, "cancel_group_next_timer", _noop)
    monkeypatch.setattr(d, "group_force_stop_xiaoai", _noop)
    monkeypatch.setattr(d, "group_player_play", fake_group_player_play)
    monkeypatch.setattr(d, "get_if_xiaoai_is_playing", fake_get_if_xiaoai_is_playing)
    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    asyncio.run(d._playmusic("song1"))

    assert calls[0] == direct
    assert calls[1] == proxy
