from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlparse

from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest, ResolvedMedia
from xiaomusic.core.source.source_plugin import SourcePlugin


class JellyfinSourcePlugin(SourcePlugin):
    """Official Jellyfin source plugin for unified core playback chain."""

    name = "jellyfin"

    def __init__(self, payload_url_resolver: Callable[[dict[str, Any]], str]) -> None:
        self._payload_url_resolver = payload_url_resolver

    def can_resolve(self, request: MediaRequest) -> bool:
        if request.source_hint == self.name:
            return True
        payload = request.context.get("source_payload")
        if not isinstance(payload, dict):
            return False
        return str(payload.get("source") or "").lower() == self.name

    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        payload = request.context.get("source_payload")
        if not isinstance(payload, dict):
            raise SourceResolveError("jellyfin source payload is required")

        stream_url = self._payload_url_resolver(payload)
        parsed = urlparse(stream_url)
        if parsed.scheme not in {"http", "https"}:
            raise SourceResolveError("jellyfin payload did not resolve to HTTP URL")

        title = request.context.get("title") or payload.get("title") or payload.get("name")
        media_id = str(payload.get("id") or request.request_id)
        return ResolvedMedia(
            media_id=media_id,
            source=self.name,
            title=str(title or "jellyfin-media"),
            stream_url=stream_url,
            headers={},
            expires_at=None,
            is_live=False,
        )
