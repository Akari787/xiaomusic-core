from __future__ import annotations

import asyncio
from typing import Any

from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest, ResolvedMedia
from xiaomusic.core.source.source_plugin import SourcePlugin
from xiaomusic.network_audio.runtime import NetworkAudioRuntime
from xiaomusic.network_audio.resolver import Resolver
from xiaomusic.network_audio.url_classifier import UrlClassifier


class SiteMediaSourcePlugin(SourcePlugin):
    """Source plugin for site-page media URLs (YouTube/Bilibili and similar)."""

    name = "site_media"

    def __init__(
        self,
        classifier: UrlClassifier | None = None,
        resolver: Resolver | None = None,
        runtime_provider: Any | None = None,
    ) -> None:
        self._classifier = classifier or UrlClassifier()
        self._resolver = resolver or Resolver()
        self._runtime_provider = runtime_provider

    def can_resolve(self, request: MediaRequest) -> bool:
        if request.source_hint == self.name:
            return True
        site = self._classifier.classify(request.query).site
        return site in {"youtube", "bilibili"}

    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        info = self._classifier.classify(request.query)
        if info.site not in {"youtube", "bilibili"}:
            raise SourceResolveError("site_media plugin only supports recognized site-page URLs")

        runtime = self._runtime_provider() if callable(self._runtime_provider) else None
        if request.device_id and isinstance(runtime, NetworkAudioRuntime):
            prepared = runtime.prepare_link(
                info.normalized_url,
                prefer_proxy=bool(request.context.get("prefer_proxy", False)),
                no_cache=bool(request.context.get("no_cache", False)),
            )
            if not prepared.get("ok"):
                detail = prepared.get("error_message") or prepared.get("error_code") or "site media prepare failed"
                raise SourceResolveError(str(detail))
            session = prepared.get("session") if isinstance(prepared.get("session"), dict) else {}
            stream_url = str(prepared.get("stream_url") or (session or {}).get("stream_url") or "").strip()
            if not stream_url:
                raise SourceResolveError("site media prepare returned empty stream url")
            media_id = str((session or {}).get("sid") or request.request_id)
            title = str((session or {}).get("title") or request.query)
            raw_headers = request.context.get("headers")
            headers: dict[str, Any] = raw_headers if isinstance(raw_headers, dict) else {}
            normalized_headers = {str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {}
            return ResolvedMedia(
                media_id=media_id,
                source=self.name,
                title=title,
                stream_url=stream_url,
                headers=normalized_headers,
                expires_at=None,
                is_live=bool((session or {}).get("is_live", False)),
            )

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
        raw_headers = request.context.get("headers")
        headers: dict[str, Any] = raw_headers if isinstance(raw_headers, dict) else {}
        normalized_headers = {str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {}
        return ResolvedMedia(
            media_id=media_id,
            source=self.name,
            title=title,
            stream_url=str(resolved.source_url),
            headers=normalized_headers,
            expires_at=None,
            is_live=bool(resolved.is_live),
        )
