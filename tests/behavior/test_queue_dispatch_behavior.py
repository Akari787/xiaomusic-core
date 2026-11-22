"""Queue dispatch (ADR-0007 step 3): the speaker owns the track switches.

The fake speaker plays a dispatched window on its own and fetches each item
through the StreamGateway; the player must follow those fetches instead of
switching tracks on a timer, and only talk to the cloud for user actions
and window refills.
"""

from __future__ import annotations

import pytest

from xiaomusic.const import PLAY_TYPE_ONE, PLAY_TYPE_SEQ, PLAY_TYPE_SIN
from xiaomusic.events import PLAYER_STATE_CHANGED
from xiaomusic.playback.runtime_state import PlaybackPhase

from .speaker_harness import build_rig

FOUR = {"L": ["a", "b", "c", "d"]}
FIVE = {"L": ["a", "b", "c", "d", "e"]}
SONG = 30.0


@pytest.mark.asyncio
async def test_list_loop_sends_one_queue_and_follows_local_advances(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR, queue=True)
    try:
        await rig.play_playlist("L", "a")
        await rig.wait_until(lambda: len(rig.speaker.heard()) >= 3, player_seconds=3 * SONG)
        await rig.settle(5)

        assert rig.speaker.queues() == [["a", "b", "c", "d"]]
        assert "loop:1" in rig.speaker.timeline()
        assert rig.speaker.heard()[:3] == ["a", "b", "c"]
        assert rig.player.get_cur_music() == "c"
        state = rig.state()
        assert state.phase == PlaybackPhase.PLAYING
        assert state.confirmed_track.display_name == "c"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_list_loop_wraps_on_the_speaker_without_new_dispatch(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists={"L": ["a", "b"]}, queue=True)
    try:
        await rig.play_playlist("L", "a")
        await rig.wait_until(lambda: len(rig.speaker.heard()) >= 3, player_seconds=3 * SONG)
        await rig.settle(5)

        assert rig.speaker.queues() == [["a", "b"]]
        assert rig.speaker.heard()[:3] == ["a", "b", "a"]
        assert rig.player.get_cur_music() == "a"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_window_refills_when_the_sentinel_is_reached(monkeypatch, tmp_path):
    rig = build_rig(
        monkeypatch,
        tmp_path,
        playlists=FIVE,
        queue=True,
        config_overrides={"queue_window_size": 2},
    )
    try:
        await rig.play_playlist("L", "a")
        await rig.wait_until(
            lambda: rig.speaker.heard().count("c") >= 1 and len(rig.speaker.heard()) >= 5,
            player_seconds=5 * SONG,
        )
        await rig.settle(5)

        assert rig.speaker.queues()[:2] == [
            ["a", "b", "<silence>"],
            ["c", "d", "<silence>"],
        ]
        assert rig.speaker.heard()[:5] == ["a", "b", "<silence>", "c", "d"]
        assert rig.player.get_cur_music() == "d"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_sequential_plays_to_the_end_and_stops(monkeypatch, tmp_path):
    rig = build_rig(
        monkeypatch, tmp_path, playlists={"L": ["a", "b", "c"]}, play_type=PLAY_TYPE_SEQ, queue=True
    )
    try:
        await rig.play_playlist("L", "a")
        await rig.wait_until(
            lambda: rig.state().phase == PlaybackPhase.STOPPED, player_seconds=4 * SONG + 30
        )

        assert rig.speaker.queues() == [["a", "b", "c"]]
        assert "loop:2" in rig.speaker.timeline()
        assert rig.speaker.heard() == ["a", "b", "c"]
        assert rig.state().phase == PlaybackPhase.STOPPED
        assert rig.player.get_cur_music() == "c"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_single_loop_repeats_on_the_speaker_without_redispatch(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR, play_type=PLAY_TYPE_ONE, queue=True)
    try:
        await rig.play_playlist("L", "b")
        await rig.settle(3 * SONG + 20)

        assert rig.speaker.queues() == [["b"]]
        assert "loop:0" in rig.speaker.timeline()
        assert set(rig.speaker.heard()) == {"b"}
        assert len(rig.speaker.heard()) >= 3
        assert rig.state().phase == PlaybackPhase.PLAYING
        assert rig.player.get_cur_music() == "b"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_single_play_stops_after_the_track(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR, play_type=PLAY_TYPE_SIN, queue=True)
    try:
        await rig.play_playlist("L", "b")
        await rig.wait_until(
            lambda: rig.state().phase == PlaybackPhase.STOPPED, player_seconds=2 * SONG + 20
        )

        assert rig.speaker.queues() == [["b"]]
        assert rig.speaker.heard() == ["b"]
        assert rig.state().phase == PlaybackPhase.STOPPED
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_manual_next_after_a_local_advance_starts_from_the_followed_track(
    monkeypatch, tmp_path
):
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR, queue=True)
    try:
        await rig.play_playlist("L", "a")
        await rig.wait_until(lambda: rig.player.get_cur_music() == "b", player_seconds=SONG + 10)
        await rig.settle(5)
        await rig.player.play_next()
        await rig.wait_until(lambda: len(rig.speaker.queues()) >= 2, player_seconds=10)
        await rig.settle(5)

        assert rig.speaker.queues() == [["a", "b", "c", "d"], ["c", "d", "a", "b"]]
        assert rig.player.get_cur_music() == "c"
        assert rig.state().phase == PlaybackPhase.PLAYING
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_switch_to_single_play_mid_track_stops_at_the_next_boundary(
    monkeypatch, tmp_path
):
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR, queue=True)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(10)
        await rig.player.set_play_type(PLAY_TYPE_SIN)
        await rig.wait_until(
            lambda: rig.state().phase == PlaybackPhase.STOPPED, player_seconds=SONG + 20
        )

        assert rig.speaker.queues() == [["a", "b", "c", "d"]]  # no interruption
        assert "loop:2" in rig.speaker.timeline()
        assert rig.state().phase == PlaybackPhase.STOPPED
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_switch_to_single_loop_mid_track_keeps_repeating_it(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR, queue=True)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(10)
        await rig.player.set_play_type(PLAY_TYPE_ONE)
        await rig.settle(3 * SONG)

        assert rig.speaker.queues() == [["a", "b", "c", "d"]]
        assert rig.speaker.timeline()[-1] == "loop:0"
        assert set(rig.speaker.heard()) == {"a"}
        assert rig.player.get_cur_music() == "a"
        assert rig.state().phase == PlaybackPhase.PLAYING
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_lost_fetch_falls_back_to_the_timer(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR, queue=True)
    rig.speaker.drop_fetch = {1}  # the speaker's fetch of "b" never arrives
    try:
        await rig.play_playlist("L", "a")
        await rig.wait_until(lambda: len(rig.speaker.queues()) >= 2, player_seconds=2 * SONG)
        await rig.settle(5)

        # The fallback timer fired after "a" plus the grace period and resynced
        # from the next track.
        assert rig.speaker.queues()[:2] == [["a", "b", "c", "d"], ["b", "c", "d", "a"]]
        assert rig.player.get_cur_music() == "b"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_api_play_hands_over_to_queue_dispatch_at_the_next_track(monkeypatch, tmp_path):
    """The v1 API path still dispatches its first track alone (merged in a
    later ADR-0007 step); from the next track on the speaker owns the queue."""
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR, queue=True)
    try:
        await rig.play_url("b", "L")
        await rig.wait_until(lambda: len(rig.speaker.heard()) >= 2, player_seconds=2 * SONG + 10)
        await rig.settle(5)

        assert rig.speaker.played() == ["b"]
        assert rig.speaker.queues() == [["c", "d", "a", "b"]]
        assert rig.player.get_cur_music() == "d"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_single_loop_restarts_the_position_on_every_repeat(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR, play_type=PLAY_TYPE_ONE, queue=True)
    try:
        await rig.play_playlist("L", "b")
        await rig.settle(2 * SONG + 10)  # third play of "b", 10 s in

        offset, duration = rig.player.get_offset_duration()
        assert duration == SONG
        assert 0 < offset < SONG / 2
        assert rig.speaker.queues() == [["b"]]
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_switch_to_sequential_mid_track_follows_the_next_track_without_redispatch(
    monkeypatch, tmp_path
):
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR, queue=True)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(10)
        await rig.player.set_play_type(PLAY_TYPE_SEQ)
        await rig.wait_until(lambda: rig.player.get_cur_music() == "b", player_seconds=SONG + 10)
        await rig.settle(5)

        assert rig.speaker.queues() == [["a", "b", "c", "d"]]
        assert rig.speaker.timeline()[-1] == "loop:2"
        assert rig.state().phase == PlaybackPhase.PLAYING
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_followed_track_reaches_the_webui_with_its_duration(monkeypatch, tmp_path):
    """The WebUI redraws on PLAYER_STATE_CHANGED; the last push after a local
    advance must already carry the followed track and its real duration."""
    rig = build_rig(
        monkeypatch, tmp_path, playlists=FOUR, durations={"b": 45.0}, queue=True
    )
    try:
        await rig.play_playlist("L", "a")
        await rig.wait_until(lambda: rig.player.get_cur_music() == "b", player_seconds=SONG + 10)
        await rig.settle(5)

        # The first push after "a" already shows "b" with its own duration:
        # no frame with the old title, a zero or the previous duration.
        first_b = next(i for i, seen in enumerate(rig.events.seen) if seen[0] == "b")
        assert rig.events.seen[first_b] == ("b", 45.0), rig.events.seen
        assert rig.events.seen[first_b - 1][0] == "a"  # one push, straight from a to b
        assert ("a", 45.0) not in rig.events.seen
        offset, _ = rig.player.get_offset_duration()
        assert offset < 10
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_single_loop_repeat_is_pushed_to_the_webui(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists=FOUR, play_type=PLAY_TYPE_ONE, queue=True)
    try:
        await rig.play_playlist("L", "b")
        await rig.settle(SONG - 5)
        pushes_before = rig.events.count(PLAYER_STATE_CHANGED)
        await rig.settle(10)  # the speaker starts "b" again

        assert rig.events.count(PLAYER_STATE_CHANGED) > pushes_before
        offset, _ = rig.player.get_offset_duration()
        assert offset < 10
    finally:
        await rig.close()
