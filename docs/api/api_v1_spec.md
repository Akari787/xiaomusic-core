# XiaoMusic Runtime API v1 规范

版本：v1.3
状态：正式契约
最后更新：2026-03-22
适用范围：XiaoMusic Runtime HTTP API 与 SSE 状态流（WebUI、Home Assistant 与第三方调用）

---

## 1. 总则与契约优先级

### 1.1 唯一权威来源

本文档是 XiaoMusic Runtime v1 API 的唯一权威契约来源。

本文档定义并约束以下内容：

- 对外 HTTP 接口行为
- SSE 状态流接口行为
- 请求字段与查询参数
- 成功响应结构
- 错误响应结构
- v1 白名单接口的内部归属路径

### 1.2 与上位规范的关系

以下规范文档是本文档的上位规范，本文档不重复定义其内容，当本文档与上位规范冲突时，以上位规范为准：

- `docs/spec/player_state_projection_spec.md`（以下简称"投影规范"）：定义权威播放状态快照的字段模型、语义与消费约束。本文档中 `GET /api/v1/player/state` 与 `GET /api/v1/player/stream` 的 `data` 字段结构，必须完全遵守投影规范。
- `docs/spec/player_stream_sse_spec.md`（以下简称"SSE 规范"）：定义 `GET /api/v1/player/stream` 的 SSE 传输协议细节。本文档对该接口的描述是契约级摘要，完整协议由 SSE 规范承接。

### 1.3 冲突裁决规则

当以下内容与本文档冲突时，一律以本文档为准：

- 当前实现行为
- 历史文档
- 前端假设
- 手工验收口径
- 临时调试结论

后续所有代码修复、前端修复、测试修复与验收结论，均以本文档为基准。

### 1.4 显式列出原则

只有本文档中显式列出的行为，才属于 v1 正式承诺。

下列内容若未在本文档中被明确定义，则不属于 v1 契约：

- 额外顶层字段
- 未声明的 `data` 字段
- 未声明的错误阶段值
- 未声明的内部归属路径
- 未声明的字段同构关系

### 1.5 规范要求与实现现状

本文档中的"必须 / 不得 / 应 / 可以"均为规范要求。

若当前实现与规范要求不一致，应判定为"实现不符合规范"，而不是修改规范去描述偏差实现。

---

## 2. 设计目标与范围

### 2.1 v1 的定位

v1 API 是 XiaoMusic Runtime 对外稳定控制面的正式契约，面向：

- WebUI
- Home Assistant
- 第三方自动化调用方

### 2.2 v1 的职责边界

v1 API 负责暴露以下正式能力：

- 统一播放入口与媒体解析
- 设备控制动作
- 歌单与收藏控制
- 设备与系统状态查询
- 播放器权威状态快照查询（`GET /api/v1/player/state`）
- 播放器状态 SSE 推送流（`GET /api/v1/player/stream`）

### 2.3 统一播放入口原则

`POST /api/v1/play` 是 v1 的唯一正式播放入口。

所有正式播放请求必须通过 `POST /api/v1/play` 进入统一播放执行路径。

`POST /api/v1/playlist/play` 与 `POST /api/v1/playlist/play-index` 已从正式实现中删除。

约束：

- 新前端功能不得新增对 `/api/v1/playlist/*` 的依赖
- 新插件能力与新来源扩展必须通过 `/api/v1/play` 接入
- 不再对 `/api/v1/playlist/*` 做长期播放能力扩展承诺

### 2.4 命令接口与状态接口的解耦原则

播放控制命令（`POST /api/v1/play`、`POST /api/v1/control/*` 等）与播放状态观测（`GET /api/v1/player/state`、`GET /api/v1/player/stream`）是两条相互独立的通道。

以下约束必须遵守：

- Class A 命令接口的成功响应只承诺"动作已进入链路"与 `transport` 字段，不承诺命令执行后的播放器最终状态。
- 调用方不得依赖命令响应体中的字段推断播放器的当前播放状态。
- 播放器的权威当前状态，只能通过 `GET /api/v1/player/state` 或 `GET /api/v1/player/stream` 获取。
- 前端不得将命令响应中的 `transport` 字段直接写入播放状态展示层。

### 2.5 非 v1 范围

以下内容不属于 v1 正式契约：

- 中文命令字符串
- 依赖自然语言表达的控制入口
- 未列入白名单的 `/api/v1/*` 路由
- 未通过统一 envelope 暴露的 HTTP 返回形式

---

## 3. 接口分层与边界

### 3.1 Public API

Public API 是唯一正式对外接口层。

定义：

- `/api/v1/*` 是唯一正式对外接口层
- 仅第 5 章白名单中的接口属于 Public API
- 仅 Public API 对 WebUI、Home Assistant、插件与第三方调用方提供兼容性与长期稳定性承诺
- 任何未列入第 5 章白名单的接口都不属于 Public API

Public API 面向：

- WebUI
- Home Assistant
- 插件
- 第三方自动化调用方

### 3.2 Internal API

