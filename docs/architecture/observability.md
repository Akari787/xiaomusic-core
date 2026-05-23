# S3-3: Snapshot 端点设计（可观测性）

> 设计时间：2026-05-16
> 任务：s3-c（Snapshot 端点）
> 前提：阶段2.5完成

## 1. 设计目标

`GET /debug/snapshot` 端点用于导出 xiaomusic-core 运行时的完整状态快照，供调试、监控和问题排查使用。

**设计原则**：
- 只读导出，不修改任何状态
- 输出完整的运行时状态，不遗漏关键维度
- JSON 格式，便于程序解析和日志集成
- 包含足够的上下文信息用于关联（request_id、play_id 等）

## 2. 端点规范

### 2.1 基本信息

| 属性 | 值 |
|---|---|
| Method | `GET` |
| Path | `/api/v1/debug/snapshot` |
| 认证要求 | 内部调试端点，建议添加 admin 认证或 `include_in_schema=False` |
| 输出格式 | `application/json` |
| 响应码 | `200`（成功）、`500`（内部错误） |

### 2.2 查询参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `request_id` | string | 否 | 过滤只包含特定 request_id 的事件 |
| `play_id` | string | 否 | 过滤只包含特定 play_id 的事件 |
| `include_logs` | bool | 否 | 是否包含最近的日志片段（默认 false） |
| `log_lines` | int | 否 | 包含的日志行数（默认 100，最大 1000） |

**示例**：

```
GET /api/v1/debug/snapshot?play_id=play_abc123&include_logs=true&log_lines=200
```

## 3. 输出 Schema

### 3.1 顶层结构

```json
{
  "meta": {
    "snapshot_id": "snap_xxxxxxxxxxxx",
    "snapshot_time": "2026-05-16T10:30:00.000Z",
    "uptime_seconds": 12345,
    "version": "x.x.x",
    "request_id": "optional_filter",
    "play_id": "optional_filter"
  },
  "runtime": { ... },
  "auth": { ... },
  "player": { ... },
  "queue": { ... },
  "sources": { ... },
  "session": { ... },
  "events": { ... },
  "logs": { ... }
}
```

### 3.2 meta 字段

```json
{
  "meta": {
    "snapshot_id": "snap_abc123def456",
    "snapshot_time": "2026-05-16T10:30:00.000Z",
    "uptime_seconds": 12345,
    "version": "1.2.3",
    "request_id": null,
    "play_id": null,
    "include_logs": true,
    "log_lines": 200
  }
}
```

### 3.3 runtime 状态

导出 RelayRuntime 的运行时状态：

```json
{
  "runtime": {
    "type": "relay",
    "stream_port": 18090,
    "uptime_seconds": 3600,
    "active_sessions": 2,
    "max_active_sessions": 3,
    "idle_timeout_seconds": 120,
    "resolve_timeout_seconds": 15,
    "healthz": {
      "cache_stats": {
        "live_entries": 10,
        "vod_entries": 5
      }
    }
  }
}
```

如果存在其他 Runtime 类型（当前只有 RelayRuntime），应分别导出。

### 3.4 auth 状态

导出 AuthManager 的状态（不包含敏感 token 实际值）：

```json
{
  "auth": {
    "state": "HEALTHY",
    "runtime_auth_ready": true,
    "token_expiry": {
      "mina_token": "2026-05-16T12:00:00.000Z",
      "account_token": "2026-05-16T14:00:00.000Z"
    },
    "recovery": {
      "in_progress": false,
      "last_attempt": "2026-05-16T09:00:00.000Z",
      "attempt_count": 0
    },
    "device_count": 3,
    "last_status_mapping_source": "healthy"
  }
}
```

**注意**：不导出实际 token 值，只导出元数据（如过期时间）。

### 3.5 player 状态

按设备导出播放状态：

```json
{
  "player": {
    "devices": [
      {
        "device_id": "device_abc",
        "device_name": "小米音箱-客厅",
        "state": "playing",
        "is_playing": true,
        "cur_music": "周杰伦-晴天",
        "current_index": 3,
        "queue_length": 20,
        "play_mode": "sequence",
        "volume": 65,
        "duration_seconds": 267,
        "elapsed_seconds": 45,
        "started_at": "2026-05-16T10:25:00.000Z",
        "source": "jellyfin"
      }
    ]
  }
}
```

