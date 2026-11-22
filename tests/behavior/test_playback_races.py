"""Concurrency behavior through public entry points (slow speaker).

These pin the outcomes the staleness guards (session id, lifecycle token,
manual-navigation generation, stop barrier) exist to guarantee, so those
guards can be consolidated without changing what users observe.
"""

from __future__ import annotations

import asyncio

import pytest

from xiaomusic.playback.runtime_state import PlaybackPhase

from .speaker_harness import build_rig

FIVE = {"L": ["a", "b", "c", "d", "e"]}
SLOW = 1.0  # player seconds per cloud call


@pytest.mark.asyncio
async def test_rapid_next_presses_dispatch_only_the_final_target(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists=FIVE)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(5)
        # A slow stop guarantees each later press lands while the previous
        # target is still clearing the speaker, i.e. before it is dispatched.
        # (A play command already sent to the cloud cannot be recalled.)
        rig.speaker.latency = {"player_stop": 3.0, "play_by_music_url": SLOW, "play_by_url": SLOW}

        tasks = [asyncio.create_task(rig.player.play_next())]
        for _ in range(2):
            await rig.settle(0.3)
            tasks.append(asyncio.create_task(rig.player.play_next()))
        await asyncio.gather(*tasks)
        await rig.settle(10)

        assert rig.speaker.played() == ["a", "d"]
        assert rig.player.get_cur_music() == "d"
        assert rig.state().phase == PlaybackPhase.PLAYING
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_stop_during_slow_dispatch_wins(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path)
    try:
        await rig.play_playlist("日语", "a")
        await rig.settle(5)
        rig.speaker.latency = {"play_by_music_url": 3.0, "play_by_url": 3.0}

        switching = asyncio.create_task(rig.player.play_next())
        await rig.settle(1.0)
        assert await rig.player.stop(arg1="notts") is True
        await switching
        await rig.settle(40)

        timeline = rig.speaker.timeline()
        last_play = max(i for i, e in enumerate(timeline) if e.startswith("play:"))
        assert "stop" in timeline[last_play + 1 :]  # stop lands after the in-flight play
        assert rig.state().phase == PlaybackPhase.STOPPED
        settled = len(rig.speaker.commands)
        await rig.settle(40)
        assert len(rig.speaker.commands) == settled  # nothing resumes later
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_new_play_supersedes_play_still_dispatching(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists=FIVE)
    try:
        # Slow stop: the second request arrives while "a" is still clearing
        # the speaker, before its play command could have been sent.
        rig.speaker.latency = {"player_stop": 3.0, "play_by_music_url": 2.0, "play_by_url": 2.0}
        first = asyncio.create_task(rig.play_playlist("L", "a"))
        await rig.settle(0.5)
        await rig.play_playlist("L", "d")
        await first
        await rig.settle(10)

        assert rig.speaker.played() == ["d"]
        assert rig.player.get_cur_music() == "d"
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_pause_while_switching_is_rejected_and_playback_continues(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path)
    try:
        await rig.play_playlist("日语", "a")
        await rig.settle(5)
        rig.speaker.latency = {"play_by_music_url": 3.0, "play_by_url": 3.0}

        switching = asyncio.create_task(rig.player.play_next())
        await rig.settle(1.0)
        assert await rig.player.pause() is False
        await switching
        await rig.settle(5)

        assert rig.speaker.played() == ["a", "b"]
        assert rig.state().phase == PlaybackPhase.PLAYING
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_manual_next_cancels_pending_track_end_timer(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists=FIVE)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(20)  # a's track-end timer is still pending
        await rig.player.play_next()
        await rig.settle(20)  # past a's original end time, before b's

        assert rig.speaker.played() == ["a", "b"]
        assert rig.player.get_cur_music() == "b"
    finally:
        await rig.close()
