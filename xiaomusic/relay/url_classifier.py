"""URL classification and normalization for relay sources."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from xiaomusic.relay.contracts import UrlInfo


class UrlClassifier:
    """Classify target site and provide stable normalized URL."""

    _youtube_live_hint_ids = {"vNG3-GRjrAo"}
    _jellyfin_audio_stream_pattern = re.compile(r"^/Audio/[^/]+/stream(?:\.[^/?#]+)?$", re.IGNORECASE)

    def __init__(self, jellyfin_base_url: str = "") -> None:
        self._jellyfin_base_url = str(jellyfin_base_url or "").strip()

    def classify(self, raw_url: str) -> UrlInfo:
        parsed = urlparse(raw_url)
        host = (parsed.netloc or "").lower()

        if host.startswith("m."):
            host = host[2:]

        if host in {"youtu.be", "www.youtu.be"}:
            video_id = parsed.path.strip("/")
            normalized = f"https://www.youtube.com/watch?v={video_id}"
            return UrlInfo(
                site="youtube",
                kind_hint=self._youtube_kind_hint(video_id),
                normalized_url=normalized,
                original_url=raw_url,
            )

        if host in {"youtube.com", "www.youtube.com"}:
            query = parse_qs(parsed.query)
            if parsed.path == "/watch" and query.get("v"):
                video_id = query["v"][0]
                normalized = f"https://www.youtube.com/watch?v={video_id}"
                return UrlInfo(
                    site="youtube",
                    kind_hint=self._youtube_kind_hint(video_id),
                    normalized_url=normalized,
                    original_url=raw_url,
                )

        if host == "live.bilibili.com":
            room_id = parsed.path.strip("/")
            normalized = f"https://live.bilibili.com/{room_id}"
            return UrlInfo(
                site="bilibili",
                kind_hint="live",
                normalized_url=normalized,
                original_url=raw_url,
            )

        if host in {"www.bilibili.com", "bilibili.com"} and parsed.path.startswith(
            "/video/"
        ):
            path = parsed.path.rstrip("/")
            normalized = f"https://www.bilibili.com{path}"
            return UrlInfo(
                site="bilibili",
                kind_hint="vod",
                normalized_url=normalized,
                original_url=raw_url,
            )

        if self.is_jellyfin_url(raw_url):
            normalized = self._normalize_unknown(raw_url)
            return UrlInfo(
                site="jellyfin",
                kind_hint="audio_stream",
                normalized_url=normalized,
                original_url=raw_url,
            )

        normalized_unknown = self._normalize_unknown(raw_url)
        return UrlInfo(
            site="unknown",
            kind_hint="unknown",
            normalized_url=normalized_unknown,
            original_url=raw_url,
        )

    def is_jellyfin_url(self, raw_url: str) -> bool:
        if not self._jellyfin_base_url or not raw_url:
            return False
        try:
            base = urlparse(self._ensure_scheme(self._jellyfin_base_url))
            cand = urlparse(raw_url)
            if cand.scheme not in {"http", "https"}:
                return False
            if not base.hostname or not cand.hostname:
                return False
            if base.hostname.strip().lower().rstrip(".") != cand.hostname.strip().lower().rstrip("."):
                return False
            if base.port is not None and base.port != cand.port:
                return False
            base_path = (base.path or "").rstrip("/")
            cand_path = cand.path or ""
            if base_path:
                if cand_path != base_path and not cand_path.startswith(base_path + "/"):
                    return False
                relative_path = cand_path[len(base_path) :] or "/"
            else:
                relative_path = cand_path
            return bool(self._jellyfin_audio_stream_pattern.match(relative_path or "/"))
        except Exception:
            return False

    def _youtube_kind_hint(self, video_id: str) -> str:
        if video_id in self._youtube_live_hint_ids:
            return "live"
        return "vod"

    @staticmethod
    def _ensure_scheme(raw_url: str) -> str:
        if "://" in raw_url:
            return raw_url
        return "http://" + raw_url

    def _normalize_unknown(self, raw_url: str) -> str:
        parsed = urlparse(raw_url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query_string = urlencode(query, doseq=True)
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                query_string,
                "",
            )
        )
