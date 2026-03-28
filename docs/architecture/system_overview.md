# 系统总览（System Overview）

版本：v1.0
状态：正式架构文档
最后更新：2026-03-28

本文档是 xiaomusic-core 文档体系的总纲，定义系统一级边界、各模块职责与文档优先级关系。

---

## 1. 系统一级边界

系统由以下九个一级边界组成。每个边界有且只有一个核心职责，跨边界调用必须通过明确的接口层。

| 边界 | 核心职责 | 代码入口 |
|---|---|---|
| **api** | 对外 HTTP 接口层，承载 v1 Public API 与 Internal API | `xiaomusic/api/` |
| **runtime** | 系统主协调对象，管理各子系统生命周期与依赖注入 | `xiaomusic/xiaomusic.py` |
| **playback** | 播放编排层，负责策略决策、队列管理与状态快照输出 | `xiaomusic/playback/`、`xiaomusic/core/` |
| **source** | 媒体来源解析，提供可播放流 URL 与曲目事实 | `xiaomusic/adapters/sources/`、`xiaomusic/core/source/` |
| **device** | 设备抽象与设备侧命令执行 | `xiaomusic/device_manager.py`、`xiaomusic/device_player.py`、`xiaomusic/core/device/` |
| **auth** | 小米账号认证状态管理与会话维护 | `xiaomusic/auth.py`、`xiaomusic/security/token_store.py` |
| **config** | 运行时配置对象管理与持久化 | `xiaomusic/config.py`、`xiaomusic/config_manager.py` |
| **relay** | 站内流媒体中转，建立 relay session 并输出 `/relay/stream/{sid}` 端点 | `xiaomusic/relay/` |
| **webui** | 前端展示层，通过 Public API 与 Internal API 消费后端状态 | `xiaomusic/webui/` |

---

## 2. 边界调用规则

```
webui
  → Public API (/api/v1/*)        ← 所有外部调用方
  → Internal API (/api/auth/*, /api/file/*)  ← 仅 WebUI

api
  → playback（通过 facade）
  → runtime（通过 runtime_provider）
  → relay（只读查询）

playback
  → source（解析媒体）
  → device（下发命令）
  → relay（准备投递流）

runtime
  → auth
  → config
  → device
  → 音乐库（music_library）
  → 事件总线（event_bus）
```

**严格禁止的跨边界调用：**

- `source` 不得直接调用 `device`
- `webui` 不得依赖 `playback` / `runtime` 内部对象
- `api` 层不得直接读取设备底层状态绕过 `playback` 快照构建器
- `relay` 不得主动触发播放命令（只提供流服务）

---

## 3. 各边界当前状态

| 边界 | 边界稳定性 | 说明 |
|---|---|---|
| api | 稳定 | v1 白名单接口已形成正式契约，`api_v1_spec.md` 为权威 |
| playback | 稳定 | 状态快照、revision、SSE 推送已实现；`facade.py` 是唯一入口 |
| source | 稳定 | 四种来源已形成统一接口，`SourcePlugin.resolve()` 是唯一规范点 |
| device | 稳定 | 设备执行层边界清晰，transport（mina/miio）已抽象 |
| auth | 稳定 | 两层认证状态模型已明确，`auth.json` 是事实来源 |
| config | 稳定 | `Config` 对象是运行时单一配置入口 |
| relay | 稳定 | relay/proxy 语义已收口，`network_audio` 废弃术语已清除 |
| runtime | 过渡 | `XiaoMusic` 类仍承担部分协调职责，逐步向 playback/facade 下沉；一级边界定义不变 |
| webui | 过渡 | 前端已切换至 SSE 主通道 + `serverState` 消费模型；旧推测型逻辑已清除 |

"过渡"表示内部实现仍在收敛，但一级边界定义和对外接口契约不因此改变。

---

## 4. 文档优先级关系

当多份文档对同一事项有描述时，以下优先级从高到低生效：

```
1. docs/api/api_v1_spec.md
   ↳ v1 接口行为、字段、错误模型的最终权威

2. docs/spec/*
   ↳ 状态语义、SSE 协议、WebUI 状态机、auth 恢复等运行时行为规范

3. docs/architecture/*（含本文档）
   ↳ 系统结构、层级边界、模块归属决策

4. docs/development/*
   ↳ 开发流程、改动前置规则、文档更新约束

5. ARCHITECTURE.md
   ↳ 接口分层与调用关系的高层概述（与本文档互补，无冲突时均有效）

6. docs/dev/*（归档）
   ↳ 历史实施计划与验收报告，不作为当前实现依据
```

---

## 5. 新功能归属判断

新功能落地前，必须先判断归属边界：

- **涉及对外接口**：归 `api`，同步修改 `api_v1_spec.md`
- **涉及媒体来源解析**：归 `source`，实现 `SourcePlugin.resolve()`
- **涉及播放编排与状态**：归 `playback`，通过 `facade.py` 暴露
- **涉及设备命令执行**：归 `device`，通过 transport 接口下发
- **涉及中转流服务**：归 `relay`
- **涉及认证状态**：归 `auth`
- **涉及配置读写**：归 `config`
- **涉及前端展示**：归 `webui`，只能依赖 Public API / Internal API

无法明确归属时，必须先在架构文档中确认边界，再落代码。

---

## 6. 相关文档索引

| 文档 | 职责 |
|---|---|
| `ARCHITECTURE.md` | 接口分层与调用关系概述 |
| `docs/api/api_v1_spec.md` | v1 API 完整契约 |
| `docs/spec/player_state_projection_spec.md` | 播放状态快照语义规范 |
| `docs/spec/player_stream_sse_spec.md` | SSE 推送协议规范 |
| `docs/spec/runtime_specification.md` | core 层数据模型与错误体系 |
| `docs/spec/playback_coordinator_interface.md` | PlaybackCoordinator 接口约束 |
| `docs/authentication_architecture.md` | 认证系统两层状态模型 |
| `docs/architecture/runtime_architecture.md` | runtime 内部运转与生命周期 |
| `docs/architecture/source_architecture.md` | source 边界与插件体系 |
| `docs/architecture/webui_architecture.md` | WebUI 接口依赖边界 |
| `docs/development/contributor_guide.md` | 改动前置规则与文档更新约束 |
