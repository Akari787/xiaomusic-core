"""Playback behavior through public entry points only.

These tests are the safety net for refactoring device_player.py: they pin
what the speaker receives and what the player reports, not how the player
is built inside. See speaker_harness.py for the rules.
"""

from __future__ import annotations

import pytest

from xiaomusic.const import PLAY_TYPE_ALL, PLAY_TYPE_ONE, PLAY_TYPE_SEQ, PLAY_TYPE_SIN
from xiaomusic.playback.runtime_state import PlaybackPhase

from .speaker_harness import build_rig

SONG = 30.0  # default track length in player seconds


def assert_stop_precedes_every_play(timeline: list[str]) -> None:
    """Each track switch must clear the speaker before dispatching."""
    for i, entry in enumerate(timeline):
        if entry.startswith("play:"):
            previous = [e for e in timeline[:i] if not e.startswith("tts")]
            assert previous and previous[-1] in ("stop", "pause"), timeline


@pytest.fixture
def rig_factory(monkeypatch, tmp_path):
    rigs = []

    def make(**kwargs):
        rig = build_rig(monkeypatch, tmp_path, **kwargs)
        rigs.append(rig)
        return rig

    yield make
    # Cleanup happens in each test via ``await rig.close()``; this only keeps
    # references alive for the duration of the test.


# ── starting playback ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_playlist_play_dispatches_requested_track(rig_factory):
    rig = rig_factory()
    try:
        await rig.play_playlist("日语", "b")
        await rig.settle(5)

        assert rig.speaker.played() == ["b"]
        assert_stop_precedes_every_play(rig.speaker.timeline())
        assert rig.state().phase == PlaybackPhase.PLAYING
        assert rig.player.get_cur_music() == "b"
    finally:
        await rig.close()


# ── track end (auto-advance) per play mode ─────────────────────────────


@pytest.mark.asyncio
async def test_list_loop_advances_in_order_and_wraps(rig_factory):
    rig = rig_factory(play_type=PLAY_TYPE_ALL)
    try:
        await rig.play_playlist("日语", "b")
        await rig.settle(2 * SONG + 10)

        assert rig.speaker.played() == ["b", "c", "a"]
        # Auto-advance replaces the speaker queue directly (no preplay stop).
        timeline = rig.speaker.timeline()
        first = timeline.index("play:b")
        assert timeline[first:] == ["play:b", "play:c", "play:a"], timeline
        assert rig.state().phase == PlaybackPhase.PLAYING
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_sequential_stops_speaker_after_last_track(rig_factory):
    """Regression (2026-11-22): reaching the end used to leave the server in
    PLAYING and the speaker looping the last track."""
    rig = rig_factory(play_type=PLAY_TYPE_SEQ)
    try:
        await rig.play_playlist("日语", "b")
        await rig.settle(2 * SONG + 20)

        assert rig.speaker.played() == ["b", "c"]
        assert rig.speaker.timeline()[-1] == "stop"
        assert rig.state().phase == PlaybackPhase.STOPPED
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_single_play_stops_after_one_track(rig_factory):
    rig = rig_factory(play_type=PLAY_TYPE_SIN)
    try:
        await rig.play_playlist("日语", "a")
        await rig.settle(SONG + 20)

        assert rig.speaker.played() == ["a"]
        assert rig.speaker.timeline()[-1] == "stop"
        assert rig.state().phase == PlaybackPhase.STOPPED
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_single_loop_repeats_until_manual_next(rig_factory):
    rig = rig_factory(play_type=PLAY_TYPE_ONE)
    try:
        await rig.play_playlist("日语", "a")
        await rig.settle(2 * SONG + 10)
        assert rig.speaker.played() == ["a", "a", "a"]

        await rig.player.play_next()
        await rig.settle(5)
        assert rig.speaker.played()[-1] == "b"
    finally:
        await rig.close()


