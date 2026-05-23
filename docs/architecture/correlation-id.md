# S3-2: Correlation ID 设计

> 设计时间：2026-05-16
> 任务：s3-ab（Event 与 Correlation）
> 前提：阶段2.5完成

## 1. Correlation ID 概念

Correlation ID（关联ID）用于在分布式或复杂系统中追踪一个请求或会话的完整生命周期。不同粒度的 ID 追踪不同层级的事件链。

本设计定义三个层级的 ID：

| ID 粒度 | 范围 | 用途 |
|---|---|---|
| `request_id` | API 请求粒度 | 追踪单个 HTTP 请求的完整调用链 |
| `play_id` | 播放会话粒度 | 追踪一次播放从请求到完成的完整链路 |
| `session_id` | SSE 连接粒度 | 追踪一个 SSE 长连接的整个生命周期 |

## 2. request_id（API 请求粒度）

### 2.1 定义

`request_id` 是每次 API 调用的唯一标识符，从 HTTP 请求入口生成，沿整个调用链传递。

### 2.2 生成策略

**决策：API 网关生成，16字符十六进制，随 HTTP 请求头传递**

- Header 名称：`X-Request-ID`（外部）/ 内部用 `request_id` 字段
- 生成算法：`uuid4().hex[:16]`（128位熵，截断为64位）
- 如果请求头已携带 `X-Request-ID`，直接使用（允许外部追踪）

**实现位置**：`api/routers/v1.py` 的 `_next_request_id()` 函数

```python
def _next_request_id(raw: str | None = None) -> str:
    return str(raw or uuid4().hex[:16])
```

### 2.3 传递机制

所有内部调用必须携带 `request_id`：

```python
# API 层
result = await device_player.play(device_id, query, request_id=request_id)

# 事件发布
event_bus.publish("PLAYBACK_STARTED", request_id=request_id, ...)

# 日志
LOG.info("playback started", extra={"request_id": request_id})
```

### 2.4 响应回传

`request_id` 必须出现在 API 响应中：

```json
{
  "code": 0,
  "message": "ok",
  "data": {...},
  "request_id": "a1b2c3d4e5f6g7h8"
}
```

## 3. play_id（播放会话粒度）

### 3.1 定义

`play_id` 标识一次播放操作的完整生命周期。一次播放可能包含：解析、投递、播放开始、播放结束。所有相关的事件和日志共享同一个 `play_id`。

### 3.2 生成策略

**决策：PlaybackFacade 或 PlaybackCoordinator 生成，随播放请求创建**

- Header 名称：在内部上下文传递，不暴露到 HTTP API
- 生成算法：`f"play_{uuid4().hex[:12]}"`
- 生成时机：用户调用 `/api/v1/play` 时，在 `play_id` 字段中返回给调用者

### 3.3 传递机制

`play_id` 通过 `MediaRequest.context` 或 `request.context` 传递：

```python
# MediaRequest 构造时
media_request = MediaRequest(
    query=query,
    request_id=request_id,      # API request_id
    context={"play_id": play_id, ...}
)

# 事件中携带
event_bus.publish("PLAYBACK_STARTED", play_id=play_id, request_id=request_id, ...)
```

### 3.4 与 request_id 的关系

- 一个 `request_id` 对应一次 API 调用
- 一次 API 调用可能触发多个 `play_id`（如批量播放）
- `play_id` 关联多个 `request_id`（首次播放 + 后续轮询等）

```
request_id=a1b2 → play_id=play_abc123 → [PLAY_REQUESTED, SOURCE_RESOLVED, PLAYBACK_STARTED, PLAYBACK_STOPPED]
```

## 4. session_id（SSE 连接粒度）

### 4.1 定义

`session_id` 是 SSE/WebSocket 长连接的标识符，追踪整个连接的生命周期（建立 → 心跳 → 关闭）。

### 4.2 生成策略

**决策：SSE 连接建立时由服务器生成**

- 格式：`f"sess_{uuid4().hex[:16]}"`
- 通过 SSE 的 `Last-Event-ID` 字段支持重连后的会话恢复
- 在 `/api/v1/debug/auth_state` 等 SSE 端点中使用

### 4.3 传递机制

SSE 连接建立时返回 `session_id`：

```
event: connected
data: {"session_id": "sess_abc123def456", "server_time": 1234567890}

event: player_state_changed
data: {"state": "playing", "play_id": "play_xyz789"}
```

### 4.4 与 play_id 的关系

- 一个 `session_id` 期间可能发生多次 `play_id`
- 重连时如果 `Last-Event-ID` 有效，应恢复之前的会话状态

## 5. ID 生命周期

| ID | 生成位置 | 结束条件 | 持久化 |
|---|---|---|---|
| `request_id` | API 网关/路由入口 | HTTP 响应返回后仍有保留（用于调试） | 不持久化，仅日志 |
| `play_id` | PlaybackFacade | 播放完全结束（停止/失败/超时） | 写入 analytics，用于复盘 |
| `session_id` | SSE 连接建立 | SSE 连接断开 | 不持久化，但记录连接时长 |

## 6. 日志关联

所有日志条目应包含可关联的 ID：

```python
LOG.info(
    "playback started",
    extra={
        "request_id": request_id,
        "play_id": play_id,
        "session_id": session_id,
        "device_id": device_id,
    }
)
```

日志系统应支持按 `request_id` 或 `play_id` 过滤：

```bash
# 按 request_id 过滤
grep "request_id=a1b2c3d4" app.log

# 按 play_id 过滤（查看一次播放的完整链路）
grep "play_id=play_abc123" app.log
```

## 7. 调试端点

提供 `GET /api/v1/debug/snapshot` 端点，按 `request_id` 或 `play_id` 查询关联的完整事件链：

```json
{
  "request_id": "a1b2c3d4",
  "play_ids": ["play_abc123"],
  "events": [
    {"timestamp": 1234567890.1, "event": "PLAY_REQUESTED", "play_id": "play_abc123"},
    {"timestamp": 1234567890.5, "event": "SOURCE_RESOLVED", "play_id": "play_abc123"},
    {"timestamp": 1234567891.0, "event": "PLAYBACK_STARTED", "play_id": "play_abc123"}
  ],
  "logs": [...]
}
```

## 8. 实现优先级

1. **P0（立即实现）**：`request_id` 生成和传递（v1 API 已部分实现）
2. **P1（下个版本）**：`play_id` 生成、事件关联、日志集成
3. **P2（规划中）**：`session_id`、SSE 端点、snapshot 调试端点