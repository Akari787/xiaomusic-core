# 小米音箱控制系统统一架构重构设计（整合版）

本文档为唯一的架构设计主文档，整合原有多份设计稿，统一接口、模块边界与实施计划。

适用范围：

- 播放来源插件化（Jellyfin / 本地媒体库 / 直链媒体 / 网站媒体）
- Transport 分层（Mina / Miio）
- 云依赖最小化
- 统一播放与设备模型

---

## 1. 重构目标与约束

### 1.1 目标

- Core 不依赖具体播放来源实现
- Core 不依赖具体传输协议实现
- Source 与 Transport 解耦
- 降低 Xiaomi Cloud 依赖，提高系统稳定性
- 删除过度设计，保留可扩展最小骨架

### 1.2 强约束

- 接口只定义一次（本文件第 4 章）
- Source 插件不得影响 Transport 选择
- 新增 DeliveryAdapter，负责“可播准备”
- Transport 仅保留统一 6 个动作接口
- CloudSessionManager 仅属于 MinaTransport 内部

---

## 2. 统一架构图

```text
API Layer
   |
   v
PlaybackCoordinator
   |\
   | +--> DeviceRegistry ----> DeviceProfile
   |                         -> DeviceReachability
   |                         -> TransportCapabilityMatrix
   |
   +--> SourceRegistry.get_plugin()
   |            |
   |            v
   |      SourcePlugin.resolve()
   |            |
   |            v
   |      ResolvedMedia
   |            |
   |            v
   |      DeliveryAdapter
   |            |
   |            v
   |      PreparedStream
   |            |
   |            v
   +--> TransportRouter <----- TransportPolicy
                |
      +---------+---------+
      |                   |
 MiioTransport      MinaTransport ----> CloudSessionManager
      |                   |             (Mina internal only)
      v                   v
   Device              Device
```

---

## 3. 模块边界（统一口径）

### 3.1 Core（业务编排层）

包含：

- `PlaybackCoordinator`
- `SourceRegistry`
- `DeviceRegistry`
- `DeliveryAdapter`
- `TransportRouter`

职责：

- 接收播放请求并调度流程
- 通过 `DeviceRegistry` 获取设备画像、可达性与能力矩阵
- 通过 `SourceRegistry` 查找插件，并由 `PlaybackCoordinator` 直接调用插件 `resolve`
- 将 `ResolvedMedia` 准备为可下发流（`PreparedStream`）
- 通过 `TransportRouter + TransportPolicy` 选择 transport 并执行

不负责：

- 来源细节（Jellyfin API/本地扫描）
- Cloud token 细节
- Mina/Miio 协议细节

流程约束（关键）：

- `SourceRegistry` 不执行媒体解析业务
- 解析调用链必须是：
  - `PlaybackCoordinator -> SourceRegistry.get_plugin() -> SourcePlugin.resolve()`

### 3.2 Source 插件层

当前官方来源插件命名：

- `JellyfinSourcePlugin`
- `DirectUrlSourcePlugin`
- `SiteMediaSourcePlugin`
- `LocalLibrarySourcePlugin`

旧 source-hint 兼容映射：`http_url -> direct_url`、`network_audio -> site_media`、`local_music -> local_library`。

职责：

- `resolve`
- `search`（可选）
- `browse`（可选）

不负责：

- transport 选择
- 设备控制
- 路由策略

### 3.3 DeliveryAdapter

职责：

- 判断 URL 是否可直播
- 判断是否改写为本地代理 URL
- 注入播放所需 headers
- 检测 URL 是否过期并上抛错误

不负责：

- 调用 `SourcePlugin`
- 主动执行 URL 刷新

约束：

- 当检测到 URL 临近过期或已过期时，`DeliveryAdapter` 必须抛出 `ExpiredStreamError`
- 由 `PlaybackCoordinator` 捕获后重新执行 `SourcePlugin.resolve()` 获取新 `ResolvedMedia`

