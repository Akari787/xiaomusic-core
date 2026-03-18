# Playback Coordinator Interface (Updated)

## Purpose
Defines unified interaction between:
- API layer
- Source adapters
- Playback state machine

---

## PlayResolution

Adapter output:

{
  "source_type": "...",
  "playback_kind": "...",
  "tracks": [...],
  "current_index": 0,
  "context": {
    "type": "...",
    "id": "...",
    "name": "..."
  }
}

---

## Adapter Responsibilities
- Resolve query
- Select context
- Provide queue facts

---

## Framework Responsibilities
- Apply play_mode
- Compute next/previous
- Maintain state