Internal API 是非 `/api/v1/*` 的内部前后端通信接口层。

定义：

- Internal API 不是"暂时未迁移完成的 v1 残留"
- Internal API 是有意保留的内部层，用于承载认证、会话、管理、工具、文件辅助与 WebUI 专用交互
- Internal API 仅供 WebUI 与项目内部模块使用
- Internal API 不属于公开稳定契约
- Internal API 不承诺兼容性
- 插件、第三方调用方、对外接入说明不得把 Internal API 当作正式能力依赖

### 3.3 Forbidden / Removed

Forbidden / Removed 是已删除接口与明确禁止恢复的入口集合。

定义：

- 已删除接口属于 Forbidden / Removed
- 明确禁止恢复的入口类型属于 Forbidden / Removed
- Forbidden / Removed 不得以兼容、桥接、legacy、deprecated wrapper 等名义重新引入
- Forbidden / Removed 不得重新进入正式播放、正式控制或正式查询路径

### 3.4 当前接口分层清单

Public API：

- 第 5 章正式白名单接口全部归属 Public API

Internal API：

- 认证 / 会话 Internal API
  - `GET /api/auth/status`
  - `POST /api/auth/refresh`
  - `POST /api/auth/logout`
  - `GET /api/get_qrcode`
  - 归类理由：这些接口服务于认证状态、会话刷新与二维码登录流程，属于 WebUI 内部认证交互，不属于插件与第三方可复用的产品能力

- 管理 / 文件 / 工具 Internal API
  - `POST /api/file/fetch_playlist_json`
  - `POST /api/file/cleantempdir`
  - 归类理由：这些接口服务于歌单 JSON 导入与目录清理等内部文件/工具动作，不属于对外稳定控制面

Forbidden / Removed：

- 已删除的非正式播放入口
  - `POST /api/v1/playlist/play`
  - `POST /api/v1/playlist/play-index`
- 已删除的旧 HTTP wrapper
  - `GET /getplayerstatus`
  - `POST /setvolume`
  - `GET /playtts`
  - `POST /device/stop`
- 已删除的 compatibility / legacy facade 方法
  - `stop_legacy`
  - `pause_legacy`
  - `tts_legacy`
  - `set_volume_legacy`
- 禁止恢复的入口类型
  - 中文命令入口
  - cmd 风格入口
  - 自然语言控制入口
  - 任何重新引入并行播放入口的设计

### 3.5 进入 v1 的准入标准

一个接口只有在同时满足以下条件时，才应进入 Public API / v1：

- 属于用户面主功能或播放主流程
- 对插件、Home Assistant、第三方调用方具有明确复用价值
- 值得长期稳定承诺
- 能遵守统一 envelope 与结构化错误模型
- 表达的是产品能力，而不是内部实现细节、后台流程或管理动作

以下类型原则上不进入 v1，除非被重新设计为明确的产品能力：

- 二维码登录流程
- 认证恢复流程
- 文件工具
- 缓存或目录清理
- 内部管理动作

### 3.6 Internal API 使用约束

- Internal API 仅允许 WebUI 或项目内部模块使用
- 插件、第三方调用方、对外接入文档不得把 Internal API 当作正式能力推荐
- Internal API 不承诺兼容性
- 若某项 Internal API 被证明具有稳定产品价值，必须先完成正式设计，再迁入 v1
- 不得直接把现有 Internal API 路径视为未来 Public API

---

## 4. 通用协议要求

### 4.1 命名空间

正式 v1 接口必须以 `/api/v1` 为前缀。

### 4.2 JSON 协议

除查询参数与 SSE 流响应外，正式接口请求体与响应体均使用 JSON。

`GET /api/v1/player/stream` 的响应遵守 SSE 协议（`text/event-stream`），不使用 JSON envelope，详见第 12.25 节与 SSE 规范。

### 4.3 统一 Envelope

所有 v1 白名单接口的非 SSE 响应均必须使用统一 envelope：

```json
{
  "code": 0,
  "message": "ok",
  "data": {},
  "request_id": "req_xxx"
}
```

字段约束：

- `code: integer`
- `message: string`
- `data: object`
- `request_id: string`

约束说明：

- `code = 0` 表示成功
- `code != 0` 表示失败
- `request_id` 是 envelope 顶层字段，所有白名单接口必须返回
- 顶层不得用 `ret / success / status` 替代 `code/message`

`GET /api/v1/player/stream` 在建立 SSE 连接后，其事件数据不通过统一 envelope 传输；仅在返回错误 HTTP 状态码（400、401、404、503）时，使用统一 envelope 格式。

### 4.4 通用请求约束

凡是对具体设备生效的接口，请求中必须包含：

```json
{ "device_id": "<device_id>" }
```

字段约束：

- `device_id: string`
- `device_id` 必须是非空字符串

### 4.5 中文命令字符串禁入

以下输入形式不得进入 v1 正式接口：

- `上一首`
- `下一首`
- `30分钟后关机`
- `播放歌单日语`
- `播放列表第三个日语`
- 任意 `cmd` 文本

