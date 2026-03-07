from xiaomusic.adapters.sources.default_registry import register_default_source_plugins
from xiaomusic.adapters.sources.http_url_source_plugin import HttpUrlSourcePlugin
from xiaomusic.adapters.sources.jellyfin_source_plugin import JellyfinSourcePlugin
from xiaomusic.adapters.sources.legacy_payload_source_plugin import LegacyPayloadSourcePlugin
from xiaomusic.adapters.sources.local_music_source_plugin import LocalMusicSourcePlugin
from xiaomusic.adapters.sources.network_audio_source_plugin import NetworkAudioSourcePlugin

__all__ = [
    "register_default_source_plugins",
    "HttpUrlSourcePlugin",
    "JellyfinSourcePlugin",
    "LocalMusicSourcePlugin",
    "NetworkAudioSourcePlugin",
    "LegacyPayloadSourcePlugin",
]
