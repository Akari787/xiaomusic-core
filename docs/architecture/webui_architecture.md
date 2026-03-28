# WebUI 架构（WebUI Architecture）

版本：v1.0
状态：正式架构文档
最后更新：2026-03-28

本文档定义 WebUI 与后端的接口依赖边界、状态来源模型与禁止依赖范围。具体状态字段语义见 `docs/spec/player_state_projection_spec.md`，SSE 协议见 `docs/spec/player_stream_sse_spec.md`，API 契约见 `docs/api/api_v1_spec.md`。

---

## 1. WebUI 依赖的接口层

WebUI 只允许依赖以下两个接口层：

### 1.1 Public API（`/api/v1/*`）

所有 `docs/api/api_v1_spec.md` 白名单中的接口。WebUI、Home Assistant、第三方调用方均可使用。

WebUI 当前使用的 Public API：

- `GET /api/v1/player/stream`（**SSE 主通道**，播放状态）
- `GET /api/v1/player/state`（初始化 + 降级 fallback）
- `GET /api/v1/devices`
- `GET /api/v1/library/playlists`
- `GET /api/v1/system/status`
- `GET /api/v1/system/settings`
- `POST /api/v1/system/settings`
- `POST /api/v1/system/settings/item`
- `POST /api/v1/play`
- `POST /api/v1/control/*`（stop、pause、resume、next、previous 等）
- `GET /api/v1/search/online`

### 1.2 Internal API（`/api/auth/*`、`/api/file/*`）

仅供 WebUI 使用，不对外承诺兼容性：

- `GET /api/auth/status`
- `POST /api/auth/refresh`
- `POST /api/auth/logout`
- `GET /api/get_qrcode`
- `POST /api/file/fetch_playlist_json`
- `POST /api/file/cleantempdir`

---

## 2. WebUI 的播放状态来源

WebUI 的播放状态由后端唯一决定。前端只消费，不裁决。

### 2.1 主通道：SSE

`GET /api/v1/player/stream` 是播放状态的主通道。WebUI 必须在 SSE 连接正常时以 SSE 事件作为唯一状态来源，不得同时轮询 `/player/state`。

### 2.2 初始化 + 降级通道：HTTP

`GET /api/v1/player/state` 用于：

- 页面首次加载时获取初始快照
- SSE 断线期间的降级轮询（间隔 ≥ 3s）

SSE 重连成功后，必须立即停止轮询。

### 2.3 状态消费模型

```
serverState（来自 SSE / HTTP，只读）
    ↓
revision gate（去重过滤）
    ↓
session switch handler（play_session_id 变化 → 全 UI 切换）
    ↓
render（基于 transport_state / track.id / position_ms 等字段）
```

前端维护的 `uiState` 只包含纯展示辅助状态（连接状态、切歌动画标记、进度插值），不代表任何播放事实。

完整消费规则见 `docs/spec/webui_playback_state_machine_spec.md`。

---

## 3. WebUI 不允许依赖的内容

以下内容明确禁止 WebUI 依赖：

### 3.1 后端内部对象

- `XiaoMusic` 实例或其任何属性
- `DevicePlayer` 实例
- `PlaybackFacade` 内部状态
- `PlaybackCoordinator` 内部状态
- `SourceRegistry` 或任何 source 插件实现

### 3.2 未承诺的 API 字段

- `api_v1_spec.md` 白名单接口中未声明的 `data` 字段
- 通过观察实现行为"发现"但未在规范中列出的字段
- `docs/api/api_v1_spec.md` 第 13 章列出的兼容字段（`cur_music`、`is_playing`、`offset`、`duration` 等）——这些字段已废弃，新代码不得读取

### 3.3 内部 API 路由

- 任何未列入 `api_v1_spec.md` 白名单且未列入 Internal API 清单的路由
- `Forbidden / Removed` 分类中的接口

### 3.4 WebSocket 状态推送

系统当前不提供 WebSocket 状态推送。历史讨论过的 WebSocket 方案已废弃，不得在新实现中引入。状态推送统一通过 SSE（`GET /api/v1/player/stream`）实现。

---

## 4. WebUI 与各后端能力的关系

### 4.1 播放器状态

- 来源：`GET /api/v1/player/stream`（主）/ `GET /api/v1/player/state`（备）
- 字段模型：`docs/spec/player_state_projection_spec.md`
- 前端不得以本地推算覆盖服务端状态

### 4.2 系统状态与设置

- 来源：`GET /api/v1/system/status`、`GET /api/v1/system/settings`
- 设置修改通过：`POST /api/v1/system/settings`、`POST /api/v1/system/settings/item`

### 4.3 歌单与音乐库

- 来源：`GET /api/v1/library/playlists`
- 每个歌曲条目包含 `id`（稳定标识）和 `title`（展示名）
- 歌单选中项同步必须基于 `track.id` 对照，不得依赖 `title` 文本匹配

### 4.4 认证状态

- 来源：`GET /api/auth/status`（Internal API）
- 刷新：`POST /api/auth/refresh`

---

## 5. 接口变更时的影响评估

当后端 API 发生以下变更时，WebUI 必须跟随更新：

- Public API 白名单接口的成功响应新增必须字段
- `player_state_projection_spec.md` 中正式字段的语义变化
- SSE 事件格式变化（见 `player_stream_sse_spec.md`）

当后端发生以下变更时，WebUI **不受影响**（前提是未依赖上述禁止内容）：

- runtime 内部实现调整
- source 插件内部实现调整
- transport 路由策略调整
- 兼容字段的删除（`cur_music`、`is_playing`、`offset`、`duration`）

---

## 6. 相关文档

| 文档 | 职责 |
|---|---|
| `docs/api/api_v1_spec.md` | v1 接口完整契约，WebUI 依赖的接口权威来源 |
| `docs/spec/player_state_projection_spec.md` | 播放状态快照字段模型与消费约束 |
| `docs/spec/player_stream_sse_spec.md` | SSE 推送协议详细规范 |
| `docs/spec/webui_playback_state_machine_spec.md` | WebUI 消费型状态机规范 |
| `docs/spec/webui_playback_state_machine_mapping.md` | WebUI 重构实现映射清单 |
| `docs/architecture/system_overview.md` | WebUI 在系统九个一级边界中的位置 |
