# Plugin Architecture

## Source Plugins

Current official plugins:

- `DirectUrlSourcePlugin`
- `JellyfinSourcePlugin`
- `SiteMediaSourcePlugin`
- `LocalLibrarySourcePlugin`

Legacy source-hint compatibility (centralized in `SourceRegistry.LEGACY_HINT_MAP`):

- `http_url -> direct_url`
- `network_audio -> site_media`
- `local_music -> local_library`

Compatibility plugin:

- `LegacyPayloadSourcePlugin` (`compatibility_layer`)

## Plugin Contract

- Input: `MediaRequest`
- Output: `ResolvedMedia`
- Required method: `resolve()`
- Optional methods: `search()`, `browse()`

## Registration and Selection

- Plugins are registered in `SourceRegistry` during facade bootstrap.
- `SourceRegistry.get_plugin(source_hint, request)` selection order:
  1. explicit `source_hint`
  2. first plugin where `can_resolve(request)` is true

## Non-goals

- Plugins never decide transport.
- Plugins never write device reachability.
- Plugins never call transport router.