---

## 5. 正式白名单接口

本版本正式白名单接口共 25 个：

### 5.1 播放与解析

1. `POST /api/v1/play`
2. `POST /api/v1/resolve`

### 5.2 控制

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

### 5.3 音乐库

13. `POST /api/v1/library/favorites/add`
14. `POST /api/v1/library/favorites/remove`
15. `POST /api/v1/library/refresh`
16. `GET /api/v1/library/playlists`
17. `GET /api/v1/library/music-info`

### 5.4 查询

18. `GET /api/v1/devices`
19. `GET /api/v1/system/status`
20. `GET /api/v1/system/settings`
21. `POST /api/v1/system/settings`
22. `POST /api/v1/system/settings/item`
23. `GET /api/v1/search/online`
24. `GET /api/v1/player/state`
25. `GET /api/v1/player/stream`

---

## 6. 接口分级与归属路径

### 6.1 分级定义

v1 白名单接口按契约分为 Class A / B / C 三类。

该分级不是文档标签，而是正式约束；每个接口的成功响应契约、错误契约与内部归属路径均由分级决定。

### 6.2 Class A：设备动作型接口

判定标准：

- 直接涉及设备侧动作执行
- 存在 transport 语义
- 成功响应必须可观测 `transport`
- 必须进入统一调度/分发链路

Class A 接口：

- `POST /api/v1/play`
- `POST /api/v1/control/stop`
- `POST /api/v1/control/previous`
- `POST /api/v1/control/next`
- `POST /api/v1/control/pause`
- `POST /api/v1/control/resume`
- `POST /api/v1/control/tts`
- `POST /api/v1/control/volume`
- `POST /api/v1/control/probe`

契约要求的内部归属：

- 必须进入 Runtime 的统一调度/分发链路
- 必须以 transport 作为设备动作观测结果的一部分

重要约束：Class A 接口的成功响应不承诺命令执行后的播放器最终状态。`transport` 字段表示"动作已进入链路并选定传输方式"，不表示播放器已到达特定 `transport_state`。调用方必须通过 `GET /api/v1/player/state` 或 SSE 流获取命令执行后的播放器权威状态。

### 6.3 Class B：本地状态 / 歌单 / 收藏 / 控制型接口

判定标准：

- 不以 transport 可观测性为契约核心
- 允许保留在 router / runtime 侧实现
- 必须遵守统一 envelope 与统一错误模型
- 不得伪装成 Class A 的返回结构

Class B 接口：

- `POST /api/v1/control/play-mode`
- `POST /api/v1/control/shutdown-timer`
- `POST /api/v1/library/favorites/add`
- `POST /api/v1/library/favorites/remove`
- `POST /api/v1/library/refresh`
- `POST /api/v1/system/settings`
- `POST /api/v1/system/settings/item`

契约要求的内部归属：

- 可以由 router / runtime 本地控制路径承载
- 不要求暴露 transport 作为成功契约字段
- 仍必须输出结构化成功结果与结构化错误结果

### 6.4 Class C：查询型接口

判定标准：

- 只读查询或状态聚合
- 不要求 transport
- 必须有统一 envelope 与可判别错误语义

Class C 接口：

- `GET /api/v1/library/playlists`
- `GET /api/v1/library/music-info`
- `GET /api/v1/devices`
- `GET /api/v1/system/status`
- `GET /api/v1/system/settings`
- `GET /api/v1/search/online`
- `GET /api/v1/player/state`
- `GET /api/v1/player/stream`
- `POST /api/v1/resolve`

契约要求的内部归属：

- 必须以只读查询 / 聚合路径提供结果
- 不得将 transport 作为该类接口的成功契约要求
- `GET /api/v1/player/state` 与 `GET /api/v1/player/stream` 必须共享同一个状态快照构建器（见投影规范第 11 节）

---

## 7. 成功响应契约矩阵

### 7.1 通用成功 Envelope

所有成功响应（SSE 流除外）必须满足：

- 顶层 `code = 0`
- 顶层 `message` 必须存在
- 顶层 `data` 必须存在
- 顶层 `request_id` 必须存在

### 7.2 Class A 成功响应

Class A 成功响应必须包含：

- 顶层 `code`
- 顶层 `message`
- 顶层 `request_id`
- `data.status`
- `data.transport`
- `data.device_id`

Class A 成功响应按接口需要可以包含：

- `data.source_plugin`
- `data.resolved_title`
- `data.extra`

调用方可以依赖的最小事实：

- 动作已进入设备动作链路
- transport 已被选定并回传
- 不承诺命令执行后的播放器 `transport_state`

### 7.3 Class B 成功响应

Class B 成功响应必须包含：

- 顶层 `code`
- 顶层 `message`
- 顶层 `request_id`
- `data.status`
- `data.device_id`（若该接口天然作用于设备，则必须存在）

Class B 成功响应不得要求调用方假设存在：

- `data.transport`
- `data.source_plugin`

### 7.4 Class C 成功响应

