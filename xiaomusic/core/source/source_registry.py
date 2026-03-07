from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest
from xiaomusic.core.source.source_plugin import SourcePlugin


class SourceRegistry:
    """Registry for source plugin registration and lookup only."""

    LEGACY_HINT_MAP = {
        # compatibility_layer: keep legacy source hints until all external callers migrate.
        # removal_condition: remove after API v2 and configuration migration completion.
        "http_url": "direct_url",
        "network_audio": "site_media",
        "local_music": "local_library",
    }

    def __init__(self) -> None:
        self._plugins: dict[str, SourcePlugin] = {}

    def register(self, plugin: SourcePlugin) -> None:
        self._plugins[plugin.name] = plugin

    def get_plugin(self, source_hint: str | None, request: MediaRequest) -> SourcePlugin:
        if source_hint:
            normalized_hint = self._normalize_hint(source_hint)
            plugin = self._plugins.get(normalized_hint)
            if plugin is None:
                raise SourceResolveError(f"source plugin not registered: {normalized_hint}")
            return plugin

        auto_hint = self._infer_hint(request)
        if auto_hint:
            plugin = self._plugins.get(auto_hint)
            if plugin is not None:
                return plugin

        for plugin in self._plugins.values():
            if plugin.can_resolve(request):
                return plugin

        raise SourceResolveError(f"no source plugin found for query: {request.query}")

    def _infer_hint(self, request: MediaRequest) -> str | None:
        payload = request.context.get("source_payload") if isinstance(request.context, dict) else None
        if isinstance(payload, dict):
            source = str(payload.get("source") or "").strip().lower()
            source = self._normalize_hint(source)
            if source in self._plugins:
                return source

            legacy_query = str(
                payload.get("url")
                or payload.get("music_name")
                or payload.get("track_id")
                or payload.get("path")
                or payload.get("name")
                or ""
            )
            if legacy_query and self._looks_like_local(legacy_query):
                return "local_library"

        query = str(request.query or "").strip()
        if not query:
            return None

        parsed = urlparse(query)
        if parsed.scheme in {"http", "https"}:
            return "direct_url"
        if self._looks_like_local(query):
            return "local_library"
        return None

    def _normalize_hint(self, hint: str | None) -> str:
        raw = str(hint or "").strip().lower()
        return self.LEGACY_HINT_MAP.get(raw, raw)

    @staticmethod
    def _looks_like_local(query: str) -> bool:
        q = query.strip()
        if not q:
            return False
        if q.startswith("file://"):
            return True
        if q.startswith(("/", "./", "../")):
            return True
        if len(q) > 2 and q[1] == ":" and q[2] in {"/", "\\"}:
            return True
        if q.endswith((".mp3", ".flac", ".m4a", ".wav", ".aac", ".ogg")):
            return True
        try:
            p = Path(q)
            return p.exists() and p.is_file()
        except Exception:
            return False
