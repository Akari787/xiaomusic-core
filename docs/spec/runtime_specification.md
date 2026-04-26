# Runtime 核心层技术规范

> 版本：v1.1.1 Phase 1
> 最后更新：2026-03-19
> 适用范围：`xiaomusic/core/` 下所有模块

本文档定义 `xiaomusic/core/` 的数据模型、接口契约、错误体系与运行时行为约束。它是 core 层的技术规范，不是对外 API 文档（对外 API 见 `docs/api/api_v1_spec.md`）。

---

## 1. 核心数据模型

代码位置：`xiaomusic/core/models/media.py`

### 1.1 MediaRequest

播放请求的统一入参。进入 core 层之前，由 `PlaybackFacade` 或 `MediaRequest.from_payload()` 从 API payload 构造。

| 字段 | 类型 | 说明 |
|---|---|---|
| `device_id` | `str` | 目标设备 ID，非空 |
| `query` | `str` | 播放查询内容（URL、歌名等） |
| `source_hint` | `str \| None` | 来源类型提示，`None` 或 `"auto"` 时自动选择 |
| `options` | `PlayOptions` | 播放选项，见 1.2 节 |
| `context` | `dict` | 透传上下文（来源 payload 等），由 `PlayOptions.to_context()` 填充 |
| `request_id` | `str` | 请求追踪 ID（自动生成） |

构造规范：

- `MediaRequest.from_payload(payload)` 是统一构造入口，包含字段兼容映射。
- 不允许在 core 内部直接修改 `MediaRequest` 字段，它是不可变入参。

### 1.2 PlayOptions

播放选项的结构化容器。

代码位置：`xiaomusic/core/models/media.py`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `start_position` | `int` | `0` | 播放起始位置（秒） |
| `shuffle` | `bool` | `false` | 随机播放 |
| `loop` | `bool` | `false` | 循环播放 |
| `volume` | `int \| None` | `None` | 音量（0-100），`None` 表示不改变 |
| `timeout` | `float \| None` | `None` | 整体超时（秒） |
| `resolve_timeout_seconds` | `float \| None` | `None` | 来源解析超时；`None` 时按来源自动回退（site_media=15，其他=8） |
| `no_cache` | `bool` | `false` | 禁用来源缓存 |
| `prefer_proxy` | `bool` | `false` | 优先走代理投递 |
| `confirm_start` | `bool` | `true` | 播放确认开关 |
| `confirm_start_delay_ms` | `int` | `1200` | 首次确认延迟（毫秒） |
| `confirm_start_retries` | `int` | `2` | 确认重试次数 |
| `confirm_start_interval_ms` | `int` | `600` | 重试间隔（毫秒，最小 100） |
| `source_payload` | `dict \| None` | `None` | 来源插件扩展上下文 |
| `media_id` | `str` | `""` | 兼容 `id` 字段的媒体标识 |
| `title` | `str` | `""` | 媒体标题透传 |

边界规范：

- `PlayOptions.from_payload(payload)` 是唯一合法的边界解析入口，负责字符串布尔值规范化、数值合法性约束、旧字段兼容映射（`id` → `media_id`）。
- 兼容逻辑**只存在于** `from_payload()` 中，不向 coordinator 内部扩散。
- `PlayOptions.to_context()` 负责将选项展开为 `MediaRequest.context` 字典，供来源插件扩展使用。

### 1.3 ResolvedMedia

来源插件的解析结果，由 `SourcePlugin.resolve()` 返回。

| 字段 | 类型 | 说明 |
|---|---|---|
| `stream_url` | `str` | 可播放的媒体流 URL |
| `source` | `str` | 来源标识（`direct_url / site_media / jellyfin / local_library`） |
| `title` | `str` | 媒体标题 |
| `headers` | `dict` | 额外 HTTP 请求头 |
| `expires_at` | `int \| None` | UNIX 时间戳，流 URL 过期时间；`None` 表示不限期 |
| `is_live` | `bool` | 是否为直播流 |
| `prefer_proxy` | `bool` | 来源建议优先代理 |

约束：

- `source` 字段必须是四类正式来源标识之一，`network_audio` 已从 source hint 中移除。
- `stream_url` 必须是 `http://` 或 `https://`，否则 `DeliveryAdapter` 会拒绝并抛 `UndeliverableStreamError`。

### 1.4 PreparedStream 与 DeliveryPlan

`PreparedStream` 是 `DeliveryAdapter` 准备好的可投递流，包含 `final_url`（直链或代理 URL）与 `headers`。

`DeliveryPlan` 是 `prepare_plan()` 的返回值，包含：
- `primary`：主选流（按 `prefer_proxy` 决定直链或代理）
- `fallback`：备选流（`None` 表示无备选）

### 1.5 TransportDispatchResult

`TransportRouter.dispatch()` 的返回值。

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | `bool` | 是否成功 |
| `transport` | `str` | 实际使用的 transport 名称（`mina / miio`） |

---

## 2. 错误体系

代码位置：`xiaomusic/core/errors/`

错误分层：

```
Exception
  └─ CoreError                        (base.py)
       ├─ CoreValidationError          (base.py)
       │    └─ InvalidRequestError     (base.py)   — 请求参数非法
       ├─ SourceResolveError           (source_errors.py)  — 来源无法解析
       ├─ DeliveryPrepareError         (stream_errors.py)  — 流准备失败（基类）
       │    ├─ ExpiredStreamError      (stream_errors.py)  — URL 过期，应重试 resolve
       │    └─ UndeliverableStreamError (stream_errors.py) — 结构不可投递，不应重试
       ├─ TransportError               (transport_errors.py) — 传输执行失败
       └─ DeviceNotFoundError          (device_errors.py)  — 设备不在注册表
```

