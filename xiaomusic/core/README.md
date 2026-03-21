# 目录职责

本目录是项目的新播放核心层，定义通用播放编排架构：来源解析 → 流准备 → 传输投递的完整管道，以及配套的数据模型与错误体系。

# 主要内容

- 播放链路主编排器（`coordinator/`）
- 来源插件抽象与注册（`source/`）
- 流准备与代理决策（`delivery/`）
- 传输路由与策略（`transport/`）
- 设备注册表（`device/`）
- 核心数据模型（`models/`）
- 分层错误类型（`errors/`）
- 环境变量配置（`settings.py`）

# 子目录说明

## coordinator/

`PlaybackCoordinator` 是新播放链的主编排器，负责按顺序调用 source → delivery → transport，并处理 `ExpiredStreamError` 重试逻辑。

关键文件：`playback_coordinator.py`

## source/

`SourcePlugin` 是所有来源插件的抽象基类，`SourceRegistry` 负责注册与按 `source_hint` 分发。具体插件实现位于 `xiaomusic/adapters/sources/`，本目录只提供接口定义。

关键文件：`source_plugin.py`、`source_registry.py`

兼容说明：`SourceRegistry.LEGACY_HINT_MAP` 保留旧 hint（`http_url / local_music`）到新 hint 的映射，直到外部调用方迁移完成。`network_audio` hint 已移除。

## delivery/

`DeliveryAdapter` 将 `ResolvedMedia` 转换为 `PreparedStream`，执行 scheme 合法性校验、过期检测与代理决策（直链 vs 代理优先）。

关键文件：`delivery_adapter.py`

## transport/

`TransportRouter` 根据 `TransportPolicy`（action → transport 优先级列表）与 `TransportCapabilityMatrix`（设备能力）选择实际 transport 执行动作。

当前策略：
- `play`: mina
- `tts / volume / stop / pause / probe`: miio → fallback mina

具体 transport 实现（`MiioTransport` / `MinaTransport`）位于 `xiaomusic/adapters/`，本目录只定义接口与策略。

关键文件：`transport.py`（抽象基类）、`transport_policy.py`、`transport_router.py`

## device/

`DeviceRegistry` 维护设备 profile、可达性与传输能力矩阵的内存注册表，供 coordinator 查询。

说明：本目录不替代 `xiaomusic/device_manager.py`——`DeviceManager` 仍是传统设备生命周期的主入口，`DeviceRegistry` 只为新播放核心提供设备信息读取适配。

关键文件：`device_registry.py`

## models/

所有 core 层内部流转的数据类型：

| 类型 | 说明 |
|---|---|
| `MediaRequest` | 播放请求的统一入参 |
| `PlayOptions` | 播放选项（含 `from_payload()` 边界解析） |
| `ResolvedMedia` | 来源解析结果 |
| `PreparedStream` | 经投递适配器准备好的流 |
| `DeliveryPlan` | 主流 + 备选流组合 |
| `PlaybackOutcome` | 播放链路执行结果 |

关键文件：`media.py`、`payload_keys.py`、`device.py`、`transport.py`

## errors/

分层错误体系，避免 core 层抛出通用 `Exception`：

| 异常类 | 场景 |
|---|---|
| `CoreError` | 所有 core 层错误基类 |
| `InvalidRequestError` | 请求参数非法 |
| `SourceResolveError` | 来源插件无法解析 |
| `DeliveryPrepareError` | 流准备失败（基类） |
| `ExpiredStreamError` | 流 URL 已过期，coordinator 应重试 resolve |
| `UndeliverableStreamError` | 流 URL 结构不可投递，不应重试 |
| `TransportError` | 传输执行失败 |
| `DeviceNotFoundError` | 目标设备不在注册表中 |

关键文件：`errors/base.py`、`errors/source_errors.py`、`errors/stream_errors.py`、`errors/transport_errors.py`、`errors/device_errors.py`

# 不应该放什么

- HTTP 路由与请求解析（属于 `api/`）
- 具体传输协议实现（属于 `adapters/miio/` 或 `adapters/mina/`）
- 具体来源插件实现（属于 `adapters/sources/`）
- relay session / streamer 等子系统细节（属于 `relay/`）
- 设备生命周期管理（属于 `device_manager.py`）
- 配置持久化、插件管理（属于各自模块）

# 关键入口

- `xiaomusic/core/coordinator/playback_coordinator.py`：新播放链的主入口
- `xiaomusic/core/models/media.py`：核心模型定义
- `xiaomusic/core/errors/`：错误体系

# 与其他目录的关系

- **`xiaomusic/playback/`**：`PlaybackFacade` 是 core 的上游门面，API 层通过 facade 调用，不直接操作 coordinator。
- **`xiaomusic/adapters/`**：来源插件与传输适配的具体实现，core 只持有接口抽象。
- **`xiaomusic/device_manager.py`**：传统设备运行时，`DeviceRegistry` 通过 `_hydrate_from_legacy()` 从其获取设备信息，不替代它。
- **`xiaomusic/api/`**：API 层不应绕过 facade 直接访问 coordinator。

# 新增代码规则

- 新增模型字段时，`from_payload()` 的兼容解析逻辑只写在边界层，不向 coordinator 内部扩散。
- 新增错误类型时，遵循现有分层（`base → source/stream/transport/device`），不在 coordinator 内部直接 raise 通用异常。
- 新增 transport 或 source 时，实现放在 `adapters/`，core 只新增接口定义。
