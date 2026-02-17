"""Unified URL strategy shared by playback workflows."""

from __future__ import annotations

from xiaomusic.network_audio.url_classifier import UrlClassifier


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
