# XiaoMusic Runtime API v1 规范

版本：v1.1
状态：本版本目标契约
最后更新：2026-03-15
适用范围：XiaoMusic Runtime HTTP API（WebUI、Home Assistant 与第三方调用）

---

## 1. 设计目标

本版本 API 收口目标如下：

1. **彻底替代 `/cmd` 作为正式控制入口**
   - 正式 API 不再接受中文命令字符串。
   - `match_cmd()` 不再作为 HTTP v1 的业务分发器。

2. **统一 WebUI 与 HA 的调用模型**
   - WebUI 当前使用的历史 `/cmd` 能力，必须提供等价的结构化 v1 接口。
   - 面向 HA 的歌单播放类能力，必须直接以结构化 API 暴露，而不是依赖自然语言命令。

3. **统一请求/响应协议**
   - `/api/v1/*` 的请求体与响应体统一为 JSON。
   - 响应统一使用 envelope：`code/message/data/request_id`。

4. **控制面、资源面、状态面分离**
   - 控制类动作使用 `POST /api/v1/control/*`。
   - 播放列表与收藏等资源能力使用 `POST /api/v1/playlist/*`、`/api/v1/library/*`。
   - 状态查询使用 `GET /api/v1/*`。

---

## 2. 设计原则

### 2.1 单一播放入口

所有“播放某个具体媒体目标”的请求必须通过：

- `POST /api/v1/play`

说明：

- 单曲点播、直链播放、站点媒体播放、Jellyfin 解析结果播放等，统一归入 `/api/v1/play`。
- “播放某个歌单”与“播放歌单第 N 首”属于**歌单控制语义**，不属于 `/api/v1/play` 的职责范围。

### 2.2 `/cmd` 不再属于正式契约

- `/cmd` 不属于 Runtime API v1 正式白名单。
- `/cmd`、中文命令字符串、`match_cmd()`、`exec#...` 均不得作为新功能实现依据。
- 若迁移期仍保留 `/cmd`，它仅可作为兼容层存在，不得承载新能力。

### 2.3 统一 JSON Envelope

正式接口统一响应格式：

```json
{
  "code": 0,
  "message": "ok",
  "data": {},
  "request_id": "req_xxx"
}
```

约束：

- `code = 0` 表示成功。
- `code != 0` 表示失败。
- v1 不使用历史顶层字段（如 `ret`、`success`、`status`）作为主语义。
- 业务结果放入 `data`。

### 2.4 Runtime 负责业务判断

- 来源识别、插件选择、传输路径、状态聚合在 Runtime 内完成。
- WebUI/HA 只提交结构化参数，不做中文命令拼装，不做设备逻辑推断。

---

## 3. 命名空间

正式接口命名空间：

- `/api/v1`

正式接口必须以 `/api/v1` 开头。

---

## 4. 正式白名单接口

本版本正式白名单接口共 **20 个**：

### 4.1 播放与解析

1. `POST /api/v1/play`
2. `POST /api/v1/resolve`

### 4.2 基础控制

3. `POST /api/v1/control/stop`
4. `POST /api/v1/control/pause`
5. `POST /api/v1/control/resume`
6. `POST /api/v1/control/tts`
7. `POST /api/v1/control/volume`
8. `POST /api/v1/control/probe`
9. `POST /api/v1/control/previous`
10. `POST /api/v1/control/next`
11. `POST /api/v1/control/play-mode`
12. `POST /api/v1/control/shutdown-timer`

### 4.3 歌单与收藏

13. `POST /api/v1/playlist/play`
14. `POST /api/v1/playlist/play-index`
15. `POST /api/v1/library/favorites/add`
16. `POST /api/v1/library/favorites/remove`
17. `POST /api/v1/library/refresh`

### 4.4 查询接口

18. `GET /api/v1/devices`
19. `GET /api/v1/system/status`
20. `GET /api/v1/player/state`

> 注：本规范以 20 项为目标契约；若实现阶段选择暂缓 `POST /api/v1/resolve` 的正式收口，需要在实现文档中单独声明，不得影响其他结构化控制能力的落地。

---

## 5. 请求通用约束

### 5.1 Device ID

凡是对具体设备生效的控制类接口，请求体必须包含：

```json
{ "device_id": "<device_id>" }
```

字段约束：

- `device_id: string`
- 不允许使用中文设备名替代 `device_id` 作为正式协议字段。

### 5.2 中文命令字符串禁入

以下输入形式不得进入 v1 正式接口：

- `"上一首"`
- `"下一首"`
- `"30分钟后关机"`
- `"播放歌单日语"`
- `"播放列表第三个日语"`
- 任意 `cmd` 文本

这些形式只允许存在于旧兼容层，不得作为 v1 契约的一部分。

---

## 6. 播放与解析接口

## 6.1 POST /api/v1/play

用途：播放一个明确媒体目标。

请求示例：

```json
{
  "device_id": "<device_id>",
  "query": "https://example.com/song.mp3",
  "source_hint": "auto",
  "options": {}
}
```

字段说明：

