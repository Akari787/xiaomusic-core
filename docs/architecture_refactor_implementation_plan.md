# Python 项目架构重构实施计划（可执行版）

本文给出一个面向工程实施的三阶段重构计划，目标覆盖：

1. 播放来源插件化
2. 引入 Transport Layer
3. 减少 Xiaomi Cloud 依赖
4. 引入 Device Registry
5. 引入统一播放模型

---

## 总体原则

- **先并行新架构，后切流量**：新老链路双轨运行，避免一次性替换。
- **API 兼容优先**：`/api/v1/*` 请求响应结构保持稳定（`ok/success/error_code/message`）。
- **可回退**：每阶段都保留开关回退到旧实现。
- **可观测先行**：每阶段都补指标、日志和契约测试。

建议开关：

- `XIAOMUSIC_ENABLE_CORE_V2`
- `XIAOMUSIC_ENABLE_SOURCE_PLUGIN`
- `XIAOMUSIC_ENABLE_TRANSPORT_ROUTER`
- `XIAOMUSIC_ENABLE_DEVICE_REGISTRY`
- `XIAOMUSIC_ENABLE_CLOUD_MIN_DEP`

---

## Phase 1：建骨架 + 双轨运行（低风险）

目标：引入新抽象，不改变对外行为；先打通最小主链路。

### 1) 修改哪些模块

- `xiaomusic/xiaomusic.py`
  - 增加新组件初始化入口（仅初始化，不默认接管全流量）
- `xiaomusic/api/routers/v1.py`
  - `play_url` 增加灰度分支：可选走 `CoreV2`
- `xiaomusic/auth.py`
  - 抽取 cloud 会话访问接口（适配 `CloudSessionManager`）

### 2) 新增哪些模块

- `xiaomusic/core_v2/`
  - `models.py`（统一模型：MediaItem/Playable/PlaySession/Device/DeviceCapability）
  - `coordinator.py`
  - `session_manager.py`
- `xiaomusic/sources/`
  - `base.py`（SourcePlugin 协议）
  - `registry.py`
  - `httpurl.py`（首个最小插件）
- `xiaomusic/transport/`
  - `contracts.py`（BaseTransport/TransportResult）
  - `mina.py`（薄封装旧 Mina 调用）
  - `router.py`（仅 play_url 路由）
- `xiaomusic/registry/`
  - `device_registry.py`（先做内存 + 快照加载）

### 3) 是否破坏兼容

- **不破坏兼容**。
- API 不变；仅内部增加灰度路径。
- 默认仍走旧链路，开启开关才走新链路。

### 4) 风险

- 新旧状态并存导致数据不一致（会话状态、错误码口径差异）
- Mina 薄封装错误映射不完整
- 插件抽象过早固定，后续扩展时接口调整成本

### 5) 验收标准

- `play_url` 在灰度模式可跑通（成功/失败路径完整）
- 失败可自动回退旧链路
- 新增契约测试通过：
  - source plugin resolve contract
  - transport contract（play_url）
  - play session state transition
- 关键指标可见：`core_v2_hit`, `core_v2_error`, `fallback_to_legacy_count`

---

## Phase 2：功能切换 + 云依赖削减（中风险）

目标：将主控制链路切到新架构，降低 cloud 高频调用。

### 1) 修改哪些模块

- `xiaomusic/api/routers/v1.py`
  - `stop/status/pause/set_volume/test_reachability` 接入 `TransportRouter`
- `xiaomusic/device_player.py`
  - 将设备控制调用改为走 transport 接口（保留旧逻辑兜底）
- `xiaomusic/music_library.py`
  - 对接 SourceResolver（本地/Jellyfin/HTTP）
- `xiaomusic/auth.py`
  - 认证刷新逻辑迁移到 `CloudSessionManager`，保留兼容外观

### 2) 新增哪些模块

- `xiaomusic/sources/`
  - `localfile.py`
  - `jellyfin.py`
