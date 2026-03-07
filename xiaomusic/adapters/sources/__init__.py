from xiaomusic.adapters.sources.default_registry import register_default_source_plugins
from xiaomusic.adapters.sources.direct_url_source_plugin import DirectUrlSourcePlugin
from xiaomusic.adapters.sources.jellyfin_source_plugin import JellyfinSourcePlugin
from xiaomusic.adapters.sources.legacy_payload_source_plugin import LegacyPayloadSourcePlugin
from xiaomusic.adapters.sources.local_library_source_plugin import LocalLibrarySourcePlugin
from xiaomusic.adapters.sources.site_media_source_plugin import SiteMediaSourcePlugin

__all__ = [
    "register_default_source_plugins",
    "DirectUrlSourcePlugin",
    "JellyfinSourcePlugin",
    "LocalLibrarySourcePlugin",
    "SiteMediaSourcePlugin",
    "LegacyPayloadSourcePlugin",
]