与 API 响应码的映射：

| 异常 | v1 code | stage 字段 |
|---|---|---|
| `InvalidRequestError` | `50001` | — |
| `SourceResolveError` | `20002` | `resolve` |
| `DeliveryPrepareError` / `ExpiredStreamError` / `UndeliverableStreamError` | `30001` | `prepare` |
| `TransportError` | `40002` | `dispatch` |
| `DeviceNotFoundError` | `40004` | `xiaomi` |

映射实现位置：`xiaomusic/api/routers/v1.py` 的异常处理器。

规范：

- Core 层只抛 `CoreError` 子类，不抛通用 `Exception` 或 `ValueError`（`device_id` 为空时例外，由 coordinator 顶层 raise `ValueError`）。
- `ExpiredStreamError` 表示"应当重试 resolve"；`UndeliverableStreamError` 表示"不应重试"。Coordinator 只重试前者。

---

## 3. Source 插件接口

代码位置：`xiaomusic/core/source/source_plugin.py`

```python
class SourcePlugin(ABC):
    name: str  # 来源标识，唯一键

    @abstractmethod
    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        ...
```

实现约束：

- `resolve()` 只做来源解析，返回可播放媒体信息。
- 不在 `resolve()` 内调用 transport 层，不写入设备状态。
- 解析失败时抛 `SourceResolveError`，不返回 `None` 或空 URL。
- 具体实现位于 `xiaomusic/adapters/sources/`，不在 core 层。

来源标识（`source_hint` / `ResolvedMedia.source`）允许值：

| 值 | 说明 |
|---|---|
| `direct_url` | HTTP/HTTPS 直链 |
| `site_media` | 需要站点解析的媒体（YouTube / Bilibili 等） |
| `jellyfin` | Jellyfin 资源 |
| `local_library` | 本地媒体库 |
| `auto` | 自动识别（`SourceRegistry` 按顺序尝试）|

兼容 hint（`SourceRegistry.LEGACY_HINT_MAP`，计划 v1.2 评估移除）：

| 旧值 | 映射目标 |
|---|---|
| `http_url` | `direct_url` |
| `site_media` | `site_media` |
| `local_music` | `local_library` |

---

## 4. Transport 接口与策略

代码位置：`xiaomusic/core/transport/transport.py`、`transport_policy.py`、`transport_router.py`

### 4.1 Transport 接口

```python
class Transport(ABC):
    name: str  # transport 标识（"mina" 或 "miio"）

    async def play_url(self, device_id: str, prepared: PreparedStream) -> dict: ...
    async def stop(self, device_id: str) -> dict: ...
    async def pause(self, device_id: str) -> dict: ...
    async def tts(self, device_id: str, text: str) -> dict: ...
    async def set_volume(self, device_id: str, volume: int) -> dict: ...
    async def probe(self, device_id: str) -> dict: ...
```

### 4.2 TransportPolicy（当前策略）

| 动作 | 优先顺序 |
|---|---|
| `play` | `mina` |
| `tts` | `miio` → fallback `mina` |
| `volume` | `miio` → fallback `mina` |
| `stop` | `miio` → fallback `mina` |
| `pause` | `miio` → fallback `mina` |
| `probe` | `miio` → fallback `mina` |

注意：`MiioTransport.play_url()` 显式不支持（Phase 3 策略），调用时直接抛 `TransportError`，play 动作必须走 mina。

### 4.3 能力矩阵

`TransportCapabilityMatrix`（`xiaomusic/core/models/transport.py`）记录每个设备对各 transport 的实际可用性，`TransportRouter` 取 policy 列表与设备能力矩阵的交集后按顺序选择。

---

## 5. 设备注册表

代码位置：`xiaomusic/core/device/device_registry.py`

`DeviceRegistry` 是内存注册表，为 coordinator 提供：

- `get_profile(device_id) -> DeviceProfile`：设备基本信息
- `get_reachability(device_id) -> DeviceReachability`：设备可达性状态
- `get_capability_matrix(device_id) -> TransportCapabilityMatrix`：传输能力矩阵

设备不存在时抛 `DeviceNotFoundError`。

与传统 `DeviceManager` 的关系：

- `DeviceRegistry` 通过 `_hydrate_from_legacy()` 从 `DeviceManager` 同步设备信息，是适配层而非替代品。
- `DeviceManager` 仍是传统设备生命周期的事实主入口。

---

## 6. 环境配置

代码位置：`xiaomusic/core/settings.py`

| 类 | 字段 | 说明 |
|---|---|---|
| `AuthSettings` | `HTTP_AUTH_HASH` | HTTP Basic Auth 密码哈希，必填 |
| `AnalyticsSettings` | `API_SECRET` | Analytics 密钥，可选 |

两者均通过 `pydantic-settings` 从环境变量（`.env` 文件或系统环境）加载，使用 `@lru_cache` 缓存单例。

`get_auth_settings()` / `get_analytics_settings()` 是推荐的访问函数，不应直接实例化这两个类。

---

## 7. 运行时约束

- **单 worker**：当前认证 token 写入与 config 原子写入均依赖进程内锁，不支持多 worker 并发写。
- **设备 ID 必填**：所有 coordinator 调用必须提供非空 `device_id`，facade 在入口处校验。
- **中文命令禁入**：中文命令字符串（`"上一首"`、`"播放歌单日语"` 等）不得进入 core 层，只允许存在于旧兼容层。
- **核心链路无重试扩散**：`ExpiredStreamError` 触发 coordinator 重试一次 resolve，其他错误直接向上抛，由 API 层统一处理。
