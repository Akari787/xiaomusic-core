# System Overview

## Runtime Layers

- API layer receives user requests and maps them into core DTOs.
- `PlaybackCoordinator` orchestrates source resolve, delivery prepare, and transport dispatch.
- Source plugins return `ResolvedMedia` only; they do not select transport.
- `DeliveryAdapter` converts `ResolvedMedia` to `PreparedStream` and validates stream safety.
- `TransportRouter` selects transport by `capability ∩ policy` and executes fallback.

## Default Playback Path

`API -> PlaybackCoordinator -> SourceRegistry.get_plugin() -> SourcePlugin.resolve() -> DeliveryAdapter.prepare() -> TransportRouter.dispatch() -> Transport.play_url()`

## Error Flow

- Source failures raise `SourceResolveError`.
- Expired URLs raise `ExpiredStreamError`.
- Transport execution failures raise `TransportError`.
- Coordinator is the consolidation point; API returns unified `code/message/data` envelopes.

## Compatibility Strategy

- `LegacyPayloadSourcePlugin` is compatibility-only for non-migrated legacy payload callers.
- Legacy runtime branches in facade are marked `compatibility_layer` with planned removal in next major release.