- `device_id: string`，必填。
- `query: string`，必填；表示单曲名、URL、站点链接或其他可解析媒体目标。
- `source_hint: string`，可选，默认 `auto`。
- `options: object`，可选。

`source_hint` 允许值：

- `auto`
- `direct_url`
- `site_media`
- `jellyfin`
- `local_library`

成功响应示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "status": "playing",
    "device_id": "<device_id>",
    "source_plugin": "direct_url",
    "transport": "mina"
  },
  "request_id": "req_123"
}
```

### 6.2 POST /api/v1/resolve

用途：只解析，不播放。

请求示例：

```json
{
  "query": "https://youtube.com/...",
  "source_hint": "auto",
  "options": {}
}
```

说明：

- 该接口不替代 `/api/v1/play`。
- 该接口适合调试、预解析或上层编排使用。

---

## 7. 基础控制接口

## 7.1 POST /api/v1/control/stop

用途：停止当前播放。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

## 7.2 POST /api/v1/control/pause

用途：暂停当前播放。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

## 7.3 POST /api/v1/control/resume

用途：恢复当前播放。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

## 7.4 POST /api/v1/control/tts

用途：播放一段 TTS 文本。

请求示例：

```json
{
  "device_id": "<device_id>",
  "text": "你好，欢迎使用 XiaoMusic"
}
```

## 7.5 POST /api/v1/control/volume

用途：设置设备音量。

请求示例：

```json
{
  "device_id": "<device_id>",
  "volume": 35
}
```

字段约束：

- `volume: integer`
- 推荐范围：`0 ~ 100`

## 7.6 POST /api/v1/control/probe

用途：执行设备探活或基础可用性检查。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

## 7.7 POST /api/v1/control/previous

用途：切到上一首。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

说明：

- 该接口用于替代 `/cmd` 的 `上一首`。
- 不允许再通过命令字符串表达此能力。

## 7.8 POST /api/v1/control/next

用途：切到下一首。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

说明：

- 该接口用于替代 `/cmd` 的 `下一首`。

## 7.9 POST /api/v1/control/play-mode

用途：设置播放模式。

请求示例：

```json
{
  "device_id": "<device_id>",
  "play_mode": "random"
}
```

字段约束：

- `play_mode: string`，必填。
- 允许值：
  - `one`
  - `all`
  - `random`
  - `single`
  - `sequence`

映射关系：

- `one`：单曲循环
- `all`：全部循环
- `random`：随机播放
- `single`：单曲播放
- `sequence`：顺序播放

约束：

- v1 协议只接受英文枚举值。
- WebUI/HA 不允许以中文文案作为协议值。

## 7.10 POST /api/v1/control/shutdown-timer

用途：设置定时停止播放。

请求示例：

```json
{
  "device_id": "<device_id>",
  "minutes": 30
}
```

字段约束：

- `minutes: integer`，必填。
- `minutes > 0`

说明：

- 该接口用于替代 `/cmd` 的 `xx分钟后关机`。
- v1 协议不接受 `"30分钟后关机"` 这类自然语言文本。
- 若后续需要支持取消定时关机，应新增结构化能力，不得回退到字符串命令。

---

## 8. 歌单与收藏接口

## 8.1 POST /api/v1/playlist/play

用途：播放指定歌单。

适用场景：

- WebUI 后续歌单播放能力
- Home Assistant 自动化场景

请求示例：

```json
{
  "device_id": "<device_id>",
  "playlist_name": "日语"
}
```

字段约束：

- `playlist_name: string`，必填。

说明：

- 该接口用于替代 `/cmd` 的 `播放歌单xxx` / `播放列表xxx`。
- 这是 HA 侧的重要编排接口，必须是结构化能力。

## 8.2 POST /api/v1/playlist/play-index

用途：播放指定歌单中的第 N 首。

请求示例：

```json
{
  "device_id": "<device_id>",
  "playlist_name": "日语",
  "index": 3
}
```

字段约束：

- `playlist_name: string`，必填。
- `index: integer`，必填。
- 建议约定为 **1-based** 序号，与历史“第 N 个”语义保持一致。

说明：

- 该接口用于替代 `/cmd` 的 `播放列表第N个xxx`。
- v1 协议不接受 `"播放列表第三个日语"` 这类自然语言表达。

## 8.3 POST /api/v1/library/favorites/add

用途：将歌曲加入收藏。

请求示例：

```json
{
  "device_id": "<device_id>",
  "track_name": "夜に駆ける"
}
```

字段约束：

- `device_id: string`，必填。
- `track_name: string`，可选。

行为约束：

- 当 `track_name` 为空时，默认以当前正在播放的歌曲作为目标。

说明：

- 该接口用于替代 `/cmd` 的 `加入收藏`。

## 8.4 POST /api/v1/library/favorites/remove

用途：将歌曲移出收藏。

请求示例：

```json
{
  "device_id": "<device_id>",
  "track_name": "夜に駆ける"
}
```

字段与行为约束：

- 与 `/api/v1/library/favorites/add` 保持一致。
- 当 `track_name` 为空时，默认以当前正在播放的歌曲作为目标。

说明：

- 该接口用于替代 `/cmd` 的 `取消收藏`。
- 收藏能力应以 add/remove 成对提供，便于 HA 自动化闭环控制。

## 8.5 POST /api/v1/library/refresh

用途：刷新音乐库或歌单索引。

请求示例：

```json
{}
```

可选扩展示例：

```json
{
  "scope": "all"
}
```

说明：

- 该接口用于替代 `/cmd` 的 `刷新列表`。
- 该接口也用于替代旧的刷新入口，不再新增新的 legacy 刷新路由。

---

## 9. 查询接口

## 9.1 GET /api/v1/devices

用途：获取设备列表。

成功响应示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "devices": [
      {
        "device_id": "<device_id>",
        "name": "XiaoAi",
        "model": "LX06",
        "online": true
      }
    ]
  },
  "request_id": "req_123"
}
```