- `xiaomusic/transport/`
  - `miio.py`
  - `health.py`（熔断/健康分）
- `xiaomusic/cloud/`
  - `session_manager.py`（token/refresh/backoff）
- `xiaomusic/cache/`
  - `metadata_cache.py`（L1/L2 + TTL）

### 3) 是否破坏兼容

- **原则上不破坏兼容**。
- 需要兼容配置映射：旧配置字段 -> 新模型字段。
- 返回字段保持原样；新增字段需可选。

### 4) 风险

- MiIO 设备能力差异大，命令支持不一致
- Device Registry 与真实设备状态漂移
- Cloud 调用降频后，某些边缘场景设备映射更新不及时

### 5) 验收标准

- 主要 API 全部可通过新链路执行（play/stop/pause/volume/status）
- 云调用频率显著下降（目标：`device_list` 从“高频”降至“启动+低频轮询”）
- Local-first 生效：`stop/pause/volume/tts` 优先 MiIO，失败再回 Mina
- Device Registry 命中率达标（建议 > 90%）
- 无严重兼容回归（现有 API 测试与手工 smoke 通过）

---

## Phase 3：收口清理 + 默认启用（中高风险）

目标：新架构成为默认路径，历史耦合代码收口。

### 1) 修改哪些模块

- `xiaomusic/xiaomusic.py`
  - 简化为 bootstrap/facade，去除业务聚合逻辑
- `xiaomusic/device_player.py`
  - 移除与来源解析/云控制耦合代码，保留必要适配
- `xiaomusic/services/online_music_service.py`
  - 将来源解析逻辑下沉至 source plugins
- `xiaomusic/api/*`
  - 统一错误码与响应口径（内部统一，外部兼容）

### 2) 新增哪些模块

- `xiaomusic/adapters/legacy_adapter.py`
  - 仅保留短期兼容层
- `docs/`
  - 新增插件开发指南、transport 扩展指南、设备能力维护指南

### 3) 是否破坏兼容

- **默认不破坏对外 API**。
- 可能破坏内部模块调用（第三方直接 import 内部旧类的场景）。
- 需发布迁移说明（deprecation 窗口）。

### 4) 风险

- 删除旧路径时可能丢失边缘行为
- 外部生态依赖内部实现（非公开接口）导致兼容投诉
- 新旧日志字段变化影响运维告警

### 5) 验收标准

- 新架构开关默认开启且稳定运行
- 旧链路可选保留一个小版本后下线
- 覆盖率达标（新增模块单测 + 关键集成测试）
- 文档齐全：
  - 架构文档
  - 插件规范
  - transport 规范
  - 运维排障手册

---

## 横向任务清单（所有阶段都要做）

1. **测试体系**
- 契约测试：SourcePlugin / BaseTransport / DeviceRegistry
- 组件测试：CloudSessionManager、metadata cache、router fallback
- 回归测试：`/api/v1/*` 兼容性

2. **可观测**
- 指标：`transport_success_rate`, `cloud_call_qps`, `fallback_count`, `offline_mode_duration`
- 结构化日志：`request_id/session_id/did/source/transport/error_code`

3. **发布策略**
- canary（小流量）-> 分批放量 -> 全量
- 每阶段有回滚开关与回滚脚本

4. **配置治理**
- 增加新配置默认值
- 旧配置兼容映射
- 不兼容项提前告警

---

## 里程碑建议（示例）

- M1（Phase 1 完成）：`play_url` 新链路灰度通过
- M2（Phase 2 完成）：核心控制命令切流，新旧兼容稳定
- M3（Phase 3 完成）：默认新架构，旧路径进入弃用窗口

---

## 交付物清单（按阶段验收）

- 代码：新模块 + 兼容适配 + 开关
- 测试：契约/组件/回归
- 文档：架构、开发、运维
- 发布：灰度记录、风险评估、回滚预案
