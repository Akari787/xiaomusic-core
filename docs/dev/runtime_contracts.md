# Runtime 合同说明（v1.1.0 Phase 1）

> 本文档描述当前代码已落地的运行时边界合同。  
> 正式外部 API 仍以 `docs/api/api_v1_spec.md` 为准。

## 1. PlayOptions

代码位置：`xiaomusic/core/models/media.py` (`PlayOptions`)

### 1.1 字段与默认值

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `resolve_timeout_seconds` | `float \| None` | `None` | 若为空，按来源自动回退（`site_media=15`，其他=8） |
| `no_cache` | `bool` | `false` | 禁用来源缓存 |
| `prefer_proxy` | `bool` | `false` | 优先代理投递 |
| `confirm_start` | `bool` | `true` | 播放确认开关 |
| `confirm_start_delay_ms` | `int` | `1200` | 首次确认延迟 |
| `confirm_start_retries` | `int` | `2` | 确认重试次数 |
| `confirm_start_interval_ms` | `int` | `600` | 重试间隔 |
| `source_payload` | `dict \| None` | `None` | 来源插件扩展上下文 |
| `media_id` | `str` | `""` | 兼容 `media_id/id` |
| `title` | `str` | `""` | 兼容标题透传 |

### 1.2 输入来源

- v1 请求体 `options`（`/api/v1/play`、`/api/v1/resolve`）
- `PlaybackFacade.play/resolve` 内部统一执行 `PlayOptions.from_payload(...)`

### 1.3 与旧 payload 的兼容关系

- `id` 兼容映射到 `media_id`
- 字符串布尔值（如 `"true"`, `"1"`）会被规范化
- 不合法数字会回退默认值或最小值

兼容边界：

- 兼容逻辑只保留在边界层（`from_payload`），不向 core 编排层扩散。

---

## 2. facade.status() 契约

代码位置：`xiaomusic/playback/facade.py`

### 2.1 新契约

- 方法签名：`status(device_id: str)`
- 参数为空时抛 `InvalidRequestError("device_id is required")`

### 2.2 兼容边界

- 旧 router（如 `/getplayerstatus`）在路由层把 `did` 适配为 `device_id` 后再调用 facade
- facade 不再接收 `target: dict` 形态

### 2.3 正式调用方建议

- 正式调用方不应使用该 legacy 状态包装结构
- 对外统一状态查询使用 `GET /api/v1/player/state`

---

## 3. payload 键名与映射层

代码位置：`xiaomusic/core/models/payload_keys.py`

### 3.1 正式字段

- 请求主字段：`device_id`、`query`、`source_hint`、`options`、`request_id`
- options 核心字段：`resolve_timeout_seconds`、`no_cache`、`prefer_proxy` 等

### 3.2 旧字段兼容映射

- `speaker_id`：仅旧 router/facade bridge 使用
- `id`：兼容为 `media_id`
- `source_payload` 里的历史键（如 `url`、`music_name`）仅用于来源适配

### 3.3 映射放置层

- `payload -> PlayOptions`：`PlayOptions.from_payload(...)`
- `PlayOptions -> MediaRequest.context`：`PlayOptions.to_context(...)`
- `MediaRequest` 组装：`MediaRequest.from_payload(...)`

原则：

- 主链路使用统一模型；兼容转换只在边界做一次。

---

## 4. 错误模型最小映射

代码位置：`xiaomusic/api/routers/v1.py`、`xiaomusic/core/errors/*`

| 类别 | 典型异常 | v1 code | stage | 说明 |
|---|---|---|---|---|
| 参数错误 | `InvalidRequestError` | `50001` | - | 请求字段非法或缺失 |
| 来源解析失败 | `SourceResolveError` | `20002` | `resolve` | 来源插件无法解析 query |
| 运行时准备失败 | `DeliveryPrepareError` | `30001` | `prepare` | 流准备失败（过期/不可投递） |
| 传输/投递失败 | `TransportError` | `40002` | `dispatch` | 设备下发失败 |
| 设备不存在 | `DeviceNotFoundError` | `40004` | `xiaomi` | 目标设备不存在/不可用 |
| 内部错误 | 其他异常 | `10000` | `null` | 未分类内部异常 |

deprecated 入口提示：

- 旧 router wrapper 当前保留，但日志会输出 `deprecated_endpoint ... replacement=...`。
