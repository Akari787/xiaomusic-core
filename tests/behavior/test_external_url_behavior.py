"""External-URL (v1 API) playback through the device entry point.

PlaybackFacade/Coordinator resolve the media and hand the device a URL plus
optional playlist context; afterwards auto-advance and manual navigation run
on the device's playlist session. These tests pin that hand-off.
"""

from __future__ import annotations

import pytest

from xiaomusic.playback.runtime_state import PlaybackPhase

from .speaker_harness import build_rig

FOUR = {"L": ["a", "b", "c", "d"]}
SONG = 30.0


@pytest.mark.asyncio
async def test_api_play_with_playlist_context_then_auto_advances(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR)
    try:
        await rig.play_url("b", "L")
        await rig.wait_until(lambda: len(rig.speaker.played()) >= 3, player_seconds=2 * SONG + 10)
        await rig.settle(5)  # let the last start confirm

        assert rig.speaker.played() == ["b", "c", "d"]
        assert rig.player.get_cur_music() == "d"
        assert rig.state().phase == PlaybackPhase.PLAYING
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_api_play_then_manual_next_then_stop_keeps_last_track(monkeypatch, tmp_path):
    """Seen once on the test server under a slow cloud: after API play, manual
    next and stop, the reported track reverted to the API-played one."""
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR)
    try:
        await rig.play_url("a", "L")
        await rig.settle(5)
        await rig.player.play_next()
        await rig.settle(5)
        assert await rig.player.stop(arg1="notts") is True
        await rig.settle(5)

        state = rig.state()
        assert rig.speaker.played() == ["a", "b"]
        assert state.phase == PlaybackPhase.STOPPED
        assert rig.player.get_cur_music() == "b"
        assert state.confirmed_track is not None
        assert state.confirmed_track.display_name == "b"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_api_play_without_context_does_not_auto_advance_into_old_list(monkeypatch, tmp_path):
    """A bare URL (no playlist context) is a one-off; it must not continue
    into whatever playlist the device had before."""
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(5)
        await rig.play_url("c")
        await rig.settle(SONG + 10)

        assert rig.speaker.played()[:2] == ["a", "c"]
        assert len(rig.speaker.played()) == 2, rig.speaker.timeline()
    finally:
        await rig.close()