### 3.4 Transport 层

职责：

- 执行设备控制动作（播放/暂停/停止/TTS/音量/探测）
- 不做业务决策

### 3.5 Device 数据层

`DeviceRegistry` 负责设备数据来源与统一视图：

- 管理 `DeviceProfile`
- 管理 `DeviceReachability`
- 管理 `TransportCapabilityMatrix`
- 向 `PlaybackCoordinator` 提供播放前设备能力查询

### 3.6 Cloud 会话层

`CloudSessionManager` 仅供 `MinaTransport` 使用，不暴露给 Core。

---

## 4. 唯一接口定义（全系统唯一）

> 说明：本章为唯一接口定义区。其他章节与文档不得重复定义这些接口。

### 4.1 MediaRequest

用途：Core 发给 SourcePlugin 的标准请求。

核心字段：

- `request_id`
- `source_hint`（可选）
- `query`（关键词/ID/URL/path）
- `device_id`（可选）
- `context`（扩展上下文）

### 4.2 ResolvedMedia

用途：SourcePlugin 返回的标准解析结果。

核心字段：

- `media_id`
- `source`
- `title`
- `stream_url`
- `headers`（可选）
- `expires_at`（可选）
- `is_live`

约束：

- 禁止包含 `preferred_transport` / `transport_hint` 等字段。

### 4.3 SourcePlugin

必选方法：

- `resolve(MediaRequest) -> ResolvedMedia`

可选方法：

- `search(query, limit) -> list[MediaItem]`
- `browse(path, page, size) -> list[MediaItem|Node]`

### 4.4 Transport

统一接口仅允许：

- `play_url(...)`
- `stop(...)`
- `pause(...)`
- `tts(...)`
- `set_volume(...)`
- `probe(...)`

禁用旧接口：

- `play(device, playable, session)`

### 4.5 DeviceProfile

字段：

- `did`
- `model`
- `name`
- `group`

### 4.6 DeviceReachability

字段：

- `ip`
- `local_reachable`
- `cloud_reachable`
- `last_probe_ts`

### 4.7 TransportCapabilityMatrix

字段语义：按动作映射可用 transport 列表。

示例：

- `play: [mina]`
- `tts: [miio, mina]`
- `volume: [miio, mina]`
- `stop: [miio, mina]`

禁用字段：

- `can_play_via_mina`
- `can_play_via_miio`

### 4.8 PreparedStream

用途：`DeliveryAdapter` 输出的最终可播放流对象，供 `Transport` 直接下发使用。

核心字段：

- `final_url`
- `headers`
- `expires_at`
- `is_proxy`
- `source`

约束：

- `PreparedStream` 是 transport 输入对象，不携带来源插件行为。
- `PreparedStream` 的有效性由 DeliveryAdapter 在下发前校验。

### 4.9 标准错误定义

#### ExpiredStreamError

含义：

- `PreparedStream` 或 `ResolvedMedia` 中的 `stream_url` 已过期，或当前状态下不可安全下发。

处理流程：

1. DeliveryAdapter 检测到 URL 过期时抛出 `ExpiredStreamError`
2. PlaybackCoordinator 捕获异常
3. PlaybackCoordinator 重新执行 `SourcePlugin.resolve()`
4. 获取新的 `ResolvedMedia` 后再次进入 DeliveryAdapter

---

## 5. Source Plugin 设计（简化版）

### 5.1 插件能力范围

- 仅处理内容查询与解析
- 输出 `ResolvedMedia`

### 5.2 注册机制

- 第一阶段仅支持内置静态注册
- 不支持 entry_points
- 不支持动态安装

### 5.3 生命周期

- `register -> active -> disabled`

说明：

- 插件是被动组件，不包含独立线程或服务生命周期
- 插件仅在被 `PlaybackCoordinator` 调用时执行

删除：

- 插件热加载
- 插件独立 StateStore
- 插件熔断系统
- PluginHealthMonitor

