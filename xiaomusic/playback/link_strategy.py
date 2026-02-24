"""Unified URL strategy shared by playback workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from xiaomusic.network_audio.url_classifier import UrlClassifier


@dataclass
class NormalizedLink:
    source_type: str
    direct_url: str
    proxy_url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    ttl_sec: Optional[int] = None


class LinkPlaybackStrategy:
    def __init__(self, music_library, log) -> None:
        self.music_library = music_library
        self.log = log
        self.classifier = UrlClassifier()

    def classify(self, raw_url: str):
        return self.classifier.classify(raw_url)

    def should_use_network_audio(self, raw_url: str) -> bool:
        info = self.classify(raw_url)
        return info.site in {"youtube", "bilibili"}

    def normalize_input_url(self, raw_url: str) -> str:
        info = self.classify(raw_url)
        return info.normalized_url

    def build_proxy_url(self, raw_url: str, name: str = "") -> str:
        return self.music_library.get_proxy_url(raw_url, name=name)

    def _host_allowed_for_proxy(self, raw_url: str) -> bool:
        host = (urlparse(raw_url).hostname or "").lower()
        if not host:
            return False
        if host in {"localhost", "127.0.0.1"}:
            return True
        if (
            host.startswith("192.168.")
            or host.startswith("10.")
            or host.startswith("172.")
        ):
            return True

        cfg = getattr(self.music_library, "config", None)
        allowlist = []
        if cfg is not None:
            allowlist = list(getattr(cfg, "outbound_allowlist_domains", []) or [])
        if not allowlist:
            return False
        return any(host == d or host.endswith("." + d) for d in allowlist)

    def normalize(self, raw_url: str, *, name: str = "") -> NormalizedLink:
        direct = self.normalize_input_url(raw_url)
        if self.music_library.is_jellyfin_url(direct):
            source_type = "jellyfin"
        else:
            source_type = self.classify(direct).site
        proxy_url = None
        if self._host_allowed_for_proxy(direct):
            proxy_url = self.build_proxy_url(direct, name=name)
        return NormalizedLink(
            source_type=source_type,
            direct_url=direct,
            proxy_url=proxy_url,
        )

    def should_fallback(
        self,
        *,
        startup_ok: bool,
        fail_count: int,
        reason: str = "",
    ) -> bool:
        if not startup_ok:
            return True
        if fail_count >= 2:
            return True
        if reason and reason in {
            "not_playing",
            "player_play_failed",
            "connection_reset",
        }:
            return True
        return False

    def select_url(
        self,
        normalized: NormalizedLink,
        *,
        prefer: str = "direct",
        startup_ok: bool = True,
        fail_count: int = 0,
        failure_reason: str = "",
    ) -> str:
        if prefer == "proxy" and normalized.proxy_url:
            return normalized.proxy_url
        if not self.should_fallback(
            startup_ok=startup_ok,
            fail_count=fail_count,
            reason=failure_reason,
        ):
            return normalized.direct_url
        if normalized.proxy_url:
            return normalized.proxy_url
        return normalized.direct_url

    def should_jellyfin_auto_fallback(
        self,
        jellyfin_mode: str,
        origin_url: str,
        current_url: str,
    ) -> bool:
        mode = (jellyfin_mode or "auto").lower()
        return bool(
            mode == "auto"
            and origin_url
            and origin_url == current_url
            and self.music_library.is_jellyfin_url(current_url)
        )
