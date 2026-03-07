from __future__ import annotations

from xiaomusic.adapters.sources.http_url_source_plugin import HttpUrlSourcePlugin
from xiaomusic.adapters.sources.jellyfin_source_plugin import JellyfinSourcePlugin
from xiaomusic.adapters.sources.local_music_source_plugin import LocalMusicSourcePlugin
from xiaomusic.adapters.sources.network_audio_source_plugin import NetworkAudioSourcePlugin
from xiaomusic.core.source import SourceRegistry


def register_default_source_plugins(source_registry: SourceRegistry, xiaomusic) -> None:
    """Register built-in source plugins in deterministic order."""

    source_registry.register(JellyfinSourcePlugin(_resolve_jellyfin_source_url(xiaomusic)))
    source_registry.register(HttpUrlSourcePlugin())
    source_registry.register(LocalMusicSourcePlugin(xiaomusic.music_library))
    source_registry.register(NetworkAudioSourcePlugin())


def _resolve_jellyfin_source_url(xiaomusic):
    def _resolver(payload: dict) -> str:
        direct_url = str(payload.get("url") or "")
        if direct_url.startswith(("http://", "https://")):
            return direct_url

        plugin_url = str(xiaomusic.online_music_service._get_plugin_proxy_url(payload) or "")
        _, expanded_url = xiaomusic.music_library.expand_self_url(plugin_url)
        return str(expanded_url)

    return _resolver