---

## 6. DeliveryAdapter 设计

### 6.1 输入输出

- 输入：`ResolvedMedia`
- 输出：`PreparedStream`

`PreparedStream` 语义：可直接给 Transport 使用的流信息（最终 URL + headers + 过期信息）。

### 6.2 决策规则

1. URL 可直链播放 -> 保持直链
2. URL 不可直播/不可达 -> 转本地代理
3. 需要认证头 -> 注入 headers
4. URL 临近过期/已过期 -> 抛出 `ExpiredStreamError`

刷新责任边界：

- DeliveryAdapter 只负责检测过期并抛错
- `PlaybackCoordinator` 负责捕获错误并重新调用 `SourcePlugin.resolve()`
- DeliveryAdapter 不允许直接调用 SourcePlugin，避免循环依赖

### 6.3 与 Router 协作

- DeliveryAdapter 只输出可播流，不做 transport 选择
- TransportRouter 基于 `TransportCapabilityMatrix` 与 `TransportPolicy` 选择候选 transport

---

## 7. Transport 设计

### 7.1 组件

- `MinaTransport`
- `MiioTransport`
- `TransportRouter`

### 7.2 路由原则

新增 `TransportPolicy` 作为路由策略输入，避免硬编码。

`TransportPolicy` 示例：

- `play: [mina]`
- `tts: [miio, mina]`
- `volume: [miio, mina]`
- `stop: [miio, mina]`
- `pause: [miio, mina]`
- `probe: [miio, mina]`

`TransportCapabilityMatrix` 与 `TransportPolicy` 关系：

- `TransportCapabilityMatrix` 表示设备能力事实（某动作支持哪些 transport）
- `TransportPolicy` 表示系统策略顺序（多个可用 transport 的优先级）

执行规则：

```text
candidate_transports =
    intersection(
        TransportCapabilityMatrix[action],
        TransportPolicy[action]
    )
```

`TransportRouter` 只允许在 `candidate_transports` 中执行路由与 fallback。

执行流程：

1. 读取动作 `action`
2. 读取设备能力 `TransportCapabilityMatrix[action]`
3. 读取系统策略 `TransportPolicy[action]`
4. 求交集得到 `candidate_transports`
5. 按 `TransportPolicy` 顺序在候选集内尝试
6. 失败时在候选集内 fallback
7. 候选集全部失败则返回统一错误

`TransportRouter` 职责：

- 根据 `TransportPolicy` 选择执行顺序
- 执行 fallback
- 返回统一结果

`TransportRouter` 不负责：

- 定义业务策略
- 写死 Mina/Miio 优先级

### 7.3 边界约束

- Transport 不处理歌单、来源解析、会话业务语义
- Transport 只执行动作并返回执行结果
- Transport 不负责写入设备状态（包括 `DeviceReachability`）

### 7.4 probe 流程

probe 流程定义：

1. `DeviceRegistry` 或 `PlaybackCoordinator` 发起探测请求
2. `TransportRouter` 根据 `TransportPolicy` 与 `TransportCapabilityMatrix` 选择候选 transport
3. 选中的 `Transport` 执行 `probe(...)`
4. `Transport` 返回 probe 结果
5. `DeviceRegistry` 写入并更新 `DeviceReachability`

约束：

- `Transport` 只返回探测结果，不写回设备状态。
- `DeviceRegistry` 是 `DeviceReachability` 的唯一更新入口。

---

## 8. 设备模型设计（拆分后）

设备数据拆为三块：

1. `DeviceProfile`（静态标识）
2. `DeviceReachability`（连通性状态）
3. `TransportCapabilityMatrix`（动作-传输能力）

收益：

- 职责清晰，避免单模型膨胀
- 可独立更新连通性与能力，不污染设备主数据

### 8.1 DeviceRegistry（新增组件说明）

职责：

