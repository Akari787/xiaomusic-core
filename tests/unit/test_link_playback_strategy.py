import pytest


class _FakeMusicLibrary:
    def __init__(self):
        self.proxy_calls = []

    def get_proxy_url(self, url, name=""):
        self.proxy_calls.append((url, name))
        return f"http://127.0.0.1:58090/proxy?url={url}"

    def is_jellyfin_url(self, url):
        return "192.168.0.4:30013" in url


@pytest.mark.unit
def test_link_strategy_normalizes_bilibili_video_url():
    from xiaomusic.playback.link_strategy import LinkPlaybackStrategy  # noqa: PLC0415

    s = LinkPlaybackStrategy(music_library=_FakeMusicLibrary(), log=None)
    raw = "https://www.bilibili.com/video/BV1JbZCBvEdp/?spm_id_from=333.337.search-card.all.click&vd_source=abc"
    assert s.normalize_input_url(raw) == "https://www.bilibili.com/video/BV1JbZCBvEdp"


@pytest.mark.unit
def test_link_strategy_selects_relay_for_youtube_and_bilibili():
    from xiaomusic.playback.link_strategy import LinkPlaybackStrategy  # noqa: PLC0415

    s = LinkPlaybackStrategy(music_library=_FakeMusicLibrary(), log=None)
    assert s.should_use_relay("https://www.youtube.com/watch?v=vNG3-GRjrAo")
    assert s.should_use_relay("https://www.bilibili.com/video/BV1JbZCBvEdp")
    assert not s.should_use_relay("https://lhttp.qtfm.cn/live/4915/64k.mp3")


@pytest.mark.unit
def test_link_strategy_build_proxy_url_uses_shared_music_library_path():
    from xiaomusic.playback.link_strategy import LinkPlaybackStrategy  # noqa: PLC0415

    lib = _FakeMusicLibrary()
    s = LinkPlaybackStrategy(music_library=lib, log=None)
    out = s.build_proxy_url("https://example.com/live.m3u8", name="x")
    assert out.startswith("http://127.0.0.1:58090/proxy")
    assert lib.proxy_calls == [("https://example.com/live.m3u8", "x")]


@pytest.mark.unit
def test_link_strategy_detects_jellyfin_auto_fallback_candidate():
    from xiaomusic.playback.link_strategy import LinkPlaybackStrategy  # noqa: PLC0415

    s = LinkPlaybackStrategy(music_library=_FakeMusicLibrary(), log=None)
    url = "http://192.168.0.4:30013/Audio/abc/stream.mp3?api_key=xx"
    assert s.should_jellyfin_auto_fallback("auto", url, url)
    assert not s.should_jellyfin_auto_fallback("on", url, url)
    assert not s.should_jellyfin_auto_fallback("auto", url, "http://other")
