# xiaomusic-oauth2 架构重构设计文档（可执行版）

> 版本：Draft v1  
> 目标读者：项目维护者 / Python 后端工程师 / 贡献者  
> 范围：后端核心重构（不要求先改 API 表面行为）

---

## 1. 当前系统问题分析

基于现有代码（如 `xiaomusic/xiaomusic.py`、`device_player.py`、`auth.py`、`music_library.py`、`network_audio/runtime.py`、`services/online_music_service.py`）可归纳为：

### 1.1 领域边界不清，核心类过胖
- `XiaoMusic` 同时承担配置、认证、设备管理、播放、歌单同步、插件调度、轮询会话等职责。
- `XiaoMusicDevice` 既做播放状态机，又做下载、TTS、自动下一首、分组控制、错误重试，难测且难替换。

### 1.2 播放来源耦合严重
- 本地音乐、Jellyfin、在线插件、URL 播放等逻辑散落在 `music_library`、`online_music_service`、`device_player`、`network_audio`。
- 新增来源时，需要跨多个模块改逻辑，违反开闭原则。

### 1.3 Cloud 会话与播放控制耦合
- `auth.py` 里既有登录刷新又有高频接口熔断策略，且被设备控制路径直接依赖。
- 设备控制动作默认绕 Cloud（Mina），本地能力（Miio）没有形成明确优先级路由。

### 1.4 设备视图不稳定
- `device_list` 与 `did/device_id` 映射依赖云端返回，失联时容易出现控制不可达或状态不一致。
- 缓存存在但不是统一“设备注册中心”（registry）语义。

### 1.5 播放会话模型分裂
- 目前有 network_audio `Session`，但全局缺少统一 `PlaySession` 抽象，导致 direct play / proxy / cast 观测字段不一致。
- API 层对状态字段做转换，业务语义分散。

### 1.6 可维护性与可测试性风险
- 多模块相互引用、隐式全局状态（lazy proxy）较多。
- 测试虽多，但多数是行为补丁式，缺少稳定的架构契约测试（plugin/transport/session 契约）。

---

## 2. 重构目标

### 2.1 功能目标
1. 播放来源插件化：Jellyfin、本地文件、HTTP URL，并支持未来新增来源。  
2. Core 瘦身：只保留调度、队列、会话、状态、transport 路由。  
3. 云依赖最小化：Cloud 仅做必要控制面（播放下发、必要鉴权）。  
4. 引入统一模型：`MediaItem` / `Playable` / `DeviceCapability` / `PlaySession`。  
5. 建立 transport 层：`MinaTransport`、`MiioTransport`。  
6. 支持分阶段迁移，保持现有 API 和 WebUI 基本可用。

### 2.2 非功能目标
- 可插拔：新增来源不修改 Core。
- 可观测：统一 session 状态、错误码、阶段(stage)。
- 可回退：任意阶段可切回旧路径。
- 可测试：契约测试优先（plugin/transport/registry/session）。

---

## 3. 系统整体架构图（ASCII）

```text
+---------------------------------------------------------------+
|                        FastAPI API Layer                      |
|  /api/v1/*  /network_audio/*  /oauth2/*  legacy endpoints    |
+-------------------------------+-------------------------------+
                                |
                                v
+---------------------------------------------------------------+
|                             Core                              |
| +---------------------+  +------------------+  +-----------+ |
| | PlaybackCoordinator |  | SessionManager   |  | StateStore| |
| | QueueManager        |  | PlaySession FSM  |  | (in-mem)  | |
| +----------+----------+  +---------+--------+  +-----+-----+ |
|            |                       |                 |         |
|            v                       v                 |         |
|   +------------------+    +------------------+      |         |
|   | SourceResolver   |    | TransportRouter  |<-----+         |
|   +--------+---------+    +--------+---------+                |
|            |                       |                          |
+------------|-----------------------|--------------------------+
             |                       |
             v                       v
+---------------------------+   +-------------------------------+
|       Source Plugins      |   |         Transport Layer       |
| LocalFileSourcePlugin     |   | MinaTransport (Cloud)         |
| JellyfinSourcePlugin      |   | MiioTransport (LAN)           |
| HttpUrlSourcePlugin       |   | (future: MQTT/Other)          |
+-------------+-------------+   +---------------+---------------+
              |                                 |
              v                                 v
      +------------------+             +------------------------+
      | Media/URL Resolve|             | Cloud Session Manager  |
      | metadata/cache   |             | token/refresh/backoff  |
      +------------------+             +-----------+------------+
                                                   |
                                                   v
                                            Xiaomi Cloud APIs
```

---

## 4. Core 职责

Core 只保留 6 个能力：