Class C 成功响应必须包含：

- 顶层 `code`
- 顶层 `message`
- 顶层 `request_id`
- `data` 中与查询结果直接相关的字段

Class C 不承诺下列字段：

- `data.transport`
- `data.source_plugin`（除非该查询接口的字段定义中显式列出）

`GET /api/v1/player/state` 的 `data` 字段必须完全符合投影规范第 5 节所定义的状态快照模型，详见第 12.24 节。

### 7.5 成功字段禁止假设规则

调用方不得做以下假设：

- 因为某次实现返回了 `source_plugin`，就假设该字段属于所有控制接口正式契约
- 因为某个内部对象存在更多字段，就假设这些字段属于 v1 正式响应
- 因为命令接口返回了 `transport`，就假设可以从命令响应推断播放器当前 `transport_state`

---

## 8. 统一错误模型

### 8.1 错误响应基础结构

所有 v1 白名单接口的失败响应（包括 `GET /api/v1/player/stream` 建立失败时的 HTTP 错误响应）必须满足：

- 顶层 `code != 0`
- 顶层 `message` 必须存在
- 顶层 `request_id` 必须存在
- `data.error_code` 必须存在
- `data.stage` 必须存在

错误响应示例：

```json
{
  "code": 50001,
  "message": "invalid request",
  "data": {
    "error_code": "E_INVALID_REQUEST",
    "stage": "request"
  },
  "request_id": "req_xxx"
}
```

### 8.2 `stage` 合法枚举

`stage` 必须属于以下有限集合，不允许自由扩张或随意漂移：

- `request`
- `resolve`
- `prepare`
- `dispatch`
- `xiaomi`
- `library`
- `system`
- `auth`

### 8.3 错误阶段语义

- `request`：请求参数、字段约束、查询参数等边界错误
- `resolve`：来源识别、媒体解析、歌单索引定位等失败
- `prepare`：播放前资源准备失败
- `dispatch`：动作下发、统一调度或 transport 分发失败
- `xiaomi`：设备平台调用失败
- `library`：本地库、歌单、收藏、索引刷新相关失败
- `system`：系统状态、运行时状态、非业务依赖失败
- `auth`：鉴权、认证恢复、会话状态相关失败

### 8.4 各分类接口的错误要求

Class A 错误要求：

- 必须提供可判别的 `error_code`
- 必须提供合法 `stage`
- 不得把 transport / dispatch / xiaomi 失败压扁成无阶段的通用错误

Class B 错误要求：

- 即使不走统一 transport 主链路，也必须提供结构化错误
- 不得只返回模糊消息而缺少 `error_code/stage`

Class C 错误要求：

- 即使是查询接口，也必须提供结构化错误
- 查询失败不得退化为只有 envelope 顶层文案的错误
- `GET /api/v1/player/stream` 在 HTTP 层面建立失败时，错误响应必须遵守统一 envelope 与结构化错误模型

### 8.5 SSE stream_error 事件与 HTTP 错误的区分

`GET /api/v1/player/stream` 在连接建立后可能推送 `stream_error` 类型的 SSE 事件，用于通知客户端需要关闭连接的运行时错误（见 SSE 规范第 10.3 节）。`stream_error` 事件不是 HTTP 错误响应，不遵守统一 envelope 格式，其格式由 SSE 规范定义。两者的适用场景：

- HTTP 错误（400/401/404/503）：连接建立阶段失败，返回统一 envelope JSON，不产生 SSE 流。
- `stream_error` SSE 事件：连接建立后的运行时错误，在 SSE 流中推送，不是 HTTP 错误响应。

### 8.6 禁止项

禁止以下做法：

- 把可识别业务异常退化为无阶段的通用内部错误
- 要求前端依赖 traceback 文本判断错误类型
- 以临时调试字段代替 `error_code`
- 以随机字符串、模块名或异常类名代替正式 `stage`

---

## 9. 内部归属约束

### 9.1 Class A 归属约束

Class A 接口必须进入统一调度 / 分发链路。

若 Class A 接口未进入统一调度 / 分发链路，应判定为实现不符合规范。

### 9.2 Class B 归属约束

Class B 接口可以在 router / runtime 侧实现，但必须保持其自身成功返回模型。

若 Class B 接口伪装为 Class A 返回模型，应判定为实现不符合规范。

### 9.3 Class C 归属约束

Class C 接口必须提供统一 envelope 与结构化错误。

`GET /api/v1/player/state` 与 `GET /api/v1/player/stream` 必须共享同一个状态快照构建器（见投影规范第 11 节）。若两个接口基于不同逻辑独立拼装状态，应判定为实现不符合规范。

---

## 10. 播放状态获取策略

本章定义前端获取播放器权威状态的规范策略。此策略是 v1 契约的组成部分，消费方必须遵守。

### 10.1 双通道定位

v1 通过两个接口暴露播放器权威状态：

