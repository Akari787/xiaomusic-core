"""SpeakerQueue window planning (ADR-0007 step 3)."""

from __future__ import annotations

import pytest

from xiaomusic.const import (
    PLAY_TYPE_ALL,
    PLAY_TYPE_ONE,
    PLAY_TYPE_RND,
    PLAY_TYPE_SEQ,
    PLAY_TYPE_SIN,
)
from xiaomusic.speaker_queue import (
    LOOP_LIST,
    LOOP_ONCE,
    LOOP_SINGLE,
    Window,
    plan_window,
)


def test_single_loop_is_current_track_only():
    w = plan_window(PLAY_TYPE_ONE, 50, 7, size=20)
    assert w == Window((7,), LOOP_SINGLE, None)


def test_single_play_stops_after_current_track():
    w = plan_window(PLAY_TYPE_SIN, 50, 7, size=20)
    assert w == Window((7,), LOOP_ONCE, None)


@pytest.mark.parametrize("play_type", [PLAY_TYPE_ALL, PLAY_TYPE_RND])
def test_list_loop_window_wraps_and_ends_with_sentinel(play_type):
    w = plan_window(play_type, 25, 20, size=10)
    assert w.indices == (20, 21, 22, 23, 24, 0, 1, 2, 3, 4)
    assert w.loop_type == LOOP_LIST
    assert w.sentinel_next == 5


def test_list_loop_short_playlist_is_sent_whole_without_sentinel():
    w = plan_window(PLAY_TYPE_ALL, 4, 2, size=20)
    assert w.indices == (2, 3, 0, 1)
    assert w.sentinel_next is None
    assert w.loop_type == LOOP_LIST


def test_list_loop_one_song_playlist_is_one_item_without_sentinel():
    w = plan_window(PLAY_TYPE_ALL, 1, 0, size=20)
    assert w == Window((0,), LOOP_LIST, None)


def test_sequential_window_has_sentinel_until_last_window():
    w = plan_window(PLAY_TYPE_SEQ, 25, 3, size=10)
    assert w.indices == tuple(range(3, 13))
    assert w.loop_type == LOOP_ONCE
    assert w.sentinel_next == 13

    last = plan_window(PLAY_TYPE_SEQ, 25, 18, size=10)
    assert last.indices == tuple(range(18, 25))
    assert last.sentinel_next is None


def test_sequential_window_ending_exactly_at_list_end_has_no_sentinel():
    w = plan_window(PLAY_TYPE_SEQ, 20, 10, size=10)
    assert w.indices == tuple(range(10, 20))
    assert w.sentinel_next is None


@pytest.mark.parametrize("start", [-1, 5])
def test_start_out_of_range_is_rejected(start):
    with pytest.raises(ValueError):
        plan_window(PLAY_TYPE_ALL, 5, start)


def test_empty_playlist_is_rejected():
    with pytest.raises(ValueError):
        plan_window(PLAY_TYPE_ALL, 0, 0)