1. **PlaybackCoordinator**：处理 `play/stop/pause/next/prev` 请求与状态机推进。  
2. **QueueManager**：每设备（或每组）队列管理（插入、跳过、循环策略）。  
3. **SessionManager**：管理 `PlaySession` 生命周期、状态、指标。  
4. **TransportRouter**：根据设备能力与策略选择 Mina/Miio。  
5. **SourceResolver**：通过插件将输入请求解析为 `Playable`。  
6. **StateStore**：统一缓存运行时状态（设备、会话、健康度）。

### Core 模块建议目录

```text
xiaomusic/core_v2/
  models.py
  coordinator.py
  queue.py
  session_manager.py
  transport_router.py
  source_resolver.py
  state_store.py
  policies.py
```

### Core 对外接口（示意）

```python
class PlaybackCoordinator:
    async def play(self, req: "PlayRequest") -> "PlayResult": ...
    async def stop(self, req: "StopRequest") -> "PlayResult": ...
    async def status(self, req: "StatusRequest") -> "PlaySession": ...
```

---

## 5. Source Plugin 架构

### 5.1 插件职责
每个 Source Plugin 负责：
- 接受查询参数（如 song_id、keyword、url、path）
- 产出标准化 `Playable`（可播放 URL + 元数据 + 约束）
- 可选：提供搜索、列表、歌词、封面等扩展能力

### 5.2 核心接口设计

```python
from typing import Protocol, Iterable

class SourcePlugin(Protocol):
    plugin_id: str

    async def can_handle(self, req: "MediaRequest") -> bool: ...
    async def resolve(self, req: "MediaRequest") -> "Playable": ...

    # optional capabilities
    async def search(self, q: str, limit: int = 20) -> list["MediaItem"]: ...
    async def health(self) -> dict: ...
```

### 5.3 标准化模型（示意）

```python
from dataclasses import dataclass, field
from typing import Literal

@dataclass
class MediaItem:
    media_id: str
    source: str                 # jellyfin/local/http
    title: str
    artist: str = ""
    album: str = ""
    duration_sec: float = 0
    tags: dict = field(default_factory=dict)

@dataclass
class Playable:
    media: MediaItem
    stream_url: str
    origin_url: str = ""
    protocol: Literal["file","http","https","hls","dash"] = "http"
    is_live: bool = False
    requires_proxy: bool = False
    preferred_transport: str | None = None  # e.g. "mina"
```

### 5.4 内置插件建议
- `LocalFileSourcePlugin`：从本地路径/歌曲名解析（整合现 `music_library` 本地路径能力）
- `JellyfinSourcePlugin`：封装 Jellyfin 搜索与 URL 构建（整合 `jellyfin_client`）
- `HttpUrlSourcePlugin`：纯 URL 来源 + 安全校验 + 规范化（整合 `link_strategy` & `network_audio` 入口）

### 5.5 插件注册机制
```python
class SourceRegistry:
    def register(self, plugin: SourcePlugin) -> None: ...
    def list_plugins(self) -> list[str]: ...
    async def resolve(self, req: MediaRequest) -> Playable: ...
```

---

## 6. Transport 架构

### 6.1 设计目标
- 把“如何下发播放指令”与“播放来源解析”彻底解耦。
- 可按设备能力/网络状况切换 transport。

### 6.2 统一接口

```python
class Transport(Protocol):
    transport_id: str

    async def play(self, device: "DeviceRef", playable: "Playable", session: "PlaySession") -> "TransportResult": ...
    async def stop(self, device: "DeviceRef", session: "PlaySession|None" = None) -> "TransportResult": ...
    async def set_volume(self, device: "DeviceRef", volume: int) -> "TransportResult": ...
    async def tts(self, device: "DeviceRef", text: str) -> "TransportResult": ...
    async def probe(self, device: "DeviceRef") -> "TransportProbe": ...
```

### 6.3 实现策略
- **MinaTransport**：保留云端 `play_by_url/play_by_music_url/player_*`。
- **MiioTransport**：优先承接 `tts/stop/volume`（局域网能力优先）。
- **TransportRouter**：根据 `DeviceCapability` + 策略路由：
  - `play`: 默认 Mina（必要时）  
  - `stop/volume/tts`: 优先 Miio，失败回 Mina

---

## 7. Cloud Session Manager 架构

### 7.1 职责收敛
Cloud Session Manager 只做：
- token 加载/刷新/持久化
- Mina/Miio 客户端实例生命周期
- 认证状态与熔断
- 最低频健康检查

### 7.2 建议接口

```python
class CloudSessionManager:
    async def ensure_ready(self, reason: str = "") -> bool: ...
    async def refresh(self, force: bool = False) -> dict: ...
    def auth_status(self) -> dict: ...
    def mina_client(self): ...
    def miio_client(self): ...
```