| 接口 | 定位 | 适用场景 |
|---|---|---|
| `GET /api/v1/player/stream` | **主通道** | SSE 连接正常时的所有状态接收 |
| `GET /api/v1/player/state` | **初始化 + 降级回退** | 页面首次加载的初始快照、SSE 断线期间的轮询兜底、调试与状态核验 |

### 10.2 前端获取策略规则

前端必须遵守以下策略：

1. 优先 SSE：在 SSE 连接正常运行期间，前端必须以 SSE 事件作为唯一状态来源，不得同时对 `/player/state` 发起轮询。
2. 初始化顺序：页面加载时，应先通过 `GET /api/v1/player/state` 获取初始快照渲染 UI，再建立 SSE 连接。SSE 连接建立并收到初始事件后，停止任何轮询，切换到 SSE 主通道。
3. 降级回退：SSE 连接断开且重连失败时，前端应切换为对 `GET /api/v1/player/state` 的轮询（间隔不低于 3 秒）。一旦 SSE 重连成功，必须立即停止轮询。
4. 禁止并行竞争：不允许将 SSE 事件与 HTTP 轮询结果并行写入 UI，不允许两路来源互相覆盖。

### 10.3 revision 去重要求

无论通过哪个通道收到状态快照，前端必须基于 `revision` 字段进行去重：

- `revision` 严格小于当前已渲染 revision 的快照，必须丢弃，不得写回 UI。
- `revision` 等于当前已渲染 revision 的快照，视为重复，应丢弃，不触发 UI 重渲染。

详细规则见投影规范第 6.3 节。

---

## 11. 接口归属总表

