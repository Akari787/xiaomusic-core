from __future__ import annotations

from urllib.parse import urlparse

from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest, ResolvedMedia
from xiaomusic.core.source.online_plugin_reference_store import (
    OnlinePluginReferenceStore,
)
from xiaomusic.core.source.source_plugin import SourcePlugin


class OnlinePluginSourcePlugin(SourcePlugin):
    """Resolve process-local opaque references through OnlineMusicService."""

    name = "online_plugin"

    def __init__(self, store: OnlinePluginReferenceStore, online_music_service) -> None:
        self._store = store
        self._online_music_service = online_music_service

    def can_resolve(self, request: MediaRequest) -> bool:
        return request.source_hint == self.name and str(request.query).startswith("opq_")

    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        if not self.can_resolve(request):
            raise SourceResolveError("online plugin query is invalid")
        try:
            item = self._store.get(request.query)
            if not str(item.get("platform") or "").strip():
                raise SourceResolveError("online plugin item is unsupported")
            result = await self._online_music_service.get_media_source_url(item)
            stream_url = str((result or {}).get("url") or "").strip()
            if urlparse(stream_url).scheme not in {"http", "https"}:
                raise SourceResolveError("online plugin media resolution failed")
            media_id = str(
                item.get("media_id")
                or item.get("id")
                or item.get("songmid")
                or item.get("songid")
                or request.request_id
            ).strip()
            title = str(item.get("title") or item.get("name") or "online media").strip()
            return ResolvedMedia(
                media_id=media_id,
                source=self.name,
                title=title,
                stream_url=stream_url,
                headers={
                    str(key): str(value)
                    for key, value in (result or {}).get("headers", {}).items()
                }
                if isinstance((result or {}).get("headers"), dict)
                else {},
                duration_seconds=(result or {}).get("duration_seconds"),
            )
        except SourceResolveError:
            raise
        except Exception:
            raise SourceResolveError("online plugin media resolution failed") from None
