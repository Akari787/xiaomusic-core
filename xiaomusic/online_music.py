"""Compatibility wrapper for online music service modules."""

from xiaomusic.providers.online_music_keywords import build_keyword as _build_keyword
from xiaomusic.providers.online_music_keywords import (
    parse_keyword_by_dash as _parse_keyword_by_dash,
)
from xiaomusic.services.online_music_service import OnlineMusicService

__all__ = ["OnlineMusicService", "_build_keyword", "_parse_keyword_by_dash"]
