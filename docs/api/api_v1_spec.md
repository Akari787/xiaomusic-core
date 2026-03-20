# XiaoMusic Runtime API v1 规范

版本：v1.2
状态：正式契约
最后更新：2026-03-20
适用范围：XiaoMusic Runtime HTTP API（WebUI、Home Assistant 与第三方调用）

---

## 1. 总则与契约优先级

### 1.1 唯一权威来源

本文档是 XiaoMusic Runtime v1 API 的唯一权威契约来源。

本文档定义并约束以下内容：

- 对外 HTTP 接口行为
- 请求字段与查询参数
- 成功响应结构
- 错误响应结构
- v1 白名单接口的内部归属路径

### 1.2 冲突裁决规则

当以下内容与本文档冲突时，一律以本文档为准：

- 当前实现行为
- 历史文档
- 前端假设
- 手工验收口径
- 临时调试结论

后续所有代码修复、前端修复、测试修复与验收结论，均以本文档为基准。

### 1.3 显式列出原则

只有本文档中显式列出的行为，才属于 v1 正式承诺。

下列内容若未在本文档中被明确定义，则不属于 v1 契约：

- 额外顶层字段
- 未声明的 `data` 字段
- 未声明的错误阶段值
- 未声明的内部归属路径
- 未声明的字段同构关系

### 1.4 规范要求与实现现状

本文档中的“必须 / 不得 / 应 / 可以”均为规范要求。

若当前实现与规范要求不一致，应判定为“实现不符合规范”，而不是修改规范去描述偏差实现。

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

### 2.3 统一播放入口原则

`POST /api/v1/play` 是 v1 的唯一正式播放入口。

所有正式播放请求必须通过 `POST /api/v1/play` 进入统一播放执行路径。

`POST /api/v1/playlist/play` 与 `POST /api/v1/playlist/play-index` 已从正式实现中删除。

约束：

- 新前端功能不得新增对 `/api/v1/playlist/*` 的依赖
- 新插件能力与新来源扩展必须通过 `/api/v1/play` 接入
- 不再对 `/api/v1/playlist/*` 做长期播放能力扩展承诺

### 2.4 非 v1 范围

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
- 仅第 4 章白名单中的接口属于 Public API
- 仅 Public API 对 WebUI、Home Assistant、插件与第三方调用方提供兼容性与长期稳定性承诺
- 任何未列入第 4 章白名单的接口都不属于 Public API

Public API 面向：

- WebUI
- Home Assistant
- 插件
- 第三方自动化调用方

### 3.2 Internal API

Internal API 是非 `/api/v1/*` 的内部前后端通信接口层。

定义：

- Internal API 不是“暂时未迁移完成的 v1 残留”
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

除查询参数外，正式接口请求体与响应体均使用 JSON。

### 4.3 统一 Envelope

所有 v1 白名单接口的响应均必须使用统一 envelope：

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

本版本正式白名单接口共 24 个：

### 4.1 播放与解析

1. `POST /api/v1/play`
2. `POST /api/v1/resolve`

### 4.2 控制

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

### 4.3 音乐库

13. `POST /api/v1/library/favorites/add`
14. `POST /api/v1/library/favorites/remove`
15. `POST /api/v1/library/refresh`
16. `GET /api/v1/library/playlists`
17. `GET /api/v1/library/music-info`

### 4.4 查询

18. `GET /api/v1/devices`
19. `GET /api/v1/system/status`
20. `GET /api/v1/system/settings`
21. `POST /api/v1/system/settings`
22. `POST /api/v1/system/settings/item`
23. `GET /api/v1/search/online`
24. `GET /api/v1/player/state`

---

## 6. 接口分级与归属路径

### 5.1 分级定义

v1 白名单接口按契约分为 Class A / B / C 三类。

该分级不是文档标签，而是正式约束；每个接口的成功响应契约、错误契约与内部归属路径均由分级决定。

### 5.2 Class A：设备动作型接口

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

### 5.3 Class B：本地状态 / 歌单 / 收藏 / 控制型接口

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

### 5.4 Class C：查询型接口

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
- `POST /api/v1/resolve`

契约要求的内部归属：

- 必须以只读查询 / 聚合路径提供结果
- 不得将 transport 作为该类接口的成功契约要求

---

## 7. 成功响应契约矩阵

### 6.1 通用成功 Envelope

所有成功响应必须满足：

