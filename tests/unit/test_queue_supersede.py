"""ADR-0008 step 1: an accepted user command supersedes the window on the
speaker, so its fetches can no longer trigger automatic dispatches."""

from __future__ import annotations

import pytest

from tests.behavior.speaker_harness import build_rig

FIVE = {"L": ["a", "b", "c", "d", "e"]}


@pytest.mark.asyncio
async def test_manual_next_supersedes_the_current_window(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists=FIVE, queue=True)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(5)
        window = rig.player._queue_dispatch
        assert window and not window.get("superseded")

        await rig.player.play_next()

        assert window.get("superseded") is True
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_stop_supersedes_the_current_window(monkeypatch, tmp_path):
    rig = build_rig(monkeypatch, tmp_path, playlists=FIVE, queue=True)
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(5)
        window = rig.player._queue_dispatch

        await rig.player.stop(arg1="notts")

        assert window.get("superseded") is True
    finally:
        await rig.close()


@pytest.mark.asyncio
async def test_superseded_window_sentinel_does_not_dispatch(monkeypatch, tmp_path):
    rig = build_rig(
        monkeypatch,
        tmp_path,
        playlists=FIVE,
        queue=True,
        config_overrides={"queue_window_size": 2},
    )
    try:
        await rig.play_playlist("L", "a")
        await rig.settle(5)
        queues_before = list(rig.speaker.queues())
        window = rig.player._queue_dispatch
        sentinel = rig.player.xiaomusic.stream_gateway.verify(
            *_ref_parts(rig, window["sid"], window["sentinel_next"])
        )
        assert sentinel is not None

        rig.player._supersede_queue(reason="test")
        rig.player.on_track_fetched(sentinel)
        await rig.settle(5)

        assert rig.speaker.queues() == queues_before  # no window refill
        assert rig.player.get_cur_music() == "a"
    finally:
        await rig.close()


def _ref_parts(rig, queue_sid, next_index):
    from urllib.parse import parse_qs, unquote, urlparse

    url = rig.player.xiaomusic.stream_gateway.sentinel_url(rig.player.did, str(queue_sid), next_index)
    parsed = urlparse(url)
    did, queue, index, source, item = [unquote(p) for p in parsed.path.split("/")][3:8]
    return did, queue, int(index), source, item, parse_qs(parsed.query)["sig"][0]
