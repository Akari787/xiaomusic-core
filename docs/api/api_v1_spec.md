# XiaoMusic Runtime API v1 规范

版本：v1.0
状态：生效中
最后更新：2026-03-08
适用范围：XiaoMusic Runtime HTTP API（WebUI 与第三方调用）

---

## 1. 设计原则

1) 单一播放入口

- 所有播放请求必须通过 `POST /api/v1/play`。

2) 统一 JSON 请求

- `/api/v1/*` 的请求体与响应体均为 JSON。

3) 统一响应 Envelope

```json
{
  "code": 0,
  "message": "ok",
  "data": {},
  "request_id": "req_xxx"
}
```

- `code=0` 表示成功；`code!=0` 表示失败。
- 错误信息仅通过 `code/message/data` 表达，不使用历史顶层字段（如 `ret`、`success`）作为 v1 主语义。

4) Runtime 负责业务判断

- 来源识别、插件选择、传输路径、状态聚合都在 Runtime 完成。
- WebUI 仅展示结果，不做业务推断。

---

## 2. 命名空间

- 正式接口命名空间：`/api/v1`
- 正式接口必须以 `/api/v1` 开头。

---

## 3. 正式白名单接口（11 个）

1. `POST /api/v1/play`
2. `POST /api/v1/resolve`
3. `POST /api/v1/control/stop`
4. `POST /api/v1/control/pause`
5. `POST /api/v1/control/resume`
6. `POST /api/v1/control/tts`
7. `POST /api/v1/control/volume`
8. `POST /api/v1/control/probe`
9. `GET /api/v1/devices`
10. `GET /api/v1/system/status`
11. `GET /api/v1/player/state`

说明：第 11 项用于统一播放状态查询，解决旧状态接口与新播放链路不同步问题。

---

## 4. 播放接口

### POST /api/v1/play

请求示例：

```json
{
  "device_id": "981257654",
  "query": "https://example.com/song.mp3",
  "source_hint": "auto",
  "options": {}
}
```

`source_hint` 允许值：`auto` / `direct_url` / `site_media` / `jellyfin` / `local_library`。

成功响应示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "status": "playing",
    "device_id": "981257654",
    "source_plugin": "direct_url",
    "transport": "mina"
  },
  "request_id": "req_123"
}
```

---

## 5. 控制接口

- `POST /api/v1/control/stop`
- `POST /api/v1/control/pause`
- `POST /api/v1/control/resume`
- `POST /api/v1/control/tts`
- `POST /api/v1/control/volume`
- `POST /api/v1/control/probe`

除 `tts/volume` 外，请求体最小字段为：

```json
{ "device_id": "981257654" }
```

---

## 6. 查询接口

### GET /api/v1/devices

成功响应示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "devices": [
      {
        "device_id": "981257654",
        "name": "XiaoAi",
        "model": "LX06",
        "online": true
      }
    ]
  },
  "request_id": "req_123"
}
```

### GET /api/v1/system/status

成功响应示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "status": "ok",
    "version": "1.0.6",
    "devices_count": 1
  },
  "request_id": "req_123"
}
```

### GET /api/v1/player/state

用途：统一播放状态查询。

查询参数：

- `device_id`（必填）

`data` 字段定义：

- `device_id: string`
- `is_playing: boolean`
- `cur_music: string`
- `offset: number`（秒，整数，>=0）
- `duration: number`（秒，整数，>=0）

约束：

- `offset/duration` 单位固定为秒，WebUI 不允许再做毫秒猜测。
- 状态由 Runtime 统一聚合，避免前端多接口拼装。

成功响应示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "device_id": "981257654",
    "is_playing": true,
    "cur_music": "test.mp3",
    "offset": 12,
    "duration": 180
  },
  "request_id": "req_123"
}
```

---

## 7. 解析接口

### POST /api/v1/resolve

用途：只解析，不播放。

请求示例：

```json
{
  "query": "https://youtube.com/...",
  "source_hint": "auto",
  "options": {}
}
```

---

## 8. WebUI 调用约束

WebUI 仅允许调用白名单 11 个接口。

WebUI 不得调用：

- Runtime 内部函数
- 插件内部实现
- 传输层实现细节

---

## 9. 兼容与迁移策略

- 非 `/api/v1/*` 历史接口可在迁移期保留，但不属于正式契约。
- 新功能必须优先落入 `/api/v1/*`。
- 迁移完成后，历史接口按版本计划逐步下线。

---

## 10. 错误码

错误码分段：

- `1xxxx` 系统
- `2xxxx` 来源解析
- `3xxxx` Delivery
- `4xxxx` Transport
- `5xxxx` API 请求

详细定义见：`docs/dev/spec/runtime_specification.md`。
