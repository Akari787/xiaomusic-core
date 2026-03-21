from __future__ import annotations

from urllib.parse import urlparse

from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest, ResolvedMedia
from xiaomusic.core.source.source_plugin import SourcePlugin
from xiaomusic.relay.url_classifier import UrlClassifier


class DirectUrlSourcePlugin(SourcePlugin):
    """Source plugin for user-provided direct playable media URLs."""

    name = "direct_url"

    def __init__(self, classifier: UrlClassifier | None = None) -> None:
        self._classifier = classifier or UrlClassifier()

    def can_resolve(self, request: MediaRequest) -> bool:
        parsed = urlparse(request.query)
        if parsed.scheme not in {"http", "https"}:
            return False
        site = self._classifier.classify(request.query).site
        return site == "unknown"

    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        if not self.can_resolve(request):
            raise SourceResolveError("query is not a direct playable media URL")

        context = request.context if isinstance(request.context, dict) else {}
        title = context.get("title")
        headers = context.get("headers") if isinstance(context.get("headers"), dict) else {}
        expires_at_raw = context.get("expires_at")
        expires_at = (
            int(expires_at_raw)
            if isinstance(expires_at_raw, (int, float, str)) and str(expires_at_raw).isdigit()
            else None
        )
        is_live = bool(context.get("is_live", False))
        resolved_title = str(title or request.query.rsplit("/", 1)[-1] or "direct-stream")
        return ResolvedMedia(
            media_id=request.request_id,
            source=self.name,
            title=resolved_title,
            stream_url=request.query,
            headers={str(k): str(v) for k, v in headers.items()},
            expires_at=expires_at,
            is_live=is_live,
        )