| 接口 | 分类 | 契约要求的内部归属 | 成功响应关键字段 | 错误模型要求 | 备注 |
|---|---|---|---|---|---|
| `POST /api/v1/play` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport`，可含 `data.source_plugin`, `data.extra` | 必须有 `error_code`, `stage`；设备动作失败不得退化为模糊内部错误 | 唯一正式播放入口；承诺 `transport`，不承诺命令后 `transport_state` |
| `POST /api/v1/control/stop` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 承诺 `transport`，不承诺命令后 `transport_state` |
| `POST /api/v1/control/pause` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 承诺 `transport`，不承诺命令后 `transport_state` |
| `POST /api/v1/control/resume` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 承诺 `transport`，不承诺命令后 `transport_state` |
| `POST /api/v1/control/tts` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 承诺 `transport`，不承诺命令后 `transport_state` |
| `POST /api/v1/control/volume` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 承诺 `transport`，不承诺命令后 `transport_state` |
| `POST /api/v1/control/probe` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 承诺 `transport`，不承诺命令后 `transport_state` |
| `POST /api/v1/control/previous` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 承诺 `transport`，不承诺命令后 `transport_state` |
| `POST /api/v1/control/next` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 承诺 `transport`，不承诺命令后 `transport_state` |
| `POST /api/v1/control/play-mode` | B | router / runtime 本地控制路径 | `data.status`, `data.device_id`, `request_id` | 必须有 `error_code`, `stage` | 不得要求 `transport` |
| `POST /api/v1/control/shutdown-timer` | B | router / runtime 本地控制路径 | `data.status`, `data.device_id`, `request_id` | 必须有 `error_code`, `stage` | 不得要求 `transport` |
| `POST /api/v1/library/favorites/add` | B | library 本地控制路径 | `data.status`, `data.device_id`, `data.track_name` | 必须有 `error_code`, `stage` | 不得要求 `transport` |
| `POST /api/v1/library/favorites/remove` | B | library 本地控制路径 | `data.status`, `data.device_id`, `data.track_name` | 必须有 `error_code`, `stage` | 不得要求 `transport` |
| `POST /api/v1/library/refresh` | B | library 本地控制路径 | `data.status`，可含范围信息 | 必须有 `error_code`, `stage` | 不得要求 `transport` |
| `POST /api/v1/system/settings` | B | system 设置路径 | `data.status`, `data.saved` | 必须有 `error_code`, `stage` | 不得要求 `transport` |
| `POST /api/v1/system/settings/item` | B | system 设置路径 | `data.status`, `data.updated`, `data.key` | 必须有 `error_code`, `stage` | 不得要求 `transport` |
| `GET /api/v1/library/playlists` | C | library 查询路径 | `data.playlists` | 必须有 `error_code`, `stage` | 不得要求 `transport` |
| `GET /api/v1/library/music-info` | C | library 查询路径 | `data.name`, `data.url`, `data.duration_seconds` | 必须有 `error_code`, `stage` | 不得要求 `transport` |
| `POST /api/v1/resolve` | C | 只读解析 / 聚合路径 | 以解析结果字段为主 | 必须有 `error_code`, `stage` | 查询型；不要求 `transport` |
| `GET /api/v1/devices` | C | 只读查询 / 聚合路径 | `data.devices` | 必须有 `error_code`, `stage` | 不得要求 `transport` |
| `GET /api/v1/system/status` | C | 只读查询 / 聚合路径 | `data.status`, `data.version`, `data.devices_count` | 必须有 `error_code`, `stage` | 不得要求 `transport` |
| `GET /api/v1/system/settings` | C | system 设置查询路径 | `data.settings`, `data.device_ids`, `data.devices` | 必须有 `error_code`, `stage` | 不得要求 `transport` |
| `GET /api/v1/search/online` | C | 搜索查询路径 | `data.items`, `data.total` | 必须有 `error_code`, `stage` | 不得要求 `transport` |
| `GET /api/v1/player/state` | C | 只读状态聚合路径（与 stream 共享快照构建器） | 完整状态快照（见第 12.24 节） | 必须有 `error_code`, `stage` | 初始化 + 降级 fallback 通道 |
| `GET /api/v1/player/stream` | C | 只读状态推送路径（与 state 共享快照构建器） | SSE 事件流（见第 12.25 节） | HTTP 建立失败时必须有 `error_code`, `stage` | **主通道**；SSE 协议 |

---

## 12. 接口逐项契约

### 12.1 `POST /api/v1/play`

用途：播放一个明确媒体目标。

定位：唯一正式播放入口。

请求体：

```json
{
  "device_id": "<device_id>",
  "query": "https://example.com/song.mp3",
  "source_hint": "auto",
  "options": {}
}
```

字段约束：

- `device_id: string`，必填，非空
- `query: string`，必填，非空
- `source_hint: string`，可选，默认 `auto`
- `options: object`，可选

`source_hint` 允许值：

- `auto`
- `direct_url`
- `site_media`
- `jellyfin`
- `local_library`

成功响应最小契约：

- 顶层 envelope
- `data.status`
- `data.device_id`
- `data.transport`

按接口需要可包含：

- `data.source_plugin`
- `data.resolved_title`
- `data.extra`

约束：成功响应中的 `transport` 表示动作已进入链路，不表示播放器已到达 `playing` 状态。调用方必须通过 `GET /api/v1/player/state` 或 SSE 流确认播放状态。

### 12.2 `POST /api/v1/resolve`

用途：只解析，不播放。

请求体：

```json
{
  "query": "https://example.com/video",
  "source_hint": "auto",
  "options": {}
}
```

成功响应以解析结果字段为主，不承诺 `transport`。

### 12.3 `POST /api/v1/control/stop`

用途：停止当前播放。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

成功响应必须包含 `data.transport`。成功响应不承诺播放器已达到 `stopped` 状态，调用方须通过状态通道确认。

### 12.4 `POST /api/v1/control/pause`

用途：暂停当前播放。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

成功响应必须包含 `data.transport`。成功响应不承诺播放器已达到 `paused` 状态，调用方须通过状态通道确认。

### 12.5 `POST /api/v1/control/resume`

用途：恢复当前播放。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

成功响应必须包含 `data.transport`。成功响应不承诺播放器已达到 `playing` 状态，调用方须通过状态通道确认。

### 12.6 `POST /api/v1/control/tts`

用途：播放一段 TTS 文本。

请求体：

```json
{
  "device_id": "<device_id>",
  "text": "你好，欢迎使用 XiaoMusic"
}
```

字段约束：

- `text: string`，必填，非空

成功响应必须包含 `data.transport`。

### 12.7 `POST /api/v1/control/volume`

用途：设置设备音量。

请求体：

```json
{
  "device_id": "<device_id>",
  "volume": 35
}
```

字段约束：

- `volume: integer`
- `0 <= volume <= 100`

成功响应必须包含 `data.transport`。

### 12.8 `POST /api/v1/control/probe`

用途：执行设备可用性探测。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

成功响应必须包含 `data.transport`。

### 12.9 `POST /api/v1/control/previous`

用途：切到上一首。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

成功响应必须包含 `data.transport`。成功响应不承诺切歌已完成，调用方须通过状态通道（SSE 中 `play_session_id` 变化且 `transport_state == "playing"`）确认切歌成功。

### 12.10 `POST /api/v1/control/next`

用途：切到下一首。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

成功响应必须包含 `data.transport`。成功响应不承诺切歌已完成，调用方须通过状态通道（SSE 中 `play_session_id` 变化且 `transport_state == "playing"`）确认切歌成功。

### 12.11 `POST /api/v1/control/play-mode`

用途：设置播放模式。

请求体：

```json
{
  "device_id": "<device_id>",
  "play_mode": "random"
}
```

允许值：

- `one`
- `all`
- `random`
- `single`
- `sequence`

该接口不承诺 `data.transport`。

### 12.12 `POST /api/v1/control/shutdown-timer`

用途：设置停止播放定时器。

请求体：

```json
{
  "device_id": "<device_id>",
  "minutes": 30
}
```

字段约束：

- `minutes: integer`
- `minutes >= 0`

该接口不承诺 `data.transport`。

### 12.13 `POST /api/v1/library/favorites/add`

用途：将歌曲加入收藏。

请求体：

```json
{
  "device_id": "<device_id>",
  "track_name": "夜に駆ける"
}
```

字段约束：

- `device_id: string`，必填，非空
- `track_name: string`，可选

该接口不承诺 `data.transport`。

### 12.14 `POST /api/v1/library/favorites/remove`

用途：将歌曲移出收藏。

请求体与字段约束与 `favorites/add` 保持一致。

该接口不承诺 `data.transport`。

### 12.15 `POST /api/v1/library/refresh`

用途：刷新音乐库或歌单索引。

请求体：

```json
{}
```

该接口不承诺 `data.transport`。

### 12.16 `GET /api/v1/library/playlists`

用途：获取播放上下文所需的歌单与歌曲列表。

成功响应关键字段：

- `data.playlists`
- `data.playlists.<playlist_name>[]`

查询错误应返回结构化 `error_code/stage`，推荐归类为 `library` 或 `request`。

### 12.17 `GET /api/v1/library/music-info`

用途：获取单曲的最小上下文信息。

查询参数：

- `name`，必填，非空

成功响应最小字段：

- `data.name: string`
- `data.url: string`
- `data.duration_seconds: number`

参数错误必须返回：

- `code = 40001`
- `data.error_code = E_INVALID_REQUEST`
- `data.stage = request`

### 12.18 `GET /api/v1/devices`

用途：获取设备列表。

成功响应关键字段：

- `data.devices[]`
- `data.devices[].device_id`
- `data.devices[].name`
- `data.devices[].model`
- `data.devices[].online`

### 12.19 `GET /api/v1/system/status`

用途：获取系统状态。

成功响应关键字段：

- `data.status`
- `data.version`
- `data.devices_count`

### 12.20 `GET /api/v1/system/settings`

用途：获取 WebUI 当前所需的最小系统设置与设备联动信息。

成功响应关键字段：

- `data.settings`
- `data.device_ids`
- `data.devices`

约束：

- `data.settings` 为设置对象
- `data.device_ids` 为当前已选择设备 DID 列表
- `data.devices` 为可供设置页勾选的设备列表

### 12.21 `POST /api/v1/system/settings`

用途：保存设置页当前配置。

请求体：

```json
{
  "settings": {},
  "device_ids": ["did-1"]
}
```

成功响应最小字段：

- `data.status`
- `data.saved`

### 12.22 `POST /api/v1/system/settings/item`

用途：更新单个设置项。

请求体：

```json
{
  "key": "enable_pull_ask",
  "value": true
}
```

成功响应最小字段：

- `data.status`
- `data.updated`
- `data.key`

参数错误必须返回：

- `code = 40001`
- `data.error_code = E_INVALID_REQUEST`
- `data.stage = request`

### 12.23 `GET /api/v1/search/online`

用途：执行在线搜索，返回 WebUI 当前所需的最小搜索结果集合。

查询参数：

- `keyword`，必填，非空
- `plugin`，可选，默认 `all`
- `page`，可选，默认 `1`
- `limit`，可选，默认 `20`

成功响应最小字段：

- `data.items[]`
- `data.items[].name`
- `data.items[].title`
- `data.items[].artist`
- `data.total`

参数错误必须返回：

- `code = 40001`
- `data.error_code = E_INVALID_REQUEST`
- `data.stage = request`

### 12.24 `GET /api/v1/player/state`

用途：获取当前设备的权威播放状态快照。

定位：初始化通道与降级回退通道。在 SSE 主通道（`GET /api/v1/player/stream`）正常运行期间，调用方不应通过本接口轮询获取状态更新；本接口适用于页面首次加载的初始快照获取、SSE 断线期间的轮询兜底以及调试核验目的。

查询参数：

- `device_id`，必填，非空

成功响应结构：

`data` 字段必须是完整的权威播放状态快照，完全符合投影规范第 5 节所定义的状态快照模型。`data` 必须包含以下字段（字段语义以投影规范为准，本文档不重复定义）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `device_id` | `string` | 当前状态所属设备 ID |
| `revision` | `integer` | 状态版本号，单设备维度单调递增 |
| `play_session_id` | `string` | 当前播放会话唯一标识 |
| `transport_state` | `string` | 播放传输状态枚举值（`idle` / `starting` / `switching` / `playing` / `paused` / `stopped` / `error`） |
| `track` | `object \| null` | 当前曲目信息，含 `track.id`（稳定身份标识）与 `track.title` |
| `context` | `object \| null` | 播放上下文信息，含 `context.id`、`context.name`、`context.current_index` |
| `position_ms` | `integer` | 当前播放位置（毫秒） |
| `duration_ms` | `integer` | 当前曲目总时长（毫秒），未知时为 `0` |
| `snapshot_at_ms` | `integer` | 快照生成时的服务端时间戳（毫秒 Unix 时间戳） |

响应示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "device_id": "981257654",
    "revision": 42,
    "play_session_id": "sess_a1b2c3d4",
    "transport_state": "playing",
    "track": {
      "id": "a1b2c3d4e5f6g7h8",
      "title": "希望を求めて",
      "artist": "Unknown",
      "source": "local_library"
    },
    "context": {
      "id": "OTS",
      "name": "OTS",
      "current_index": 5
    },
    "position_ms": 32000,
    "duration_ms": 218000,
    "snapshot_at_ms": 1711084800000
  },
  "request_id": "abc123def456"
}
```

