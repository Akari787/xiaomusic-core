from __future__ import annotations

from xiaomusic.playback.facade import PlaybackFacade


def test_playback_facade_no_longer_exposes_legacy_methods() -> None:
    facade = PlaybackFacade(object())

    for name in ("stop_legacy", "pause_legacy", "tts_legacy", "set_volume_legacy"):
        assert not hasattr(facade, name)
