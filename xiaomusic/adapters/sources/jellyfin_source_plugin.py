from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlparse

from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest, ResolvedMedia
from xiaomusic.core.source.source_plugin import SourcePlugin
from xiaomusic.relay.url_classifier import UrlClassifier


class JellyfinSourcePlugin(SourcePlugin):
    """Official Jellyfin source plugin for unified core playback chain."""

    name = "jellyfin"

    @staticmethod
    def _resolve_duration_seconds(*values: Any) -> float | None:
        for value in values:
            if value is None:
                continue
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if parsed <= 0:
                continue
            if parsed > 10000:
                parsed = parsed / 1000.0
            if parsed > 0:
                return parsed
        return None

    def __init__(
        self,
        payload_url_resolver: Callable[[dict[str, Any]], str],
        classifier: UrlClassifier | None = None,
    ) -> None:
        self._payload_url_resolver = payload_url_resolver
        self._classifier = classifier or UrlClassifier()

    def can_resolve(self, request: MediaRequest) -> bool:
        if request.source_hint == self.name:
            return True
        payload = request.context.get("source_payload")
        if isinstance(payload, dict) and str(payload.get("source") or "").lower() == self.name:
            return True
        query = str(request.query or "").strip()
        if not query:
            return False
        parsed = urlparse(query)
        if parsed.scheme not in {"http", "https"}:
            return False
        return self._classifier.classify(query).site == self.name

    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        payload = request.context.get("source_payload")
        if isinstance(payload, dict):
            stream_url = self._payload_url_resolver(payload)
            parsed = urlparse(stream_url)
            if parsed.scheme not in {"http", "https"}:
                raise SourceResolveError("jellyfin payload did not resolve to HTTP URL")
            title = request.context.get("title") or payload.get("title") or payload.get("name")
            media_id = str(payload.get("id") or request.request_id)
            duration_seconds = self._resolve_duration_seconds(
                payload.get("duration_seconds"),
                payload.get("duration"),
                request.context.get("duration_seconds"),
                request.context.get("duration_ms"),
            )
            return ResolvedMedia(
                media_id=media_id,
                source=self.name,
                title=str(title or "jellyfin-media"),
                stream_url=stream_url,
                headers={},
                expires_at=None,
                is_live=False,
                duration_seconds=duration_seconds,
            )

        query = str(request.query or "").strip()
        parsed = urlparse(query)
        if parsed.scheme not in {"http", "https"} or self._classifier.classify(query).site != self.name:
            raise SourceResolveError("jellyfin source payload is required")

        context = request.context if isinstance(request.context, dict) else {}
        title = context.get("title") or query.rsplit("/", 1)[-1] or "jellyfin-media"
        media_id = request.request_id
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) >= 2 and path_parts[0].lower() == "audio":
            media_id = path_parts[1]
        duration_seconds = self._resolve_duration_seconds(
            context.get("duration_seconds"),
            context.get("duration_ms"),
        )
        return ResolvedMedia(
            media_id=str(media_id),
            source=self.name,
            title=str(title),
            stream_url=query,
            headers={},
            expires_at=None,
            is_live=False,
            duration_seconds=duration_seconds,
        )
