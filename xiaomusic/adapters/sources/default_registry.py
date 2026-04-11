from __future__ import annotations

from xiaomusic.adapters.sources.direct_url_source_plugin import DirectUrlSourcePlugin
from xiaomusic.adapters.sources.jellyfin_source_plugin import JellyfinSourcePlugin
from xiaomusic.adapters.sources.local_library_source_plugin import LocalLibrarySourcePlugin
from xiaomusic.adapters.sources.site_media_source_plugin import SiteMediaSourcePlugin
from xiaomusic.core.source import SourceRegistry
from xiaomusic.relay.url_classifier import UrlClassifier


def register_default_source_plugins(
    source_registry: SourceRegistry,
    xiaomusic,
    runtime_provider=None,
) -> None:
    """Register built-in source plugins in deterministic order."""

    jellyfin_classifier = UrlClassifier(
        jellyfin_base_url=str(getattr(xiaomusic.config, "jellyfin_base_url", "") or "")
    )
    source_registry.register(
        JellyfinSourcePlugin(
            _resolve_jellyfin_source_url(xiaomusic),
            classifier=jellyfin_classifier,
        )
    )
    source_registry.register(DirectUrlSourcePlugin(classifier=jellyfin_classifier))
    source_registry.register(LocalLibrarySourcePlugin(xiaomusic.music_library))
    source_registry.register(SiteMediaSourcePlugin(runtime_provider=runtime_provider))


def _resolve_jellyfin_source_url(xiaomusic):
    def _resolver(payload: dict) -> str:
        direct_url = str(payload.get("url") or "")
        if direct_url.startswith(("http://", "https://")):
            return direct_url

        plugin_url = str(xiaomusic.online_music_service._get_plugin_proxy_url(payload) or "")
        _, expanded_url = xiaomusic.music_library.expand_self_url(plugin_url)
        return str(expanded_url)

    return _resolver
