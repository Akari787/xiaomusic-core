from types import SimpleNamespace

from xiaomusic.music_library import MusicLibrary


def _ml(base_url: str) -> MusicLibrary:
    ml = MusicLibrary.__new__(MusicLibrary)
    ml.config = SimpleNamespace(jellyfin_base_url=base_url)
    return ml


def test_is_jellyfin_url_scheme_mismatch_ok():
    ml = _ml("http://jellyfin.local:8096")
    assert ml.is_jellyfin_url("https://jellyfin.local:8096/Audio/stream")


def test_is_jellyfin_url_base_path_prefix_ok():
    ml = _ml("http://jf.local/jellyfin")
    assert ml.is_jellyfin_url("http://jf.local/jellyfin/Audio/stream")
    assert not ml.is_jellyfin_url("http://jf.local/other/Audio/stream")


def test_is_jellyfin_url_port_mismatch_blocked():
    ml = _ml("http://jf.local:8096")
    assert not ml.is_jellyfin_url("http://jf.local:9999/Audio/stream")