- 顶层 `code = 0`
- 顶层 `message` 必须存在
- 顶层 `data` 必须存在
- 顶层 `request_id` 必须存在

### 6.2 Class A 成功响应

Class A 成功响应必须包含：

- 顶层 `code`
- 顶层 `message`
- 顶层 `request_id`
- `data.status`
- `data.transport`
- `data.device_id`（对设备动作型接口适用时必须存在；本类接口均适用）

Class A 成功响应按接口需要可以包含：

- `data.source_plugin`
- `data.resolved_title`
- `data.extra`

调用方可以依赖的最小事实：

- 动作已进入设备动作链路
- transport 已被选定并回传

### 6.3 Class B 成功响应

Class B 成功响应必须包含：

- 顶层 `code`
- 顶层 `message`
- 顶层 `request_id`
- `data.status`
- `data.device_id`（若该接口天然作用于设备，则必须存在）

Class B 成功响应不得要求调用方假设存在：

- `data.transport`
- `data.source_plugin`

特别约束：

- `POST /api/v1/play` 是唯一正式播放入口
- `POST /api/v1/library/favorites/add`
- `POST /api/v1/library/favorites/remove`
- `POST /api/v1/library/refresh`
- `POST /api/v1/control/play-mode`
- `POST /api/v1/control/shutdown-timer`
  均不要求 `transport/source_plugin`

### 6.4 Class C 成功响应

Class C 成功响应必须包含：

- 顶层 `code`
- 顶层 `message`
- 顶层 `request_id`
- `data` 中与查询结果直接相关的字段

Class C 不承诺下列字段：

- `data.transport`
- `data.source_plugin`（除非该查询接口的字段定义中显式列出）

### 6.5 成功字段禁止假设规则

调用方不得做以下假设：

- 因为某次实现返回了 `source_plugin`，就假设该字段属于所有控制接口正式契约
- 因为某个内部对象存在更多字段，就假设这些字段属于 v1 正式响应

---

## 8. 统一错误模型

### 7.1 错误响应基础结构

所有 v1 白名单接口的失败响应必须满足：

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

### 7.2 `stage` 合法枚举

`stage` 必须属于以下有限集合，不允许自由扩张或随意漂移：

- `request`
- `resolve`
- `prepare`
- `dispatch`
- `xiaomi`
- `library`
- `system`
- `auth`

### 7.3 错误阶段语义

- `request`：请求参数、字段约束、查询参数等边界错误
- `resolve`：来源识别、媒体解析、歌单索引定位等失败
- `prepare`：播放前资源准备失败
- `dispatch`：动作下发、统一调度或 transport 分发失败
- `xiaomi`：设备平台调用失败
- `library`：本地库、歌单、收藏、索引刷新相关失败
- `system`：系统状态、运行时状态、非业务依赖失败
- `auth`：鉴权、认证恢复、会话状态相关失败

### 7.4 各分类接口的错误要求

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

### 7.5 禁止项

禁止以下做法：

- 把可识别业务异常退化为无阶段的通用内部错误
- 要求前端依赖 traceback 文本判断错误类型
- 以临时调试字段代替 `error_code`
- 以随机字符串、模块名或异常类名代替正式 `stage`

---

## 9. 内部归属约束

### 8.1 Class A 归属约束

Class A 接口必须进入统一调度 / 分发链路。

若 Class A 接口未进入统一调度 / 分发链路，应判定为实现不符合规范。

### 8.2 Class B 归属约束

Class B 接口可以在 router / runtime 侧实现，但必须保持其自身成功返回模型。

若 Class B 接口伪装为 Class A 返回模型，应判定为实现不符合规范。

### 8.3 Class C 归属约束

Class C 接口必须提供统一 envelope 与结构化错误。

若 Class C 接口缺少统一 envelope 或结构化错误，应判定为实现不符合规范。

---

## 10. 接口归属总表