### 7.3 关键策略
- 与业务解耦：Core 不直接调用 refresh 细节，只看 `ensure_ready()`。
- 单写点：沿用 `TokenStore`，禁止多 worker 竞争写。
- 认证熔断：保留现有 backoff/cooldown，但由 manager 统一暴露状态。

---

## 8. Device Registry 设计

### 8.1 目标
以 registry 替代散落的 `device_id/did` 映射逻辑，支持“云不可用时可控”。

### 8.2 数据结构

```python
@dataclass
class DeviceCapability:
    can_play_mina: bool = True
    can_stop_miio: bool = True
    can_tts_miio: bool = True
    can_set_volume_miio: bool = True

@dataclass
class DeviceRecord:
    did: str
    device_id: str
    name: str
    hardware: str
    group: str = ""
    capability: DeviceCapability = DeviceCapability()
    last_seen_ts: int = 0
    source: str = "cloud"   # cloud/cache/manual
```

### 8.3 接口

```python
class DeviceRegistry:
    async def refresh_from_cloud(self) -> int: ...
    def get(self, did: str) -> DeviceRecord | None: ...
    def resolve_by_any(self, key: str) -> DeviceRecord | None: ...
    def list(self) -> list[DeviceRecord]: ...
    def mark_seen(self, did: str): ...
```

### 8.4 存储建议
- 内存主存 + `conf/device_registry.json` 持久化快照。
- 启动优先加载快照，后台低频刷新（例如 10~30 分钟）。

---

## 9. 播放流程设计

### 9.1 统一流程（play）

```text
API Request
  -> PlaybackCoordinator.play()
    -> DeviceRegistry.resolve()
    -> SourceResolver.resolve() -> Playable
    -> SessionManager.create_session()
    -> TransportRouter.select("play")
    -> transport.play(...)
    -> SessionManager.update_state(streaming/failed)
    -> return PlayResult
```

### 9.2 状态机（PlaySession）

```text
created -> resolving -> queued -> dispatching -> streaming
                                   |               |
                                   v               v
                                 failed <------ reconnecting
                                   |
                                   v
                                 stopped
```

### 9.3 会话模型示例

```python
@dataclass
class PlaySession:
    session_id: str
    did: str
    media_id: str
    source: str
    state: str
    stream_url: str = ""
    started_at: int = 0
    last_transition_at: int = 0
    error_code: str | None = None
    fail_stage: str | None = None
    retry_count: int = 0
    metadata: dict = field(default_factory=dict)
```

---

## 10. 插件生命周期

### 生命周期阶段
1. **discover**：从内置目录加载（后续可支持 entry points）。  
2. **register**：完成接口校验与 ID 去重。  
3. **init**：传入只读配置和依赖（logger/http client/cache）。  
4. **healthcheck**：启动后探测可用性。  
5. **serve**：处理 resolve/search 请求。  
6. **degrade**：连续失败时降级（短暂熔断）。  
7. **shutdown**：释放资源。

### 插件管理器接口

```python
class PluginManager:
    async def load_all(self) -> None: ...
    async def reload(self, plugin_id: str) -> None: ...
    def get(self, plugin_id: str) -> SourcePlugin: ...
    def stats(self) -> dict: ...
```

---

## 11. 错误处理策略

### 11.1 统一错误码层级
- `E_SOURCE_*`：来源解析失败（URL 无效、资源不存在、鉴权失败）
- `E_TRANSPORT_*`：下发失败（云端失败、局域网不可达）
- `E_SESSION_*`：会话状态异常
- `E_DEVICE_*`：设备不可用/未注册
- `E_AUTH_*`：云会话异常

### 11.2 错误对象标准

```python
@dataclass
class XmError:
    code: str
    message: str
    stage: str        # resolve/dispatch/stream/control/auth
    retriable: bool
    details: dict = field(default_factory=dict)
```

### 11.3 策略
- API 返回用户可读 message + 机器可读 error_code/stage。
- 日志保留结构化字段（request_id/session_id/did/source/transport）。
- 敏感字段统一脱敏（沿用 `security/redaction.py` 思路）。

---

## 12. 回退策略

### 12.1 Transport 回退
- `tts/stop/volume`: Miio -> Mina
- `play`: Mina 首选；若 Mina 错误为可重试且设备支持 fallback，则尝试 proxy/network_audio 路径

### 12.2 Source 回退
- Jellyfin 直连失败 -> Jellyfin proxy URL
- URL 解析失败 -> no_cache 重试 -> direct 原链路尝试
- 插件熔断后回默认 `HttpUrlSourcePlugin`（仅 URL 类型）

