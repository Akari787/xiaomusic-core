from __future__ import annotations

from xiaomusic.core.errors.source_errors import SourceResolveError
from xiaomusic.core.models.media import MediaRequest
from xiaomusic.core.source.source_plugin import SourcePlugin


class SourceRegistry:
    """Registry for source plugin registration and lookup only."""

    def __init__(self) -> None:
        self._plugins: dict[str, SourcePlugin] = {}

    def register(self, plugin: SourcePlugin) -> None:
        self._plugins[plugin.name] = plugin

    def get_plugin(self, source_hint: str | None, request: MediaRequest) -> SourcePlugin:
        if source_hint and source_hint in self._plugins:
            return self._plugins[source_hint]

        for plugin in self._plugins.values():
            if plugin.can_resolve(request):
                return plugin

        raise SourceResolveError(f"no source plugin found for query: {request.query}")