- 统一维护设备三类数据：`DeviceProfile` / `DeviceReachability` / `TransportCapabilityMatrix`
- 提供查询接口给 `PlaybackCoordinator`（播放前必经）
- 为 `TransportRouter` 提供动作可用矩阵依据
- 接收 probe 结果并更新 `DeviceReachability`

边界：

- 不参与媒体解析
- 不参与 transport 执行
- 是 `DeviceReachability` 的唯一写入入口

### 8.2 DeviceReachability 更新流程

1. `DeviceRegistry` 或 `PlaybackCoordinator` 发起 probe
2. `TransportRouter` 选择并调用 transport probe
3. transport 返回探测结果
4. `DeviceRegistry` 统一落库/落内存更新 `DeviceReachability`

---

## 9. 媒体模型设计（统一后）

核心对象：

- `MediaRequest`
- `MediaItem`
- `ResolvedMedia`

关系：

- `MediaRequest` 进入 Source
- Source 输出 `ResolvedMedia`
- DeliveryAdapter 将其转换为 `PreparedStream`

URL 生命周期：

- `valid -> near_expiry -> expired`

当进入 `near_expiry/expired`：

- DeliveryAdapter 抛出 `ExpiredStreamError`
- Coordinator 重新触发 `SourcePlugin.resolve()`
- 获取新 `ResolvedMedia` 后再次进入 DeliveryAdapter

---

## 10. CloudSessionManager 设计（降级后）

### 10.1 归属

- 只属于 `MinaTransport` 内部实现
- Core 禁止直接调用

### 10.2 职责

- token 加载与刷新
- 云会话可用性判断
- 失败退避

### 10.3 非职责

- 不参与播放调度
- 不管理来源插件
- 不直接暴露给业务 API

---

## 11. 云依赖最小化策略（精简版）

- 默认 `TransportPolicy` 采用本地优先控制策略（如 `tts/stop/volume/pause` 优先 Miio）
- 云端仅保留必要调用（主要用于 Mina 播放下发）
- 设备信息低频同步，避免高频 `device_list`
- 云故障时进入降级模式，保留本地可执行能力

---

## 12. 实施分期（工程可执行）

### Phase 1：骨架与接口收敛

- 落地统一接口（第 4 章）
- 接入 SourceRegistry + DeliveryAdapter + TransportRouter 最小链路
- 保持对外兼容

### Phase 2：主链路切换

- `play_url/stop/pause/tts/set_volume/probe` 迁入统一链路
- Device 模型切换为三分结构
- 删除旧 transport 接口

### Phase 3：清理与收口

- 移除过度设计组件
- 清理重复文档与重复定义
- 新架构默认启用

---

## 13. 删除与清理清单

必须删除的架构组件概念：

- `PluginManager`（复杂版）
- `PluginHealthMonitor`
- `MetadataCache`（作为架构主组件）
- `TransportHealthScore`
- 插件独立 `StateStore`
- 插件热加载系统

文档治理原则：

- 本文档作为唯一主设计文档
- 其他历史文档在迁移完成后删除，避免多版本冲突

---

## 14. 验收标准

- Core 不依赖具体 Source
- Core 不依赖 Mina/Miio
- Source 不影响 Transport
- Transport 不包含业务逻辑
- 插件系统简单且可扩展
- `SourceRegistry` 不包含业务解析逻辑
- DeliveryAdapter 不调用 SourcePlugin
- TransportRouter 基于 `TransportPolicy` 与 `TransportCapabilityMatrix` 路由
- `DeviceRegistry` 是 `DeviceReachability` 的唯一更新入口

---

## 附录：未来优化建议

- 将 `TransportPolicy` 外置为可配置文件，支持按设备组覆盖策略。
- 为 `ExpiredStreamError` 增加统一错误码与指标埋点，便于定位来源解析稳定性。
- 为 `DeviceRegistry` 增加快照版本号与变更日志，便于排障与回滚。