### 12.3 Core 回退（关键）
- 提供 `LEGACY_PLAYBACK_ENABLED=true` 开关：
  - 新链路失败时，回调旧 `xiaomusic.play_url()/device_player` 路径
  - 确保迁移阶段生产可控

---

## 13. 云依赖最小化策略

1. **设备信息低频同步**：`device_list` 启动时 + 定时低频（10-30 分钟）+ 手动触发。  
2. **缓存优先**：控制请求先查 Device Registry 快照。  
3. **本地控制优先**：`stop/tts/volume` 首先尝试 Miio。  
4. **云调用削峰**：保留现有 high-frequency circuit breaker，统一移入 CloudSessionManager。  
5. **无感降级**：云临时不可用时，仍允许本地可执行能力和已缓存设备控制。  
6. **明确“必要云调用”白名单**：播放下发、必要鉴权刷新，其他禁止高频轮询。

---

## 14. 向后兼容策略

### 14.1 API 兼容
- 保持 `/api/v1/*` 现有请求/响应 shape 不变（`ok/success/error_code/message`）。
- legacy 路由继续保留一段时间，内部转发到新 Core。

### 14.2 配置兼容
- 继续读取现有 `setting.json` 字段，新增字段走默认值。
- 提供 config adapter：旧字段 -> 新模型（如 `jellyfin_force_proxy` -> proxy policy）。

### 14.3 模块兼容
- 初期 `XiaoMusic` 作为 Facade，不立刻删除；内部逐步委托到 `core_v2`。
- 保留现 `TokenStore`、`AuthManager`，先重构包裹层再替换实现。

### 14.4 可观测兼容
- 保持现有日志关键字与 error_code，避免运维监控失效。

---

## 15. 重构阶段计划（Phase1 / Phase2 / Phase3）

## Phase 1：建骨架 + 双轨运行（低风险）
**目标**：引入新抽象，不改外部行为。

- 新增目录：`core_v2/`、`sources/`、`transports/`、`cloud/`、`registry/`
- 落地统一模型：`MediaItem/Playable/PlaySession/DeviceCapability`
- 实现 `SourcePlugin` 与 `Transport` 协议
- 先做 3 个插件（Local/Jellyfin/HttpUrl）“薄封装”
- `PlaybackCoordinator` 只接 `/api/v1/play_url` 灰度
- 引入 `LEGACY_PLAYBACK_ENABLED` 回退开关
- 增加契约测试：
  - source resolve contract
  - transport play/stop contract
  - session state transition contract

**验收**
- `/api/v1/play_url` 在灰度模式可用
- 失败时可回退到旧链路
- 覆盖核心新模块单测 > 70%

---

## Phase 2：核心切换 + 云依赖削减（中风险）
**目标**：将主播放链路切到 Core v2，降低 cloud 调用频率。

- `/api/v1/play_*`、`/api/v1/stop`、`/api/v1/status` 全部接入 `PlaybackCoordinator`
- 上线 `DeviceRegistry`（持久化快照 + 低频同步）
- 上线 `TransportRouter`（Miio 优先控制类动作）
- CloudSessionManager 接管 auth 状态与 refresh 接口
- 将 `device_list` 高频调用迁移为按需/低频

**验收**
- 云调用频率显著下降（可统计）
- 云不可用场景下 stop/tts/volume 仍可执行（取决于设备能力）
- API 响应 shape 与历史兼容

---

## Phase 3：收口与清理（中高风险）
**目标**：移除耦合历史实现，形成长期演进架构。

- 精简 `xiaomusic/xiaomusic.py` 为 Facade/Bootstrap
- `device_player` 中与来源解析/下载/cloud 交叉逻辑迁出
- 废弃重复路径（例如旧 style 的 URL 处理散点）
- 文档更新：插件开发指南、transport 扩展指南、错误码手册
- 将旧模块标记 deprecated，给出移除时间表

**验收**
- Core 职责清晰，新增来源无需改 Core
- transport/source/cloud 各层可独立测试
- 代码复杂度与模块耦合度下降（可用 import graph/圈复杂度验证）

---

## 附：建议的目标目录结构（示例）

```text
xiaomusic/
  core_v2/
    models.py
    coordinator.py
    queue.py
    session_manager.py
    transport_router.py
    source_resolver.py
    errors.py
  sources/
    base.py
    local_file.py
    jellyfin.py
    http_url.py
    registry.py
  transports/
    base.py
    mina.py
    miio.py
    router.py
  cloud/
    session_manager.py
    token_provider.py
  registry/
    device_registry.py
  adapters/
    legacy_api_adapter.py
```
