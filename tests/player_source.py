"""Source of XiaoMusicDevice for AST guard tests.

The device player is split into ``xiaomusic/device_player.py`` and mixin
modules under ``xiaomusic/player/`` (ADR-0007 step 6). Guards that scan the
player's source must cover all of them, so they read this combined text.
"""

from __future__ import annotations

import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAYER_FILES = [ROOT / "xiaomusic" / "device_player.py"] + sorted(
    (ROOT / "xiaomusic" / "player").glob("*.py")
)


def player_source() -> str:
    return "\n\n".join(path.read_text(encoding="utf-8") for path in PLAYER_FILES)


def player_source_file() -> io.StringIO:
    """Drop-in for ``open("xiaomusic/device_player.py")``."""
    return io.StringIO(player_source())


def player_relpaths() -> list[str]:
    return [path.relative_to(ROOT).as_posix() for path in PLAYER_FILES]
