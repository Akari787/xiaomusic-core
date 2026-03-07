# Core 实现骨架设计书（Phase 1）

本文档用于指导 `architecture_unified_refactor_design.md` 的落地实现。  
范围仅限“实现骨架”，不包含完整业务功能。

---

## 1. 实现目标与约束

### 1.1 目标

- 建立 `core/` 目录下统一编排骨架
- 完成最小播放链路可运行骨架
- 将现有 `MinaTransport` / `MiioTransport` 通过适配层接入
- 先实现 `HttpUrlSourcePlugin` 作为首个来源插件

### 1.2 架构约束（必须满足）

- Core 不依赖具体 Source
- Core 不依赖 Mina/Miio 具体实现
- Source 不影响 Transport 选择
- Transport 不包含业务逻辑
- `SourceRegistry` 仅注册与查找
- `DeliveryAdapter` 不调用 `SourcePlugin`

---

## 2. 新代码目录结构规划

```text
project_root/
  core/
    coordinator/
      playback_coordinator.py
    source/
      source_plugin.py
      source_registry.py
    delivery/
      delivery_adapter.py
    transport/
      transport.py
      transport_router.py
      transport_policy.py
    device/
      device_registry.py
    models/
      media.py
      device.py
      transport.py
    errors/
      base.py
      source_errors.py
      transport_errors.py
      stream_errors.py
```

说明：

- `core/` 只包含编排与抽象，不放具体 Mina/Miio/Jellyfin 业务代码。
- 具体实现建议在现有业务目录保留，通过 adapter 注入到 core 接口。

建议补充（不在 core 内）：

```text
project_root/
  adapters/
    sources/
      http_url_source_plugin.py
    transports/
      mina_transport_adapter.py
      miio_transport_adapter.py
```

---

## 3. 核心类骨架设计

## 3.1 PlaybackCoordinator

职责：

- 串联最小播放链路
- 组织 source 解析、delivery 准备、transport 下发
- 处理 `ExpiredStreamError` 并触发重新 resolve

输入：

- `MediaRequest`
- `device_id`（来自 API 或请求上下文）

输出：

- `PlayResultDTO`（Phase 1 可先用 dict）

依赖组件：

- `SourceRegistry`
- `DeviceRegistry`
- `DeliveryAdapter`
- `TransportRouter`

调用关系：

1. 从 `DeviceRegistry` 获取设备能力
2. 通过 `SourceRegistry.get_plugin()` 获取插件
3. 调用 `plugin.resolve()` 获取 `ResolvedMedia`
4. 调用 `DeliveryAdapter.prepare()` 获取 `PreparedStream`
5. 调用 `TransportRouter.dispatch_play_url()` 下发播放

---

## 3.2 SourceRegistry

职责：

- 注册插件
- 查找插件

输入：

- `register(plugin: SourcePlugin)`
- `get_plugin(source_hint: str | None, request: MediaRequest)`

输出：

- `SourcePlugin`

依赖组件：

- 无业务依赖

边界：

- 不执行媒体解析
- 不处理 transport 逻辑

---

## 3.3 DeviceRegistry

职责：

- 提供设备信息读取
- 提供 `TransportCapabilityMatrix` 与 `DeviceReachability`
- 接收 probe 结果并更新 `DeviceReachability`

输入：

- `get_profile(device_id)`
- `get_reachability(device_id)`
- `get_capability_matrix(device_id)`
- `update_reachability(device_id, probe_result)`

输出：

- `DeviceProfile`
- `DeviceReachability`
- `TransportCapabilityMatrix`

依赖组件：

- Phase 1 可先用内存存储

---

## 3.4 DeliveryAdapter

职责：

- 将 `ResolvedMedia` 转换为 `PreparedStream`
- 校验 URL 可下发性
- 注入 headers
- 判断是否本地代理
- 检测过期并抛 `ExpiredStreamError`

输入：

- `ResolvedMedia`

输出：

- `PreparedStream`

依赖组件：

- URL 校验工具（纯工具函数）

边界：

- 不调用 SourcePlugin
- 不做 transport 选择

---

## 3.5 TransportRouter

职责：

- 根据 `TransportCapabilityMatrix` 与 `TransportPolicy` 计算候选 transport
- 执行 route + fallback
- 返回统一 transport 执行结果

输入：

- `action`（play/tts/stop/pause/volume/probe）
- `PreparedStream`（play_url 时）
- `DeviceProfile`
- `TransportCapabilityMatrix`

输出：

- `TransportDispatchResult`

依赖组件：

- `TransportPolicy`
- `Transport` 实例集合（按名称注册）

执行规则：

```text
candidate_transports = intersection(
  capability_matrix[action],
  transport_policy[action]
)
```

---

## 4. DTO 数据模型定义（core/models）

> 所有 DTO 仅承载数据，不包含业务逻辑。

## 4.1 MediaRequest

字段建议：

- `request_id: str`
- `source_hint: str | None`
- `query: str`
- `device_id: str | None`
- `context: dict[str, Any]`

语义：统一媒体请求输入。

## 4.2 ResolvedMedia

字段建议：

- `media_id: str`
- `source: str`
- `title: str`
- `stream_url: str`
- `headers: dict[str, str]`
- `expires_at: int | None`
- `is_live: bool`

语义：Source 插件返回的解析结果。

## 4.3 PreparedStream

