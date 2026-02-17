"""URL classification and normalization for M1."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from xiaomusic.m1.contracts import UrlInfo


class UrlClassifier:
    """Classify target site and provide stable normalized URL."""

    _youtube_live_hint_ids = {"vNG3-GRjrAo"}

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

        normalized_unknown = self._normalize_unknown(raw_url)
        return UrlInfo(
            site="unknown",
            kind_hint="unknown",
            normalized_url=normalized_unknown,
            original_url=raw_url,
        )

    def _youtube_kind_hint(self, video_id: str) -> str:
        if video_id in self._youtube_live_hint_ids:
            return "live"
        return "vod"

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
