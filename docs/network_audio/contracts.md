# Network Audio Unified Contracts

This document defines the only data structures allowed between network audio modules.

Note: runtime package path has been standardized to `xiaomusic/network_audio`.

## Models

- `UrlInfo`: classified and normalized input URL.
- `ResolveResult`: resolver output after `yt-dlp` stage.
- `Session`: stream session lifecycle state.
- `Event`: observable runtime event.

Canonical examples are stored in `docs/network_audio/contracts.examples.json` and are validated by unit tests.

## Error Codes

- `E_URL_UNSUPPORTED`
- `E_RESOLVE_TIMEOUT`
- `E_RESOLVE_NONZERO_EXIT`
- `E_STREAM_START_FAILED`
- `E_STREAM_NOT_FOUND`
- `E_STREAM_SINGLE_CLIENT_ONLY`
- `E_XIAOMI_PLAY_FAILED`
- `E_INTERNAL`