字段建议：

- `final_url: str`
- `headers: dict[str, str]`
- `expires_at: int | None`
- `is_proxy: bool`
- `source: str`

语义：可直接给 Transport 使用的最终播放流。

## 4.4 DeviceProfile

字段建议：

- `did: str`
- `model: str`
- `name: str`
- `group: str`

语义：设备静态画像。

## 4.5 DeviceReachability

字段建议：

- `ip: str`
- `local_reachable: bool`
- `cloud_reachable: bool`
- `last_probe_ts: int`

语义：设备连通性快照。

## 4.6 TransportCapabilityMatrix

字段建议：

- `play: list[str]`
- `tts: list[str]`
- `volume: list[str]`
- `stop: list[str]`
- `pause: list[str]`
- `probe: list[str]`

语义：设备事实能力（支持哪些 transport）。

---

## 5. 错误模型定义（core/errors）

## 5.1 ExpiredStreamError

触发条件：

- `DeliveryAdapter.prepare()` 检测 URL 过期或不可安全下发

捕获位置：

- `PlaybackCoordinator`

恢复策略：

1. Coordinator 捕获异常
2. 重新执行 `SourcePlugin.resolve()`
3. 重新执行 `DeliveryAdapter.prepare()`
4. 达到重试上限后返回失败

## 5.2 TransportError

触发条件：

- `Transport` 执行动作失败（网络错误、设备离线、鉴权失败等）

捕获位置：

- `TransportRouter`
- （最终上抛）`PlaybackCoordinator`

恢复策略：

- Router 在候选 transport 集合内 fallback
- 候选全部失败则返回统一错误结果

## 5.3 SourceResolveError

触发条件：

- `SourcePlugin.resolve()` 无法解析请求

捕获位置：

- `PlaybackCoordinator`

恢复策略：

- 按策略尝试备用插件（Phase 1 可不实现）
- 直接返回标准化错误给 API

---

## 6. SourcePlugin 接口骨架

必须：

- `resolve(request: MediaRequest) -> ResolvedMedia`

可选：

- `search(query: str, limit: int = 20) -> list[MediaItem]`
- `browse(path: str, page: int = 1, size: int = 50) -> list[MediaItem | Node]`

注册流程：

1. 初始化阶段创建插件实例
2. 调用 `SourceRegistry.register(plugin)`
3. Coordinator 根据 `source_hint` 或默认规则调用 `SourceRegistry.get_plugin()`

边界：

- 插件不返回 transport 偏好
- 插件不访问 TransportRouter

---

## 7. Transport 接口骨架

统一抽象方法：

- `play_url(...)`
- `stop(...)`
- `pause(...)`
- `tts(...)`
- `set_volume(...)`
- `probe(...)`

约束：

- Transport 只做“协议动作执行”
- Transport 不做业务编排
- Transport 不更新 `DeviceReachability`（由 DeviceRegistry 统一更新）

---

## 8. 最小播放链路（Phase 1）

链路：

```text
API
  -> PlaybackCoordinator
  -> SourceRegistry.get_plugin()
  -> SourcePlugin.resolve()
  -> DeliveryAdapter.prepare()
  -> TransportRouter.dispatch_play_url()
  -> Transport.play_url()
```

逐步输入输出：

1. API -> Coordinator
   - 输入：请求参数
   - 输出：`MediaRequest`
2. Coordinator -> SourceRegistry
   - 输入：`MediaRequest`
   - 输出：`SourcePlugin`
3. Coordinator -> SourcePlugin.resolve
   - 输入：`MediaRequest`
   - 输出：`ResolvedMedia`
4. Coordinator -> DeliveryAdapter.prepare
   - 输入：`ResolvedMedia`
   - 输出：`PreparedStream`
5. Coordinator -> TransportRouter.dispatch_play_url
   - 输入：`PreparedStream` + 设备能力矩阵 + policy
   - 输出：`TransportDispatchResult`
6. Router -> Transport.play_url
   - 输入：设备标识 + `PreparedStream`
   - 输出：`TransportResult`

---

## 9. 第一阶段实现范围

仅实现：

- `HttpUrlSourcePlugin`
- `MinaTransport` 适配层
- `MiioTransport` 适配层
- `PlaybackCoordinator` 最小播放流程
- `TransportRouter` 基于 policy 的路由与 fallback

暂不实现：

- Jellyfin / LocalFile 插件完整能力
- 搜索聚合
- 复杂缓存系统
- 高级观测与统计

---

## 10. 实施顺序建议（骨架级）

1. 建立 `core/` 目录和 DTO/错误类型
2. 落地 `SourcePlugin` / `Transport` 抽象
3. 落地 `SourceRegistry` / `DeviceRegistry`（内存实现）
4. 落地 `DeliveryAdapter.prepare()`
5. 落地 `TransportRouter` + `TransportPolicy`
6. 接入 `HttpUrlSourcePlugin`
7. 接入 Mina/Miio 适配层
8. 打通最小 API 播放链路

---

## 11. 验收标准

- Core 不依赖具体 Source
- Core 不依赖 Mina/Miio
- Source 不影响 Transport
- Transport 不包含业务逻辑
- SourceRegistry 不包含 resolve 业务
- DeliveryAdapter 不调用 SourcePlugin
- Router 路由仅在 capability 与 policy 的交集中执行
