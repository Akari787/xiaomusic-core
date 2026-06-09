"""Channel cycle and context filtering primitives for multi-platform control flows."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any


CONTROL_MESSAGE_TYPES = {
    "PLAY",
    "PAUSE",
    "NEXT",
    "PREV",
    "PLAYLIST_SELECT",
    "SEARCH",
    "CLOSE_SESSION",
}

NOISE_MESSAGE_TYPES = {"volume_changed", "progress_update"}


@dataclass
class ChannelSettings:
    cycle_interval: int = 60
    archive_log_enabled: bool = False
    archive_log_max_entries: int = 500
    context_window_size: int = 20

    @classmethod
    def from_config(cls, config: Any, log: Any | None = None) -> "ChannelSettings":
        interval = int(getattr(config, "channel_cycle_interval", 60) or 60)
        if interval < 15:
            if log is not None:
                log.warning("channel_cycle_interval too low (%s), clamped to 15", interval)
            interval = 15

        archive_max = int(getattr(config, "channel_archive_log_max_entries", 500) or 500)
        archive_max = max(100, archive_max)
        window_size = int(getattr(config, "channel_context_window_size", 20) or 20)
        window_size = max(5, window_size)
        return cls(
            cycle_interval=interval,
            archive_log_enabled=bool(getattr(config, "channel_archive_log_enabled", False)),
            archive_log_max_entries=archive_max,
            context_window_size=window_size,
        )


def infer_message_type(message: Any) -> str:
    if isinstance(message, dict):
        raw = message.get("type") or message.get("message_type") or message.get("action") or message.get("intent") or ""
    else:
        raw = getattr(message, "type", "") or getattr(message, "message_type", "") or getattr(message, "action", "") or ""
    return str(raw or "").strip()


@dataclass
class ChannelContextFilter:
    settings: ChannelSettings
    debug: bool = False
    context_window: deque = field(init=False)
    archive_log: deque = field(init=False)

    def __post_init__(self):
        self.context_window = deque(maxlen=self.settings.context_window_size)
        self.archive_log = deque(maxlen=self.settings.archive_log_max_entries)

    def accept(self, message: Any) -> bool:
        message_type = infer_message_type(message)
        normalized = message_type.upper()
        if normalized in CONTROL_MESSAGE_TYPES:
            self.context_window.append(message)
            return True

        if message_type in NOISE_MESSAGE_TYPES or message_type.lower() in NOISE_MESSAGE_TYPES:
            if self.debug or self.settings.archive_log_enabled:
                self.archive_log.append(message)
            return False

        if self.debug or self.settings.archive_log_enabled:
            self.archive_log.append(message)
        return False

    def close(self):
        self.context_window.clear()
        self.archive_log.clear()


class ChannelCycleController:
    """Interruptible cycle timer; high-priority commands wake the next poll."""

    def __init__(self, interval: int = 60):
        self.interval = max(15, int(interval or 60))
        self._interrupt = asyncio.Event()

    def interrupt(self):
        self._interrupt.set()

    async def wait_next_cycle(self) -> bool:
        """Wait until timeout or interrupt. Returns True when interrupted."""
        try:
            await asyncio.wait_for(self._interrupt.wait(), timeout=self.interval)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._interrupt.clear()