### 3.6 queue 状态

导出当前播放队列（简略形式，不包含完整 metadata）：

```json
{
  "queue": {
    "device_id": "device_abc",
    "current_index": 3,
    "items": [
      {"index": 0, "title": "歌曲A", "source": "jellyfin"},
      {"index": 1, "title": "歌曲B", "source": "jellyfin"},
      {"index": 2, "title": "歌曲C", "source": "local_library"},
      {"index": 3, "title": "周杰伦-晴天", "source": "jellyfin", "current": true},
      {"index": 4, "title": "歌曲E", "source": "jellyfin"}
    ],
    "version": "2026-05-16T10:25:00"
  }
}
```

### 3.7 sources 状态

导出注册的所有 Source 插件状态：

```json
{
  "sources": {
    "plugins": [
      {"name": "jellyfin", "registered": true, "ready": true},
      {"name": "direct_url", "registered": true, "ready": true},
      {"name": "local_library", "registered": true, "ready": true},
      {"name": "site_media", "registered": true, "ready": true}
    ],
    "active_count": 4
  }
}
```

### 3.8 session 状态（流会话）

导出当前活跃的流会话：

```json
{
  "session": {
    "active": [
      {
        "sid": "sess_abc123",
        "state": "streaming",
        "mode": "relay",
        "started_at": "2026-05-16T10:20:00.000Z",
        "last_client_at": "2026-05-16T10:30:00.000Z",
        "input_url": "https://example.com/audio.mp3",
        "stream_url": "http://127.0.0.1:18090/stream/sess_abc123"
      }
    ],
    "active_count": 1,
    "total_count": 15
  }
}
```

### 3.9 events 状态

导出最近的事件历史（用于关联分析）：

```json
{
  "events": {
    "recent": [
      {
        "timestamp": "2026-05-16T10:29:55.000Z",
        "type": "PLAYBACK_STARTED",
        "play_id": "play_abc123",
        "device_id": "device_abc",
        "payload_version": 1
      },
      {
        "timestamp": "2026-05-16T10:29:50.000Z",
        "type": "PLAY_REQUESTED",
        "play_id": "play_abc123",
        "device_id": "device_abc",
        "payload_version": 1
      }
    ],
    "total_count": 1523,
    "by_type": {
      "PLAYBACK_STARTED": 523,
      "PLAYBACK_STOPPED": 498,
      "AUTH_EXPIRED": 12,
      "AUTH_RESTORED": 10
    }
  }
}
```

### 3.10 logs 字段（可选）

当 `include_logs=true` 时，包含最近的日志片段：

```json
{
  "logs": {
    "count": 200,
    "level_filter": null,
    "entries": [
      {"timestamp": "2026-05-16T10:29:55.123", "level": "INFO", "message": "playback started", "request_id": "abc123", "play_id": "play_xyz"},
      ...
    ]
  }
}
```

## 4. 实现建议

### 4.1 权限控制

- 此端点暴露大量内部状态，**仅限本地或 admin 访问**
- 建议在 `include_in_schema=False` 标记，并在 `system.py` 路由中判断来源 IP
- 生产环境应禁用或添加额外认证

### 4.2 性能考量

- 导出完整 snapshot 可能耗时较长（尤其是包含大量日志时）
- 建议 snapshot 生成在独立的线程/协程中，不阻塞主线程
- 设置超时（如 10 秒），超时后返回部分数据

### 4.3 与 Correlation ID 的集成

- snapshot 端点接受 `request_id` 或 `play_id` 参数
- 如果传入 `request_id`，只导出与该请求关联的事件和日志
- 如果传入 `play_id`，只导出与该播放会话关联的所有信息

### 4.4 长期存储

- snapshot 数据可以写入时序数据库（如 InfluxDB）用于长期趋势分析
- 可以计算：平均活跃会话数、播放次数/天、认证失败率等