## 9.2 GET /api/v1/system/status

用途：获取系统状态。

成功响应示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "status": "ok",
    "version": "1.0.7",
    "devices_count": 1
  },
  "request_id": "req_123"
}
```

## 9.3 GET /api/v1/player/state

用途：统一播放状态查询。

查询参数：

- `device_id`（必填）

`data` 字段定义：

- `device_id: string`
- `is_playing: boolean`
- `cur_music: string`
- `offset: number`（秒，整数，>= 0）
- `duration: number`（秒，整数，>= 0）

建议扩展字段：

- `volume: integer`
- `play_mode: string`
- `playlist_name: string | null`
- `playlist_index: integer | null`
- `shutdown_timer_minutes: integer | null`

约束：

- `offset/duration` 单位固定为秒。
- WebUI 不允许再做毫秒/秒猜测。
- 状态由 Runtime 统一聚合，避免前端通过多接口拼装。

成功响应示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "device_id": "<device_id>",
    "is_playing": true,
    "cur_music": "test.mp3",
    "offset": 12,
    "duration": 180,
    "volume": 35,
    "play_mode": "random",
    "playlist_name": "日语",
    "playlist_index": 3,
    "shutdown_timer_minutes": 30
  },
  "request_id": "req_123"
}
```

---

## 10. WebUI 与 HA 调用约束

### 10.1 WebUI 约束

WebUI 不得再调用：

- `POST /cmd`
- `GET /getvolume`
- 任何以中文命令字符串作为协议输入的接口

WebUI 应迁移到本规范定义的正式接口。

### 10.2 Home Assistant 约束

HA 对接应优先使用结构化控制能力，尤其是：

- `POST /api/v1/control/previous`
- `POST /api/v1/control/next`
- `POST /api/v1/control/play-mode`
- `POST /api/v1/control/shutdown-timer`
- `POST /api/v1/playlist/play`
- `POST /api/v1/playlist/play-index`
- `POST /api/v1/library/favorites/add`
- `POST /api/v1/library/favorites/remove`

HA 不应通过发送中文命令字符串调用历史 `/cmd`。

---

## 11. 兼容与迁移策略

### 11.1 迁移原则

- 非 `/api/v1/*` 历史接口在迁移期可暂时保留。
- 历史接口不属于正式契约。
- 新功能必须优先落入 `/api/v1/*`。

### 11.2 `/cmd` 的迁移地位

- `/cmd` 仅可作为迁移期兼容层。
- `/cmd` 不得再承载任何新能力。
- 当 WebUI 与 HA 所需能力全部迁移完成后，`/cmd` 应可直接下线。

### 11.3 当前需要从 `/cmd` 迁移的核心能力

- `上一首`
- `下一首`
- `单曲循环 / 全部循环 / 随机播放 / 单曲播放 / 顺序播放`
- `xx分钟后关机`
- `加入收藏`
- `取消收藏`
- `播放歌单xxx`
- `播放列表第N个xxx`
- `刷新列表`

### 11.4 不进入正式 v1 的历史能力

以下能力不进入正式 v1 契约：

- 自定义口令输入
- `exec#...`
- `match_cmd()` 动态分发机制
- `active_cmd` 作为 HTTP 正式控制面的规则来源

---

## 12. 错误码

错误码分段：

- `1xxxx` 系统
- `2xxxx` 来源解析
- `3xxxx` Delivery
- `4xxxx` Transport
- `5xxxx` API 请求

详细定义见：

- `docs/dev/runtime_contracts.md`
- 相关运行时规范文档



## context_hint
Optional object to guide context selection.

```json
{
  "context_type": "...",
  "context_id": "...",
  "context_name": "..."
}
```
Rules:
- If provided, adapters MUST prioritize it
- Otherwise adapter selects context


## Player State (Unified Context)
```json
{
  "source_type": "...",
  "playback_kind": "single|queue|stream",
  "current_track_id": "...",
  "current_track_title": "...",
  "current_track_duration": 0,
  "play_mode": "...",
  "context_type": "...",
  "context_id": "...",
  "context_name": "...",
  "queue_supported": true,
  "current_index": 0,
  "queue_length": 0,
  "has_next": true,
  "has_previous": true
}
```
