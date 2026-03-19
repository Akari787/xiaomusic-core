# xiaomusic-core 架构总览

本文档是项目架构的统一入口索引，描述各层职责、模块边界与文档导航。

---

## 1. 项目定位

`xiaomusic-core` 是 [hanxi/xiaomusic](https://github.com/hanxi/xiaomusic) 的独立维护分支，核心目标为：

- 稳定的认证运行时恢复（扫码一次、长期运行）
- 结构化 HTTP API（v1 契约，替代历史 `/cmd` 控制面）
- Jellyfin 媒体库联动
- 自托管部署友好

---

## 2. 分层架构概览

```
┌──────────────────────────────────────────┐
│              HTTP API 层                 │
│  xiaomusic/api/  (FastAPI 路由 / 模型)   │
└────────────────┬─────────────────────────┘
                 │ 调用
┌────────────────▼─────────────────────────┐
│              播放门面层                   │
│  xiaomusic/playback/facade.py            │
└────────────────┬─────────────────────────┘
                 │ 编排
┌────────────────▼─────────────────────────┐
│             播放核心层 (core)             │
│  Coordinator → Source → Delivery →       │
│  Transport                               │
│  xiaomusic/core/                         │
└──────┬────────────────────┬──────────────┘
       │ 来源适配            │ 传输适配
┌──────▼──────┐    ┌────────▼─────────────┐
│  adapters/  │    │ adapters/miio|mina/  │
│  sources/   │    │ (MiioTransport /     │
│ (4 类来源   │    │  MinaTransport)      │
│  插件)      │    └──────────────────────┘
└─────────────┘
       │
┌──────▼────────────────────────────────────┐
│         运行时支撑层                       │
│  network_audio/  设备 session / 流代理     │
│  managers/       JS 插件运行时             │
│  services/       在线音乐 / Jellyfin       │
│  device_manager / device_player  传统设备链│
└───────────────────────────────────────────┘
       │
┌──────▼────────────────────────────────────┐
│         基础能力层                         │
│  security/   认证 / outbound / 日志脱敏    │
│  utils/      文件 / 网络 / 音乐 / 文本工具 │
│  config/     配置加载 / schema / 持久化    │
│  constants/  API 字段常量                  │
│  providers/  在线音乐关键词工具            │
└───────────────────────────────────────────┘
```

---

## 3. 核心模块索引

### 3.1 API 层

| 路径 | 职责 |
|---|---|
| `xiaomusic/api/` | HTTP API 整体目录，详见 [README](xiaomusic/api/README.md) |
| `xiaomusic/api/routers/v1.py` | 正式结构化 API 入口（v1 白名单 20 个接口） |
| `xiaomusic/api/app.py` | FastAPI 应用装配 |
| `xiaomusic/api/dependencies.py` | 运行时依赖注入与权限校验 |
| `xiaomusic/api/response.py` | 统一响应 envelope |

正式 API 规范：[docs/api/api_v1_spec.md](docs/api/api_v1_spec.md)

### 3.2 播放门面层

| 路径 | 职责 |
|---|---|
| `xiaomusic/playback/` | 播放门面目录，详见 [README](xiaomusic/playback/README.md) |
| `xiaomusic/playback/facade.py` | 播放控制的正式门面入口（API 层首选调用点） |
| `xiaomusic/playback/link_strategy.py` | 直链 / 代理 / 外部链接播放策略 |

### 3.3 播放核心层

| 路径 | 职责 |
|---|---|
| `xiaomusic/core/` | 新播放核心层，详见 [README](xiaomusic/core/README.md) |
| `xiaomusic/core/coordinator/playback_coordinator.py` | 播放链路主编排器 |
| `xiaomusic/core/source/source_registry.py` | 来源插件注册与分发 |
| `xiaomusic/core/delivery/delivery_adapter.py` | ResolvedMedia → PreparedStream 转换 |
| `xiaomusic/core/transport/transport_router.py` | 传输路由（按 policy + capability 选 transport） |
| `xiaomusic/core/models/media.py` | 核心数据模型（MediaRequest / PlayOptions 等） |
| `xiaomusic/core/errors/` | 分层错误体系 |
| `xiaomusic/core/settings.py` | 环境变量配置（HTTP_AUTH_HASH / API_SECRET） |

运行时规范：[docs/spec/runtime_specification.md](docs/spec/runtime_specification.md)
编排接口：[docs/spec/playback_coordinator_interface.md](docs/spec/playback_coordinator_interface.md)

### 3.4 适配器层

| 路径 | 职责 |
|---|---|
| `xiaomusic/adapters/` | 所有具体协议与来源适配，详见 [README](xiaomusic/adapters/README.md) |
| `xiaomusic/adapters/sources/` | 4 类来源插件（direct_url / site_media / jellyfin / local_library） |
| `xiaomusic/adapters/miio/` | Miio 传输适配（stop / pause / tts / volume / probe） |
| `xiaomusic/adapters/mina/` | Mina 传输适配（play_url + 全量控制动作） |

### 3.5 网络音频子系统

| 路径 | 职责 |
|---|---|
| `xiaomusic/network_audio/` | 网络音频子系统，详见 [README](xiaomusic/network_audio/README.md) |
| `xiaomusic/network_audio/runtime.py` | 运行时聚合入口 |
| `xiaomusic/network_audio/session_manager.py` | Session 生命周期 |
| `xiaomusic/network_audio/resolver.py` | URL / 站点解析 |
| `xiaomusic/network_audio/audio_streamer.py` | 音频流代理 |

### 3.6 运行时服务层

| 路径 | 职责 |
|---|---|
| `xiaomusic/managers/` | 插件运行时管理，详见 [README](xiaomusic/managers/README.md) |
| `xiaomusic/managers/js_plugin_manager.py` | MusicFree JS 插件生命周期管理（正式入口） |
| `xiaomusic/services/` | 业务服务层，详见 [README](xiaomusic/services/README.md) |
| `xiaomusic/services/online_music_service.py` | 在线音乐搜索 / Jellyfin 联动（正式入口） |
| `xiaomusic/device_manager.py` | 设备实例字典 / 分组 / 生命周期（传统主链路） |
| `xiaomusic/device_player.py` | 单设备播放控制 / 定时器 / TTS（传统主链路） |

### 3.7 安全层

| 路径 | 职责 |
|---|---|
| `xiaomusic/security/` | 所有安全相关能力，详见 [README](xiaomusic/security/README.md) |
| `xiaomusic/security/token_store.py` | 认证 token 持久化 |
| `xiaomusic/security/outbound.py` | 出站请求策略（私网封锁 / allowlist） |
| `xiaomusic/security/exec_plugin.py` | exec 插件调用校验 |
| `xiaomusic/security/redaction.py` | 日志敏感字段脱敏 |
| `xiaomusic/security/tar_safe.py` | 安全 tar 解压 |
| `xiaomusic/security/errors.py` | 安全层错误体系 |

认证系统架构：[docs/authentication_architecture.md](docs/authentication_architecture.md)
认证运行时恢复规范：[docs/spec/auth_runtime_recovery.md](docs/spec/auth_runtime_recovery.md)

### 3.8 基础工具层

| 路径 | 职责 |
|---|---|
| `xiaomusic/utils/` | 通用工具，详见 [README](xiaomusic/utils/README.md) |
| `xiaomusic/utils/file_utils.py` | 文件与目录操作 |
| `xiaomusic/utils/network_utils.py` | 网络请求与下载 |
| `xiaomusic/utils/music_utils.py` | 音乐文件处理 |
| `xiaomusic/utils/system_utils.py` | 系统操作与环境 |
| `xiaomusic/utils/text_utils.py` | 文本处理与繁简转换 |
| `xiaomusic/utils/openai_utils.py` | AI 大模型调用工具 |

### 3.9 配置层

| 路径 | 职责 |
|---|---|
| `xiaomusic/config.py` | 运行态配置主对象（Config dataclass，含默认值与字段） |
| `xiaomusic/config_manager.py` | 配置文件加载 / 保存 / 原子写入 |
| `xiaomusic/config_model.py` | 部分字段 Pydantic 校验（安全 / 网络 / Jellyfin） |
| `xiaomusic/constants/api_fields.py` | API payload 字段名常量 |
| `xiaomusic/providers/online_music_keywords.py` | 在线音乐关键词解析工具 |

### 3.10 兼容层（已标记废弃，禁止新功能依赖）

| 路径 | 状态 | 迁移目标 |
|---|---|---|
| `xiaomusic/js_plugin_manager.py` | DEPRECATED | `xiaomusic/managers/js_plugin_manager.py` |
| `xiaomusic/online_music.py` | DEPRECATED | `xiaomusic/services/online_music_service.py` |

---

## 4. 播放链路主流程

```
POST /api/v1/play
  └─ routers/v1.py
       └─ PlaybackFacade.play(device_id, query, source_hint, options)
            └─ PlaybackCoordinator.play(MediaRequest)
                 ├─ SourceRegistry.get_plugin(source_hint)
                 │    └─ SourcePlugin.resolve(request) → ResolvedMedia
                 ├─ DeliveryAdapter.prepare_plan(resolved) → DeliveryPlan
                 └─ TransportRouter.dispatch(action="play", prepared)
                      ├─ MinaTransport.play_url(device_id, prepared)
                      └─ (fallback: MiioTransport)
```

---

## 5. 双轨并存说明

当前项目存在"新核心播放链"与"传统设备链"并行的结构：

- **新核心链**：`PlaybackFacade` → `PlaybackCoordinator` → `adapters/sources + transports`，服务于结构化 v1 API。
- **传统设备链**：`DeviceManager` + `XiaoMusicDevice`（`device_player.py`），是仍在运行的设备控制主链路。

两条链路在当前版本均为正式运行时，不应视 `core/` 为"传统链的替代品"——它是新播放架构的局部入口，两者通过 `DeviceRegistry._hydrate_from_legacy()` 桥接。

---

## 6. 关键文档导航

| 文档 | 说明 |
|---|---|
| [docs/api/api_v1_spec.md](docs/api/api_v1_spec.md) | 正式 HTTP API v1 规范（20 个接口白名单） |
| [docs/spec/runtime_specification.md](docs/spec/runtime_specification.md) | 运行时核心层技术规范（模型 / 错误 / transport 策略） |
| [docs/spec/playback_coordinator_interface.md](docs/spec/playback_coordinator_interface.md) | 播放编排器接口规范 |
| [docs/spec/auth_runtime_recovery.md](docs/spec/auth_runtime_recovery.md) | 认证运行时恢复规范 |
| [docs/authentication_architecture.md](docs/authentication_architecture.md) | 认证系统完整架构文档 |
| [docs/dev/runtime_contracts.md](docs/dev/runtime_contracts.md) | PlayOptions / facade.status() / 错误映射合同 |
| [docs/dev/source_transport_matrix.md](docs/dev/source_transport_matrix.md) | 来源 / 传输能力矩阵 |
| [docs/dev/compatibility_inventory.md](docs/dev/compatibility_inventory.md) | 兼容项台账与废弃计划 |
| [docs/dev/terminology.md](docs/dev/terminology.md) | 来源术语统一说明 |
| [docs/dev/implementation/](docs/dev/implementation/) | 分阶段实施计划与验收报告 |
| [docs/architecture/](docs/architecture/) | 重构阶段架构分析文档 |
