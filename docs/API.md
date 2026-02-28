# API 文档

本文档描述 `xiaomusic-oauth2` 当前稳定的接口约定与调用方式。

## 1. 总体约定

- 控制面统一使用 `/api/v1/*`
- 媒体流回放使用 `/network_audio/stream/{sid}`
- 响应语义统一：`success == ok`
- 失败时返回：`error_code` + `message`

## 2. 控制面接口（/api/v1/*）

### 2.1 播放链接

- 路径：`POST /api/v1/play_url`
- 用途：播放任意可解析音频链接（直链、B 站、YouTube 等）

请求体示例：

```json
{
  "speaker_id": "<SPEAKER_ID>",
  "url": "https://example.com/test.mp3",
  "options": {
    "no_cache": false
  }
}
```

关键响应字段：

- `ok/success`：请求是否成功
- `sid`：会话 ID，用于后续 `status/stop`
- `state`：当前状态（例如 `streaming`）
- `stream_url`：内部回放地址（通常是 `/network_audio/stream/{sid}`）
- `error_code/message`：失败信息
- `cache_hit/resolve_ms`：解析缓存命中与解析耗时

### 2.2 停止播放

- 路径：`POST /api/v1/stop`
- 用途：停止指定会话或设备播放

请求体示例：

```json
{
  "sid": "s_xxx",
  "speaker_id": "<SPEAKER_ID>"
}
```

关键响应字段：

- `ok/success`
- `sid`
- `state`（成功通常为 `stopped`）
- `error_code/message`

### 2.3 查询状态

- 路径：`GET /api/v1/status`
- 用途：查询会话或设备状态

查询参数：

- `sid`：按会话查询（优先）
- `speaker_id`：按设备查询

关键响应字段：

- 基础：`ok/success/error_code/message/sid/speaker_id/state`
- 可观测：`stage/last_transition_at/last_error_code/reconnect_count/cache_hit/resolve_ms`

### 2.4 测试可达性

- 路径：`POST /api/v1/test_reachability`
- 用途：验证 Base URL 可达性，辅助排障

请求体示例：

```json
{
  "speaker_id": "<SPEAKER_ID>",
  "base_url": "http://<HOST>:<PORT>"
}
```

关键响应字段：

- `ok/success`
- `reachable`
- `test_url`
- `sid`
- `error_code/message`

### 2.5 自动检测 Base URL

- 路径：`GET /api/v1/detect_base_url`
- 用途：自动检测推荐 base_url

关键响应字段：

- `ok/success`
- `base_url`
- `error_code/message`

### 2.6 清理会话

- 路径：`POST /api/v1/sessions/cleanup`
- 用途：手动清理会话

请求体（可选）示例：

```json
{
  "max_sessions": 100,
  "ttl_seconds": 3600
}
```

关键响应字段：

- `ok/success`
- `removed`
- `remaining`
- `error_code/message`

## 3. 非控制面接口（保留）

- `GET /network_audio/stream/{sid}`：音频流回放通道
- `GET /network_audio/healthz`：健康状态（含缓存与会话统计）
- `GET /network_audio/sessions`：会话列表观测

## 4. 快速调用示例

```bash
curl -X POST http://<HOST>:<PORT>/api/v1/play_url \
  -H "Content-Type: application/json" \
  -d '{"speaker_id":"<SPEAKER_ID>","url":"https://example.com/test.mp3"}'

curl -X POST http://<HOST>:<PORT>/api/v1/stop \
  -H "Content-Type: application/json" \
  -d '{"speaker_id":"<SPEAKER_ID>"}'

curl "http://<HOST>:<PORT>/api/v1/status?speaker_id=<SPEAKER_ID>"
```