| 接口 | 分类 | 契约要求的内部归属 | 成功响应关键字段 | 错误模型要求 | 备注 |
|---|---|---|---|---|---|
| `POST /api/v1/play` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport`，可含 `data.source_plugin`, `data.extra` | 必须有 `error_code`, `stage`；设备动作失败不得退化为模糊内部错误 | 唯一正式播放入口；要求 `transport` |
| `POST /api/v1/control/stop` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 要求 `transport` |
| `POST /api/v1/control/pause` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 要求 `transport` |
| `POST /api/v1/control/resume` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 要求 `transport` |
| `POST /api/v1/control/tts` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 要求 `transport` |
| `POST /api/v1/control/volume` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 要求 `transport` |
| `POST /api/v1/control/probe` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 要求 `transport` |
| `POST /api/v1/control/previous` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 要求 `transport` |
| `POST /api/v1/control/next` | A | 统一调度 / 分发链路 | `data.status`, `data.device_id`, `data.transport` | 同 Class A | 要求 `transport` |
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
| `GET /api/v1/player/state` | C | 只读状态聚合路径 | `data.device_id`, `data.is_playing`, `data.cur_music`, `data.offset`, `data.duration` | 必须有 `error_code`, `stage` | 不得要求 `transport` |

---

## 11. 接口逐项契约

### 10.1 `POST /api/v1/play`

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

### 10.2 `POST /api/v1/resolve`

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

### 10.3 `POST /api/v1/control/stop`

用途：停止当前播放。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

成功响应必须包含 `data.transport`。

### 10.4 `POST /api/v1/control/pause`

用途：暂停当前播放。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

成功响应必须包含 `data.transport`。

### 10.5 `POST /api/v1/control/resume`

用途：恢复当前播放。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

成功响应必须包含 `data.transport`。

### 10.6 `POST /api/v1/control/tts`

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

### 10.7 `POST /api/v1/control/volume`

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

### 10.8 `POST /api/v1/control/probe`

用途：执行设备可用性探测。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

成功响应必须包含 `data.transport`。

### 10.9 `POST /api/v1/control/previous`

用途：切到上一首。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

成功响应必须包含 `data.transport`。

### 10.10 `POST /api/v1/control/next`

用途：切到下一首。

最小请求体：

```json
{ "device_id": "<device_id>" }
```

成功响应必须包含 `data.transport`。

### 10.11 `POST /api/v1/control/play-mode`

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

### 10.12 `POST /api/v1/control/shutdown-timer`

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

### 10.13 `POST /api/v1/library/favorites/add`

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

### 10.14 `POST /api/v1/library/favorites/remove`

用途：将歌曲移出收藏。

请求体与字段约束与 `favorites/add` 保持一致。

该接口不承诺 `data.transport`。

### 10.15 `POST /api/v1/library/refresh`

用途：刷新音乐库或歌单索引。

请求体：

```json
{}
```

该接口不承诺 `data.transport`。

### 10.16 `GET /api/v1/library/playlists`

用途：获取播放上下文所需的歌单与歌曲列表。

成功响应关键字段：

- `data.playlists`
- `data.playlists.<playlist_name>[]`

查询错误应返回结构化 `error_code/stage`，推荐归类为 `library` 或 `request`。

### 10.17 `GET /api/v1/library/music-info`

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

### 10.18 `GET /api/v1/devices`

用途：获取设备列表。

成功响应关键字段：

- `data.devices[]`
- `data.devices[].device_id`
- `data.devices[].name`
- `data.devices[].model`
- `data.devices[].online`

### 10.19 `GET /api/v1/system/status`

用途：获取系统状态。

成功响应关键字段：

- `data.status`
- `data.version`
- `data.devices_count`

### 10.20 `GET /api/v1/system/settings`

用途：获取 WebUI 当前所需的最小系统设置与设备联动信息。

成功响应关键字段：

- `data.settings`
- `data.device_ids`
- `data.devices`

约束：

- `data.settings` 为设置对象
- `data.device_ids` 为当前已选择设备 DID 列表
- `data.devices` 为可供设置页勾选的设备列表

### 10.21 `POST /api/v1/system/settings`

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

### 10.22 `POST /api/v1/system/settings/item`

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

### 10.23 `GET /api/v1/search/online`

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

### 10.24 `GET /api/v1/player/state`

用途：获取播放状态查询结果。

查询参数：

- `device_id`，必填

成功响应最小字段：

- `data.device_id: string`
- `data.is_playing: boolean`
- `data.cur_music: string`
- `data.offset: number`
- `data.duration: number`

约束：

- `offset / duration` 单位固定为秒
- 查询型接口不承诺 `transport`

---

## 12. 请求扩展对象

### 11.1 `context_hint`

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

## 13. 本次修改说明（供审阅）

1. 本次新增或重写了以下章节：
   - “总则与契约优先级”
   - “接口分级与归属路径”
   - “成功响应契约矩阵”
   - “统一错误模型”
   - “内部归属约束”
   - “接口归属总表”
2. 本次删除或修正了以下旧冲突表述：
   - 删除了旧文档中以阶段性落地口径描述正式接口的章节
   - 删除了把部分接口写成“目标契约”或“可暂缓收口”的表述
   - 删除了对未显式列出旧行为的默认承诺
3. 播放入口边界已明确写为：
   - `POST /api/v1/play` 是唯一正式播放入口
   - `/api/v1/playlist/*` 不再属于白名单正式接口
   - 新能力不得再以 `playlist/*` 作为播放入口基准
4. Class A / B / C 划分方式如下：
   - Class A：设备动作型，必须进入统一调度 / 分发链路，成功响应必须可观测 `transport`
   - Class B：本地状态 / 歌单 / 收藏 / 控制型，可保留在 router / runtime 路径，但必须遵守统一 envelope 与错误模型，且不得伪装为 Class A
   - Class C：查询型，必须提供统一 envelope 与结构化错误，不承诺 transport
5. 本步不涉及代码改动，因为本次任务目标是先完成 v1 API 契约收口，把对外行为、内部归属、成功响应与错误模型定义清楚；代码实现是否符合规范属于后续代码修复步骤。

## 14. 本次修正说明（供审阅）

1. 本次删除或降级了以下 `player/state` 相关字段的正式契约地位：
   - `current_track_title`
   - `current_track_id`
   - `current_track_duration`
   - `play_mode`
   - `context_type`
   - `context_id`
   - `context_name`
   - `queue_supported`
   - `current_index`
   - `queue_length`
   - `has_next`
   - `has_previous`
2. 这些字段不应在当前阶段写成正式契约，因为本轮文档收口只保留当前代码可稳定承诺的最小查询字段；上述字段属于状态聚合扩展信息，当前不应成为前端或外部调用方可依赖的稳定约束。
3. `GET /api/v1/player/state` 当前最终保留的最小正式字段为：
   - `data.device_id`
   - `data.is_playing`
   - `data.cur_music`
   - `data.offset`
   - `data.duration`
4. 以下大框架内容本次保留未动：
   - v1 唯一权威来源
   - Class A / B / C 接口分级
   - `/api/v1/play` 是唯一正式播放入口
   - Class A 必须要求 `transport`
   - Class B / C 不得假设 `transport`
   - 统一错误模型
   - 内部归属约束
   - 接口归属总表的大框架

## 15. 统一播放入口收敛原则

1. `POST /api/v1/play` 是唯一正式播放入口。
2. 所有正式播放请求必须通过 `POST /api/v1/play` 进入统一播放执行路径。
3. `/api/v1/playlist/play` 与 `/api/v1/playlist/play-index` 已从正式白名单实现中删除。
4. 新前端功能不得新增对 `/api/v1/playlist/*` 的依赖。
5. 新插件能力与新来源扩展必须通过统一播放入口接入。
6. 不再对 `/api/v1/playlist/*` 做长期接口能力扩展承诺。

## 16. 本次修改说明（供审阅）

1. 本次新增了“接口分层与边界”章节，并补齐了 Public API、Internal API、Forbidden / Removed 三层正式定义。
2. Public API 被定义为第 5 章 v1 白名单接口集合；Internal API 被定义为内部前后端通信、认证、文件、工具与管理辅助接口；Forbidden / Removed 被定义为已删除接口与明确禁止恢复的入口集合。
3. 当前被明确写入 Internal API 清单的接口包括：
   - 认证 / 会话接口：`/api/auth/status`、`/api/auth/refresh`、`/api/auth/logout`、`/api/get_qrcode`
   - 管理 / 文件 / 工具接口：`/api/file/fetch_playlist_json`、`/api/file/cleantempdir`、`/refreshmusictag`
4. 当前被明确写入 Forbidden / Removed 清单的内容包括：
   - `/api/v1/playlist/play`
   - `/api/v1/playlist/play-index`
   - 已删除的旧 device wrapper
   - 已删除的 `*_legacy` facade 方法
   - 中文命令入口、cmd 风格入口、自然语言控制入口、并行播放入口设计
5. 本次新增了“进入 v1 的准入标准”与“Internal API 使用约束”，明确只有具备长期复用价值、能遵守统一 envelope 与结构化错误模型、且表达产品能力的接口，才可以进入 v1。
6. 本步不涉及代码修改，因为本次任务目标是先把接口分层边界写成强约束文档，为后续接口去留与迁移判断提供统一依据。
- 已删除的 Internal API 工具入口
  - `POST /refreshmusictag`
