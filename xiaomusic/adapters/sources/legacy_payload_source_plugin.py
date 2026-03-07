from __future__ import annotations

from typing import Any

from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest


class LegacyPayloadSourcePlugin:
    """Compatibility adapter for legacy payload-based media requests.

    Current scope:
    - Compatibility-only request adapter for legacy payload callers.
    - Converts legacy payload into standard MediaRequest for official plugins.

    compatibility_layer:
    - reason: preserve legacy payload callers while dedicated plugins are rolled out.
    - planned_removal_phase: post-release cleanup after legacy API callers are migrated.
    """

    KNOWN_HINTS = {"jellyfin", "direct_url", "site_media", "local_library"}

    def adapt_request(
        self,
        request_id: str,
        speaker_id: str,
        payload: dict[str, Any],
        fallback_hint: str = "direct_url",
    ) -> MediaRequest:
        if not isinstance(payload, dict):
            raise SourceResolveError("legacy payload must be a dict")

        source_raw = str(payload.get("source") or "").strip().lower()
        source_hint = source_raw if source_raw in self.KNOWN_HINTS else fallback_hint

        local_query = str(
            payload.get("music_name")
            or payload.get("track_id")
            or payload.get("name")
            or payload.get("title")
            or payload.get("path")
            or ""
        )
        query = str(payload.get("url") or local_query or "")

        if not query:
            raise SourceResolveError("legacy payload has no playable query")

        if source_hint == "direct_url" and not str(payload.get("url") or "").startswith(("http://", "https://")):
            source_hint = "local_library"

        return MediaRequest(
            request_id=request_id,
            source_hint=source_hint,
            query=query,
            device_id=speaker_id,
            context={
                "source_payload": payload,
                "title": payload.get("name") or payload.get("title"),
            },
        )
