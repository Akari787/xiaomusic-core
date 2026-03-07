from __future__ import annotations

from xiaomusic.adapters.sources.direct_url_source_plugin import DirectUrlSourcePlugin
from xiaomusic.adapters.sources.jellyfin_source_plugin import JellyfinSourcePlugin
from xiaomusic.adapters.sources.local_library_source_plugin import LocalLibrarySourcePlugin
from xiaomusic.adapters.sources.site_media_source_plugin import SiteMediaSourcePlugin
from xiaomusic.core.source import SourceRegistry


def register_default_source_plugins(source_registry: SourceRegistry, xiaomusic) -> None:
    """Register built-in source plugins in deterministic order."""

    source_registry.register(JellyfinSourcePlugin(_resolve_jellyfin_source_url(xiaomusic)))
    source_registry.register(DirectUrlSourcePlugin())
    source_registry.register(LocalLibrarySourcePlugin(xiaomusic.music_library))
    source_registry.register(SiteMediaSourcePlugin())


def _resolve_jellyfin_source_url(xiaomusic):
    def _resolver(payload: dict) -> str:
        direct_url = str(payload.get("url") or "")
        if direct_url.startswith(("http://", "https://")):
            return direct_url

        plugin_url = str(xiaomusic.online_music_service._get_plugin_proxy_url(payload) or "")
        _, expanded_url = xiaomusic.music_library.expand_self_url(plugin_url)
        return str(expanded_url)

    return _resolver
