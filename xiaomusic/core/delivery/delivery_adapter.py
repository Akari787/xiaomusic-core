from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from xiaomusic.core.errors.stream_errors import ExpiredStreamError
from xiaomusic.core.models.media import PreparedStream, ResolvedMedia


LOG = logging.getLogger("xiaomusic.core.delivery_adapter")


class DeliveryAdapter:
    """Convert ResolvedMedia to PreparedStream with basic safety checks."""

    def __init__(self, expiry_skew_seconds: int = 5) -> None:
        self._expiry_skew_seconds = expiry_skew_seconds

    def prepare(self, media: ResolvedMedia) -> PreparedStream:
        parsed = urlparse(media.stream_url)
        if parsed.scheme not in {"http", "https"}:
            LOG.warning(
                "url_prepare_result=failed proxy_decision=reject_non_http source=%s url=%s",
                media.source,
                media.stream_url,
            )
            raise ExpiredStreamError("stream url is not dispatchable")

        if media.expires_at is not None:
            now_ts = int(time.time())
            if media.expires_at <= now_ts + self._expiry_skew_seconds:
                LOG.warning(
                    "url_prepare_result=failed proxy_decision=expired source=%s url=%s expires_at=%s now_ts=%s",
                    media.source,
                    media.stream_url,
                    media.expires_at,
                    now_ts,
                )
                raise ExpiredStreamError("stream url expired or near expiry")

        LOG.info(
            "url_prepare_result=ok proxy_decision=direct source=%s final_url=%s",
            media.source,
            media.stream_url,
        )
        return PreparedStream(
            final_url=media.stream_url,
            headers=dict(media.headers),
            expires_at=media.expires_at,
            is_proxy=False,
            source=media.source,
        )
