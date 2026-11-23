"""Concurrency behavior with queue dispatch (ADR-0007) and a slow cloud.

The same outcomes test_playback_races.py pins for single-track dispatch,
for the default path: the speaker owns a window and the player follows its
fetches. These are the safety net for merging the staleness counters into
one version (ADR-0008 steps 2-3): they must keep passing unchanged.
"""

from __future__ import annotations

import asyncio

import pytest

from xiaomusic.playback.runtime_state import PlaybackPhase

from .speaker_harness import build_rig

FIVE = {"L": ["a", "b", "c", "d", "e"]}
SONG = 30.0


def _queue_rig(monkeypatch, tmp_path, **kwargs):
    return build_rig(monkeypatch, tmp_path, playlists=FIVE, queue=True, **kwargs)


@pytest.mark.asyncio
async def test_rapid_next_presses_dispatch_only_the_final_target(monkeypatch, tmp_path):
    rig = _queue_rig(monkeypatch, tmp_path)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(5)
        rig.speaker.latency = {"player_stop": 3.0, "ubus_request": 1.0}

        tasks = [asyncio.create_task(rig.player.play_next())]
        for _ in range(2):
            await rig.settle(0.3)
            tasks.append(asyncio.create_task(rig.player.play_next()))
        await asyncio.gather(*tasks)
        await rig.settle(10)

        assert [q[0] for q in rig.speaker.queues()] == ["a", "d"]
        assert rig.player.get_cur_music() == "d"
        assert rig.state().phase == PlaybackPhase.PLAYING
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_stop_during_slow_queue_dispatch_wins(monkeypatch, tmp_path):
    rig = _queue_rig(monkeypatch, tmp_path)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(5)
        rig.speaker.latency = {"ubus_request": 3.0}

        switching = asyncio.create_task(rig.player.play_next())
        await rig.settle(1.0)
        assert await rig.player.stop(arg1="notts") is True
        await switching
        await rig.settle(40)

        timeline = rig.speaker.timeline()
        last_queue = max(i for i, e in enumerate(timeline) if e.startswith("queue:"))
        assert "stop" in timeline[last_queue + 1 :]  # stop lands after the in-flight dispatch
        assert rig.state().phase == PlaybackPhase.STOPPED
        settled = len(rig.speaker.commands)
        await rig.settle(2 * SONG)
        assert len(rig.speaker.commands) == settled  # nothing resumes later
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_new_play_supersedes_play_still_dispatching(monkeypatch, tmp_path):
    rig = _queue_rig(monkeypatch, tmp_path)
    try:
        rig.speaker.latency = {"player_stop": 3.0, "ubus_request": 2.0}
        first = asyncio.create_task(rig.play_playlist("L", "a"))
        await rig.settle(0.5)
        await rig.play_playlist("L", "d")
        await first
        await rig.settle(10)

        assert [q[0] for q in rig.speaker.queues()] == ["d"]
        assert rig.player.get_cur_music() == "d"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_manual_next_cancels_the_old_window_fallback_timer(monkeypatch, tmp_path):
    rig = _queue_rig(monkeypatch, tmp_path)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(20)  # a's fallback timer is pending
        await rig.player.play_next()
        await rig.settle(20)  # past a's end + grace, before b's end

        assert [q[0] for q in rig.speaker.queues()] == ["a", "b"]
        assert rig.player.get_cur_music() == "b"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_stop_while_waiting_for_the_start_fetch(monkeypatch, tmp_path):
    rig = _queue_rig(monkeypatch, tmp_path)
    rig.speaker.drop_start_fetch = 1  # accepted, never fetched
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(5)
        assert rig.state().phase != PlaybackPhase.PLAYING
        assert await rig.player.stop(arg1="notts") is True
        await rig.settle(30)  # past the start-fetch timeout

        assert rig.state().phase == PlaybackPhase.STOPPED
        assert len(rig.speaker.queues()) == 1  # no retry after the stop
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_play_right_after_stop_plays(monkeypatch, tmp_path):
    rig = _queue_rig(monkeypatch, tmp_path)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(5)
        await rig.player.stop(arg1="notts")
        # The production entry (v1 API); it queues behind the stop barrier.
        await rig.play_url("c", "L")
        await rig.settle(10)

        assert [q[0] for q in rig.speaker.queues()] == ["a", "c"]
        assert rig.player.get_cur_music() == "c"
        assert rig.state().phase == PlaybackPhase.PLAYING
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_api_play_replaces_a_playing_window(monkeypatch, tmp_path):
    rig = _queue_rig(monkeypatch, tmp_path)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(5)
        await rig.play_url("d", "L")
        await rig.wait_until(lambda: len(rig.speaker.heard()) >= 3, player_seconds=2 * SONG)
        await rig.settle(5)

        assert [q[0] for q in rig.speaker.queues()] == ["a", "d"]
        assert rig.speaker.heard()[1:3] == ["d", "e"]  # the old window is gone
        assert rig.player.get_cur_music() == "e"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_old_window_fetch_after_a_new_dispatch_is_ignored(monkeypatch, tmp_path):
    """A late fetch of the previous window (buffered request, slow speaker)
    must not move the current track."""
    rig = _queue_rig(monkeypatch, tmp_path)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(5)
        old_refs = list(rig.speaker.last_refs)
        await rig.player.play_next()
        await rig.settle(5)
        assert rig.player.get_cur_music() == "b"

        rig.speaker.gateway.tracker.observe(old_refs[2], None)  # old window's "c"
        await rig.settle(5)

        assert rig.player.get_cur_music() == "b"
        assert len(rig.speaker.queues()) == 2
    finally:
        await rig.close()
