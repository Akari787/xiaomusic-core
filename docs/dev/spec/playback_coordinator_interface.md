# Playback Coordinator Interface (Unified)

## Purpose
Defines the unified interaction between:
- API layer
- Source adapters
- Playback state machine

## PlayResolution (Adapter Output)
```json
{
  "source_type": "...",
  "playback_kind": "single|queue|stream",
  "tracks": [],
  "current_index": 0,
  "context": {
    "type": "...",
    "id": "...",
    "name": "..."
  },
  "queue_supported": true,
  "queue_length": 0
}
```

## Adapter Responsibilities (Facts)
- Resolve request (query / id / url)
- Select active context (respect `context_hint` if provided)
- Provide:
  - source_type
  - playback_kind
  - current track info
  - context info
  - queue_supported / queue_length / current_index

## Framework Responsibilities (Strategy)
- Maintain `play_mode`
- Compute `has_next` / `has_previous`
- Decide end-of-playback behavior
- Execute ONLY via device_player (single state machine)

## Execution Flow (Authoritative)
/api/v1/play
  → resolver (select adapter)
  → PlayResolution
  → build unified context
  → device_player

## Forbidden
- Direct invocation of legacy paths
- Multiple execution branches per source