# ── manual control ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_manual_next_and_previous(rig_factory):
    rig = rig_factory()
    try:
        await rig.play_playlist("日语", "a")
        await rig.settle(5)
        await rig.player.play_next()
        await rig.settle(5)
        await rig.player.play_prev()
        await rig.settle(5)

        assert rig.speaker.played() == ["a", "b", "a"]
        assert_stop_precedes_every_play(rig.speaker.timeline())
        assert rig.player.get_cur_music() == "a"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_stop_clears_speaker_and_cancels_auto_advance(rig_factory):
    rig = rig_factory()
    try:
        await rig.play_playlist("日语", "a")
        await rig.settle(5)
        assert await rig.player.stop(arg1="notts") is True
        await rig.settle(2 * SONG)

        assert rig.speaker.played() == ["a"]
        assert rig.speaker.timeline()[-1] == "stop"
        assert rig.state().phase == PlaybackPhase.STOPPED
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_pause_then_resume_toggles_speaker(rig_factory):
    rig = rig_factory()
    try:
        await rig.play_playlist("日语", "a")
        await rig.settle(5)
        before = len(rig.speaker.commands)

        assert await rig.player.pause() is True
        await rig.settle(5)
        assert rig.state().phase == PlaybackPhase.PAUSED

        assert await rig.player.resume() is True
        await rig.settle(5)
        assert rig.state().phase == PlaybackPhase.PLAYING

        assert rig.speaker.timeline()[before:] == ["pause", "pause"]
        assert rig.speaker.played() == ["a"]  # resume never re-dispatches
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_pause_cancels_track_end_advance(rig_factory):
    rig = rig_factory()
    try:
        await rig.play_playlist("日语", "a")
        await rig.settle(5)
        assert await rig.player.pause() is True
        await rig.settle(2 * SONG)

        assert rig.speaker.played() == ["a"]
        assert rig.state().phase == PlaybackPhase.PAUSED
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_track_end_switches_without_status_round_trip(rig_factory):
    """The switch at track end must not wait on a speaker status query:
    while the server waits, the speaker audibly restarts the finished track."""
    rig = rig_factory()
    try:
        await rig.play_playlist("日语", "a")
        await rig.settle(5)
        rig.speaker.latency = {"player_get_status": 5.0}
        await rig.wait_until(lambda: len(rig.speaker.played()) >= 2, player_seconds=SONG + 5)

        # The next track went out without the 5 s status latency.
        assert rig.speaker.played() == ["a", "b"]
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_auto_advance_stop_can_be_turned_back_on(rig_factory):
    """auto_next_preplay_stop=True restores the stop before auto-advance, for
    hardware that will not switch without one."""
    rig = rig_factory(config_overrides={"auto_next_preplay_stop": True})
    try:
        await rig.play_playlist("日语", "a")
        await rig.wait_until(lambda: len(rig.speaker.played()) >= 2, player_seconds=SONG + 10)

        assert rig.speaker.played() == ["a", "b"]
        assert_stop_precedes_every_play(rig.speaker.timeline())
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_manual_next_still_stops_by_default(rig_factory):
    rig = rig_factory()
    try:
        await rig.play_playlist("日语", "a")
        await rig.settle(5)
        await rig.player.play_next()
        await rig.settle(5)

        assert rig.speaker.played() == ["a", "b"]
        assert_stop_precedes_every_play(rig.speaker.timeline())
    finally:
        await rig.close()


# ── failures ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preplay_stop_timeout_retries_and_keeps_playing(rig_factory):
    """Regression (2026-11-22 incident): a cloud timeout on the stop issued
    before switching tracks stalled playback; the speaker kept looping.
    Auto-advance no longer stops by default, so enable it to exercise the
    path (it is still used when configured, and by manual switches/retries)."""
    rig = rig_factory(config_overrides={"auto_next_preplay_stop": True})
    try:
        await rig.play_playlist("日语", "a")
        await rig.settle(5)
        rig.speaker.fail("player_stop", True)

        await rig.settle(SONG + 10)

        assert "player_stop!timeout" in rig.speaker.timeline()
        assert rig.speaker.played() == ["a", "b"]
        assert rig.state().phase == PlaybackPhase.PLAYING
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_manual_next_stop_timeout_retries_and_plays(rig_factory):
    rig = rig_factory()
    try:
        await rig.play_playlist("日语", "a")
        await rig.settle(5)
        rig.speaker.fail("player_stop", True)

        await rig.player.play_next()
        await rig.settle(15)

        assert "player_stop!timeout" in rig.speaker.timeline()
        assert rig.speaker.played() == ["a", "b"]
        assert rig.state().phase == PlaybackPhase.PLAYING
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_dispatch_timeouts_retry_same_track(rig_factory):
    rig = rig_factory()
    try:
        rig.speaker.fail("play_by_music_url", True, True)
        rig.speaker.fail("play_by_url", True, True)
        await rig.play_playlist("日语", "a")
        await rig.settle(15)

        timeouts = [e for e in rig.speaker.timeline() if e.endswith("!timeout")]
        assert len(timeouts) == 2
        assert rig.speaker.played() == ["a"]
        assert rig.state().phase == PlaybackPhase.PLAYING
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_persistent_dispatch_failure_advances_then_degrades(rig_factory):
    """Policy: retry the same track twice, then the next track twice, then
    stop auto-advancing and announce it."""
    rig = rig_factory()
    try:
        rig.speaker.fail("play_by_music_url", *([True] * 20))
        rig.speaker.fail("play_by_url", *([True] * 20))
        await rig.play_playlist("日语", "a")
        await rig.settle(60)

        timeline = rig.speaker.timeline()
        assert timeline.count("tts") == 1
        before_tts = timeline[: timeline.index("tts")]
        assert sum(e.endswith("!timeout") for e in before_tts) == 5
        # The announcement must not be followed by another attempt.
        after_tts = timeline[timeline.index("tts") + 1 :]
        assert not any(e.endswith("!timeout") or e.startswith("play:") for e in after_tts), timeline
        assert rig.state().phase == PlaybackPhase.FAILED

        settled = len(rig.speaker.commands)
        await rig.settle(60)
        assert len(rig.speaker.commands) == settled  # no endless retrying
    finally:
        await rig.close()
