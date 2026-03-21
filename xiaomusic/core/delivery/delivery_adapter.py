from __future__ import annotations

import logging
import time
from typing import Callable
from urllib.parse import urlparse

from xiaomusic.core.errors.stream_errors import ExpiredStreamError, UndeliverableStreamError
from xiaomusic.core.models.media import DeliveryPlan, PreparedStream, ResolvedMedia


LOG = logging.getLogger("xiaomusic.core.delivery_adapter")


class DeliveryAdapter:
    """Convert ResolvedMedia to PreparedStream with basic safety checks."""

    def __init__(
        self,
        expiry_skew_seconds: int = 5,
        proxy_url_builder: Callable[[str, str], str] | None = None,
    ) -> None:
        self._expiry_skew_seconds = expiry_skew_seconds
        self._proxy_url_builder = proxy_url_builder

    def prepare(self, media: ResolvedMedia) -> PreparedStream:
        return self.prepare_plan(media).primary

    def prepare_plan(self, media: ResolvedMedia, context: dict | None = None) -> DeliveryPlan:
        parsed = urlparse(media.stream_url)
        if parsed.scheme not in {"http", "https"}:
            LOG.warning(
                "url_prepare_result=failed proxy_decision=reject_non_http source=%s url=%s",
                media.source,
                media.stream_url,
            )
            raise UndeliverableStreamError("stream url is not dispatchable")

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

        request_context = context if isinstance(context, dict) else {}
        prefer_proxy = bool(request_context.get("prefer_proxy", False))
        proxy_url = self._build_proxy_url(media)
        has_proxy = bool(proxy_url)
        can_fallback = media.source in {"site_media", "direct_url", "jellyfin"}
        is_prepared_stream = parsed.path.startswith("/relay/stream/")

        direct = PreparedStream(
            final_url=media.stream_url,
            headers=dict(media.headers),
            expires_at=media.expires_at,
            is_proxy=False,
            source=media.source,
        )
        proxy = (
            PreparedStream(
                final_url=str(proxy_url),
                headers=dict(media.headers),
                expires_at=media.expires_at,
                is_proxy=True,
                source=media.source,
            )
            if has_proxy
            else None
        )

        source_prefers_proxy = media.source == "site_media" and not is_prepared_stream

        if is_prepared_stream:
            plan = DeliveryPlan(
                primary=direct,
                fallback=None,
                strategy="direct_only",
                decision_reason="pre_streamed_source",
            )
        elif (prefer_proxy or source_prefers_proxy) and proxy is not None:
            plan = DeliveryPlan(
                primary=proxy,
                fallback=direct,
                strategy="proxy_first",
                decision_reason="prefer_proxy=true" if prefer_proxy else f"source={media.source}",
            )
        elif can_fallback and proxy is not None:
            plan = DeliveryPlan(
                primary=direct,
                fallback=proxy,
                strategy="direct_then_proxy",
                decision_reason=f"source={media.source}",
            )
        else:
            plan = DeliveryPlan(
                primary=direct,
                fallback=None,
                strategy="direct_only",
                decision_reason="proxy_unavailable_or_not_needed",
            )

        LOG.info(
            "url_prepare_result=ok strategy=%s source=%s primary_url=%s fallback_url=%s",
            plan.strategy,
            media.source,
            plan.primary.final_url,
            plan.fallback.final_url if plan.fallback else "",
        )
        return plan

    def _build_proxy_url(self, media: ResolvedMedia) -> str | None:
        if self._proxy_url_builder is None:
            return None
        try:
            out = str(self._proxy_url_builder(media.stream_url, media.title)).strip()
            if not out:
                return None
            parsed = urlparse(out)
            if parsed.scheme not in {"http", "https"}:
                return None
            return out
        except Exception as exc:
            LOG.warning("proxy_url_build_failed source=%s error=%s", media.source, exc.__class__.__name__)
            return None
