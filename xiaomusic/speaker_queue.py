"""SpeakerQueue: which tracks to hand the speaker in one dispatch (ADR-0007 step 3).

The speaker plays a dispatched list on its own and moves to the next item at
the end of each track, so the server no longer has to switch tracks on a
timer. It cannot append to its list (every dispatch replaces it), so the
server sends a *window* of up to ``size`` tracks. A window that needs a
follow-up ends with a silent *sentinel*: when the speaker fetches the
sentinel, the window has been played out and the server dispatches the next
one, replacing only silence.

This module is pure: it maps (play mode, playlist length, start index) to a
window. Building URLs and sending them is the device player's job.
"""

from __future__ import annotations

from dataclasses import dataclass

from xiaomusic.const import (
    PLAY_TYPE_ONE,
    PLAY_TYPE_SEQ,
    PLAY_TYPE_SIN,
)

# Speaker loop types (player_set_loop). Verified on the OH2P 2026-11-22/23:
# the value persists across queue replacements.
LOOP_SINGLE = 0  # repeat the current item
LOOP_LIST = 1  # repeat the whole list
LOOP_ONCE = 2  # play the list once, then stop

DEFAULT_WINDOW_SIZE = 20


@dataclass(frozen=True)
class Window:
    """One dispatch: playlist indices in play order, the loop type, and where
    to continue when the sentinel is reached (None = no sentinel)."""

    indices: tuple[int, ...]
    loop_type: int
    sentinel_next: int | None = None


def loop_type_for(play_type: int) -> int:
    if play_type == PLAY_TYPE_ONE:
        return LOOP_SINGLE
    if play_type in (PLAY_TYPE_SEQ, PLAY_TYPE_SIN):
        return LOOP_ONCE
    # PLAY_TYPE_ALL / PLAY_TYPE_RND: random order is decided by the server
    # (the list is already shuffled), so the speaker just loops the list.
    return LOOP_LIST


def plan_window(
    play_type: int, length: int, start: int, size: int = DEFAULT_WINDOW_SIZE
) -> Window:
    """Window starting at ``start`` for a playlist of ``length`` items."""
    if length <= 0:
        raise ValueError("empty playlist")
    if not 0 <= start < length:
        raise ValueError(f"start {start} out of range for {length} items")
    size = max(int(size), 1)
    loop_type = loop_type_for(play_type)

    if play_type in (PLAY_TYPE_ONE, PLAY_TYPE_SIN):
        return Window((start,), loop_type)

    if play_type == PLAY_TYPE_SEQ:
        end = min(start + size, length)
        sentinel_next = end if end < length else None
        return Window(tuple(range(start, end)), loop_type, sentinel_next)

    # PLAY_TYPE_ALL / PLAY_TYPE_RND (and unknown modes): wrap around.
    if length <= size:
        # The whole list fits: the speaker loops it by itself, no sentinel.
        order = tuple((start + k) % length for k in range(length))
        return Window(order, loop_type)
    order = tuple((start + k) % length for k in range(size))
    return Window(order, loop_type, (start + size) % length)


__all__ = [
    "DEFAULT_WINDOW_SIZE",
    "LOOP_LIST",
    "LOOP_ONCE",
    "LOOP_SINGLE",
    "Window",
    "loop_type_for",
    "plan_window",
]