约束：

- 本接口必须与 `GET /api/v1/player/stream` 共享同一个状态快照构建器。
- 本接口不承诺 `transport`（控制命令结果字段），不得将 `transport` 混入状态快照。
- 查询失败必须返回结构化错误，`stage` 应为 `system` 或 `request`。
- 兼容字段说明见第 13 章。

### 12.25 `GET /api/v1/player/stream`

用途：通过 SSE（Server-Sent Events）持续推送当前设备的播放状态事件流。

定位：播放状态主通道。在 SSE 连接正常运行期间，前端必须以本接口的事件作为唯一播放状态来源，停止对 `GET /api/v1/player/state` 的轮询。

完整协议由 SSE 规范（`docs/spec/player_stream_sse_spec.md`）定义。本节提供契约级摘要。

查询参数：

- `device_id`，必填，非空。一个连接只订阅一个设备，不允许省略或通过通配符订阅多个设备。

响应协议：

- HTTP 200 成功建立连接后，响应为 `Content-Type: text/event-stream` 的长连接 SSE 流，不使用统一 envelope。
- HTTP 400 / 401 / 404 / 503 建立失败时，响应为统一 envelope JSON（见第 8 章）。

事件格式：

连接建立后，服务端必须立即推送一条完整的当前状态快照事件，后续在播放状态发生变化时持续推送：

