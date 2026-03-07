from __future__ import annotations

import time
from urllib.parse import urlparse

from xiaomusic.core.errors.stream_errors import ExpiredStreamError
from xiaomusic.core.models.media import PreparedStream, ResolvedMedia


class DeliveryAdapter:
    """Convert ResolvedMedia to PreparedStream with basic safety checks."""

    def __init__(self, expiry_skew_seconds: int = 5) -> None:
        self._expiry_skew_seconds = expiry_skew_seconds

    def prepare(self, media: ResolvedMedia) -> PreparedStream:
        parsed = urlparse(media.stream_url)
        if parsed.scheme not in {"http", "https"}:
            raise ExpiredStreamError("stream url is not dispatchable")

        if media.expires_at is not None:
            now_ts = int(time.time())
            if media.expires_at <= now_ts + self._expiry_skew_seconds:
                raise ExpiredStreamError("stream url expired or near expiry")

        return PreparedStream(
            final_url=media.stream_url,
            headers=dict(media.headers),
            expires_at=media.expires_at,
            is_proxy=False,
            source=media.source,
        )
