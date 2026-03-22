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

---

## Player State Projection

播放协调器内部维护的 `current_index` 和 `context` 事实，最终需要投影到 `GET /api/v1/player/state` 的聚合结果中：

- `current_index` → `data.current_index`
- `context.type` → `data.context_type`
- `context.id` → `data.context_id`
- `context.name` → `data.context_name`

同时需要生成稳定的 `current_track_id`，用于前端可靠判断切歌：
- 使用 `context_id + track_title` 的组合哈希生成
- 同一首歌在同一次连续播放期间保持稳定
- 切到下一首后必须变化

不要求暴露完整 tracks 队列，但至少要能输出当前 track identity + context facts，供 WebUI / HA / 第三方调用方做稳定状态同步。
