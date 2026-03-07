from __future__ import annotations

import asyncio
from typing import Any

from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest, ResolvedMedia
from xiaomusic.core.source.source_plugin import SourcePlugin
from xiaomusic.network_audio.resolver import Resolver
from xiaomusic.network_audio.url_classifier import UrlClassifier


class SiteMediaSourcePlugin(SourcePlugin):
    """Source plugin for site-page media URLs (YouTube/Bilibili and similar)."""

    name = "site_media"

    def __init__(
        self,
        classifier: UrlClassifier | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._classifier = classifier or UrlClassifier()
        self._resolver = resolver or Resolver()

    def can_resolve(self, request: MediaRequest) -> bool:
        if request.source_hint == self.name:
            return True
        site = self._classifier.classify(request.query).site
        return site in {"youtube", "bilibili"}

    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        info = self._classifier.classify(request.query)
        if info.site not in {"youtube", "bilibili"}:
            raise SourceResolveError("site_media plugin only supports recognized site-page URLs")

        timeout_seconds = float(request.context.get("resolve_timeout_seconds", 8))
        resolved = await asyncio.to_thread(
            self._resolver.resolve,
            info.normalized_url,
            timeout_seconds,
        )
        if not resolved.ok or not resolved.source_url:
            detail = resolved.error_message or resolved.error_code or "site media resolve failed"
            raise SourceResolveError(detail)

        media_id = str(resolved.meta.get("raw_id") or request.request_id)
        title = str(resolved.title or request.query)
        headers = request.context.get("headers") if isinstance(request.context.get("headers"), dict) else {}
        return ResolvedMedia(
            media_id=media_id,
            source=self.name,
            title=title,
            stream_url=str(resolved.source_url),
            headers={str(k): str(v) for k, v in headers.items()},
            expires_at=None,
            is_live=bool(resolved.is_live),
        )
