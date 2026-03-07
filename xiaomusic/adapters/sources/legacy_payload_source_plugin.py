from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlparse

from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest, ResolvedMedia
from xiaomusic.core.source.source_plugin import SourcePlugin


class LegacyPayloadSourcePlugin(SourcePlugin):
    """Compatibility source adapter for legacy payload-based media sources.

    Phase 2 scope:
    - Keep Jellyfin/OpenAPI payload translation outside API handlers.
    - Produce standard ResolvedMedia for unified core chain.

    Future migration point:
    - Replace this plugin by dedicated source plugins (for example JellyfinSourcePlugin).
    """

    name = "legacy_payload"

    def __init__(self, payload_url_resolver: Callable[[dict[str, Any]], str]) -> None:
        self._payload_url_resolver = payload_url_resolver

    def can_resolve(self, request: MediaRequest) -> bool:
        return isinstance(request.context.get("source_payload"), dict)

    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        payload = request.context.get("source_payload")
        if not isinstance(payload, dict):
            raise SourceResolveError("legacy source payload is required")

        stream_url = self._payload_url_resolver(payload)
        parsed = urlparse(stream_url)
        if parsed.scheme not in {"http", "https"}:
            raise SourceResolveError("legacy payload did not resolve to HTTP URL")

        title = request.context.get("title") or payload.get("name") or payload.get("title")
        source = payload.get("source") or self._infer_source(stream_url)
        return ResolvedMedia(
            media_id=request.request_id,
            source=str(source),
            title=str(title or "legacy-media"),
            stream_url=stream_url,
            headers={},
            expires_at=None,
            is_live=False,
        )

    @staticmethod
    def _infer_source(stream_url: str) -> str:
        if "jellyfin" in stream_url.lower():
            return "jellyfin_compat"
        return "legacy_payload"
