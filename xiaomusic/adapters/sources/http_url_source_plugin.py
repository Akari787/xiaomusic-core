from __future__ import annotations

from urllib.parse import urlparse

from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest, ResolvedMedia
from xiaomusic.core.source.source_plugin import SourcePlugin


class HttpUrlSourcePlugin(SourcePlugin):
    name = "http_url"

    def can_resolve(self, request: MediaRequest) -> bool:
        parsed = urlparse(request.query)
        return parsed.scheme in {"http", "https"}

    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        if not self.can_resolve(request):
            raise SourceResolveError("query is not a valid HTTP URL")

        context = request.context if isinstance(request.context, dict) else {}
        title = context.get("title")
        headers = context.get("headers") if isinstance(context.get("headers"), dict) else {}
        expires_at_raw = context.get("expires_at")
        expires_at = int(expires_at_raw) if isinstance(expires_at_raw, (int, float, str)) and str(expires_at_raw).isdigit() else None
        is_live = bool(context.get("is_live", False))
        resolved_title = str(title or request.query.rsplit("/", 1)[-1] or "http-stream")
        return ResolvedMedia(
            media_id=request.request_id,
            source=self.name,
            title=resolved_title,
            stream_url=request.query,
            headers={str(k): str(v) for k, v in headers.items()},
            expires_at=expires_at,
            is_live=is_live,
        )