```
event: player_state
id: <revision>
data: <完整状态快照 JSON>

```

- `event` 固定为 `player_state`
- `id` 固定为该事件所携带快照的 `revision` 值（字符串形式）
- `data` 必须是完整的权威播放状态快照，字段模型与 `GET /api/v1/player/state` 的 `data` 完全一致（见第 12.24 节）

服务端应定期发送心跳注释（`: heartbeat`），频率建议 15 秒，不得超过 30 秒。

主要约束（完整约束见 SSE 规范）：

- 连接建立后必须立即推送完整初始快照。
- 重连后必须推送完整最新快照，不做增量补发。
- 进度自然推进不触发新事件。
- 服务端不得因播放器无活动而主动关闭连接。
- 本接口必须与 `GET /api/v1/player/state` 共享同一个状态快照构建器。

---

## 13. 兼容字段说明

本章说明在投影规范建立前使用的旧状态字段的降级定位。

### 13.1 兼容字段清单

以下字段属于遗留兼容字段，其正式语义已由投影规范中的新字段取代：

| 旧兼容字段 | 已被取代的正式字段 | 语义映射 |
|---|---|---|
| `cur_music` | `track.title` | 对应当前曲目展示标题 |
| `is_playing` | `transport_state == "playing"` | 对应播放传输状态的布尔简化 |
| `offset` | `position_ms / 1000` | 对应播放位置，旧字段单位为秒 |
| `duration` | `duration_ms / 1000` | 对应曲目时长，旧字段单位为秒 |
| `current_track_id` | `track.id` | 对应曲目稳定身份标识 |
| `current_index` | `context.current_index` | 对应曲目在上下文中的位置索引 |
| `context_type` | 已合并到 `context` 对象 | 上下文类型，不再作为独立顶层字段 |
| `context_id` | `context.id` | 对应播放上下文唯一标识 |
| `context_name` | `context.name` | 对应播放上下文展示名称 |

### 13.2 兼容字段约束

- 兼容字段如在当前实现中继续出现于 `GET /api/v1/player/state` 的响应中，仅视为向后兼容的投影输出，不属于本文档的正式契约字段。
- 新前端代码、新插件、新 Home Assistant 集成实现，不得依赖上述兼容字段作为播放状态的主依据。
- 新实现必须依赖投影规范定义的正式字段（`transport_state`、`track.id`、`play_session_id`、`revision`、`position_ms`、`duration_ms` 等）。
- 上述兼容字段在未来版本中可能被删除，删除时不视为破坏性变更，不单独发版通知。

---

## 14. 请求扩展对象

### 14.1 `context_hint`

`context_hint` 是可选对象，用于指导上下文选择：

```json
{
  "context_type": "...",
  "context_id": "...",
  "context_name": "..."
}
```

约束：

- 若提供，适配层必须优先使用
- 若未提供，由实现自行选择上下文

---

## 15. 统一播放入口收敛原则

1. `POST /api/v1/play` 是唯一正式播放入口。
2. 所有正式播放请求必须通过 `POST /api/v1/play` 进入统一播放执行路径。
3. `/api/v1/playlist/play` 与 `/api/v1/playlist/play-index` 已从正式白名单实现中删除。
4. 新前端功能不得新增对 `/api/v1/playlist/*` 的依赖。
5. 新插件能力与新来源扩展必须通过统一播放入口接入。
6. 不再对 `/api/v1/playlist/*` 做长期接口能力扩展承诺。
