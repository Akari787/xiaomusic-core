# Runtime 架构（Runtime Architecture）

版本：v1.0
状态：正式架构文档
最后更新：2026-03-28

本文档说明 runtime 在系统中的地位、内部构成、生命周期与和其他边界的关系。HTTP 接口规范见 `docs/api/api_v1_spec.md`，core 层技术规范见 `docs/spec/runtime_specification.md`。

---

## 1. Runtime 的职责边界

**runtime** 是系统的主协调对象，由 `XiaoMusic` 类（`xiaomusic/xiaomusic.py`）承载。其职责是：

- 创建并持有各子系统的实例（auth、config、device、music_library 等）
- 管理各子系统的启动顺序与依赖注入
- 响应配置变更、设备上下线等系统级事件
- 为 API 层提供运行时上下文（通过 `runtime_provider`）

runtime **不负责**：

- HTTP 路由（由 `api` 层负责）
- 媒体来源解析（由 `source` 层负责）
- 播放策略与队列编排（由 `playback` 层的 `facade.py` / `coordinator` 负责）
- 设备命令的具体执行（由 `device` 层负责）
- 状态快照构建（由 `playback/facade.py` 的 `build_player_state_snapshot()` 负责）

---

## 2. Runtime 与其他边界的关系

```
runtime (XiaoMusic)
    ├── 持有 → config (Config / ConfigManager)
    ├── 持有 → auth (AuthManager)
    ├── 持有 → device (DeviceManager → DevicePlayer × N)
    ├── 持有 → music_library (MusicLibrary)
    ├── 持有 → link_playback_strategy (LinkPlaybackStrategy)
    ├── 订阅 → event_bus (EventBus)
    └── 注入到 → api 层 (通过 runtime_provider / dependencies.py)

api 层
    └── 通过 facade (PlaybackFacade) 调用 playback 核心
        ├── facade 从 runtime_provider 获取 XiaoMusic 实例
        └── facade 构建 PlaybackCoordinator 执行播放链

relay
    └── 独立运行，runtime 提供配置，relay 不反向依赖 runtime
```

**关键约束：**

- `api` 层通过 `dependencies.py` 中的 `_get_facade()` / `_get_xiaomusic()` 访问 runtime，不直接 import `XiaoMusic` 实例
- `playback/facade.py` 是 api 层调用播放能力的唯一入口，不得绕过 facade 直接调用 coordinator 或 transport
- runtime 不直接响应 HTTP 请求，所有 HTTP 请求必须经过 `api` 层

---

## 3. Runtime 生命周期

### 3.1 启动阶段

```
1. 读取配置（ConfigManager.try_init_setting）
2. 初始化音乐库（MusicLibrary.gen_all_music_list）
3. 初始化设备管理器（DeviceManager）
4. 初始化认证管理器（AuthManager）
5. 注册事件监听（EventBus）
6. 启动 FastAPI 应用（包含 relay、API 路由）
7. auth_manager.init_all_data()  ← 建立小米账号连接
8. 启动 keepalive 循环（auth_manager.keepalive_loop）
9. 进入 ready 状态
```

### 3.2 Ready 状态

- 所有子系统正常运行
- API 层可正常响应请求
- SSE 状态推送正常工作

### 3.3 Degraded 状态

当以下任一情况发生时，系统进入降级状态，但不停止服务：

- 认证失效（`auth locked`）：设备相关 API 返回错误，其他 API 正常
- 设备离线：该设备的播放 API 返回 `E_DEVICE_NOT_FOUND`
- 音乐库刷新失败：使用上次成功的库数据

### 3.4 配置变更

通过 `EventBus` 的 `CONFIG_CHANGED` / `DEVICE_CONFIG_CHANGED` 事件触发各子系统的配置重载。配置变更不重启进程，只触发受影响子系统的局部刷新。

### 3.5 关闭

各子系统按启动的逆序清理资源（关闭设备连接、停止 keepalive、释放 relay session 等）。

---

## 4. 子系统关系

### 4.1 Config（配置）

- `Config` 对象是运行时唯一配置入口，所有子系统从中读取参数
- `ConfigManager` 负责从磁盘加载、持久化和热更新
- 不允许子系统直接读取环境变量或配置文件，必须通过 `Config` 对象

### 4.2 Auth（认证）

- `AuthManager` 管理小米账号的两层认证状态（长期态 + 短期态）
- `auth.json` 是事实来源，重启后从磁盘恢复
- 认证恢复流程见 `docs/spec/auth_runtime_recovery.md`

### 4.3 Device（设备）

- `DeviceManager` 管理所有设备的生命周期
- 每个设备由一个 `DevicePlayer` 实例表示，持有设备侧连接
- transport（mina/miio）由 `TransportRouter` 统一路由，不由 runtime 直接管理

### 4.4 MusicLibrary（音乐库）

- 持有本地音乐文件索引与歌单数据
- 是 `local_library` source 插件的数据来源
- 支持后台异步刷新，不阻塞播放

### 4.5 PlaybackFacade（播放门面）

- 不由 runtime 直接持有，由 api 层通过 `_get_facade()` 按需获取
- facade 通过 `runtime_provider` 获取 `XiaoMusic` 实例，间接使用 runtime 的子系统
- 状态快照构建（`build_player_state_snapshot`）由 facade 负责，runtime 不介入

---

## 5. Runtime 不负责什么

以下职责明确不在 runtime 边界内：

- **HTTP 路由**：由 `api/routers/` 负责
- **播放状态快照构建**：由 `playback/facade.py` 的 `build_player_state_snapshot()` 负责
- **revision / play_session_id 生成**：由 `playback/facade.py` 的 `_compute_revision()` 负责
- **SSE 事件推送**：由 `api/routers/v1.py` 的 `_push_player_state_event()` 负责
- **来源解析**：由 source 层各 `SourcePlugin` 负责
- **relay session 管理**：由 `relay/` 边界自管理

---

## 6. 相关文档

| 文档 | 职责 |
|---|---|
| `docs/spec/runtime_specification.md` | core 层数据模型、接口契约、错误体系的技术规范 |
| `docs/authentication_architecture.md` | 认证两层状态模型详细说明 |
| `docs/spec/auth_runtime_recovery.md` | 认证恢复流程规范 |
| `docs/architecture/system_overview.md` | runtime 在系统九个一级边界中的位置 |
| `docs/spec/playback_coordinator_interface.md` | PlaybackCoordinator 接口约束 |
