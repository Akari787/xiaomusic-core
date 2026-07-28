# Spec 文档地图（Specifications Documentation Map）

> 目录用途：作为 spec 目录的入口，提供文档分类索引
> 何时读取：理解运行时行为规范、字段定义、接口契约细节
> 权威级别：**spec 文档定义行为/字段规范，权威级别高于 architecture 文档**

---

## 规范与架构的关系

- **spec** 文档：定义"系统应该做什么、如何做"，包括行为约束、字段语义、接口契约、协议细节
- **architecture** 文档：描述"系统是如何组织的"，包括模块边界、调用关系、层级结构

**规范 > 架构**：当 spec 文档与 architecture 文档对同一事项的描述存在冲突时，以 spec 文档为准。

---

## 文档分类

### Core / Runtime 规范

描述 core 层数据模型、错误体系、Source/Transport 接口等运行时基础规范。

| 文档 | 职责 |
|---|---|
| [runtime_specification.md](runtime_specification.md) | core 层数据模型、错误体系、Source/Transport 接口定义 |

### Playback 状态与 SSE

描述播放状态快照语义、SSE 推送协议、前端状态机映射等播放相关规范。

| 文档 | 职责 |
|---|---|
| [player_state_projection_spec.md](player_state_projection_spec.md) | 权威播放状态快照的字段模型、语义与消费约束 |
| [player_stream_sse_spec.md](player_stream_sse_spec.md) | SSE 推送协议细节：连接建立、事件格式、心跳、重连 |
| [webui_playback_state_machine_spec.md](webui_playback_state_machine_spec.md) | 前端消费型状态机定义（WebUI 状态 → 播放状态映射） |
| [playback/README.md](playback/README.md) | playback spec 子目录索引 |

### Auth 规范

描述认证状态管理、恢复链路、状态机语义等认证相关规范。

| 文档 | 职责 |
|---|---|
| [auth/README.md](auth/README.md) | auth spec 子目录索引 |
| [auth/auth_runtime_recovery.md](auth/auth_runtime_recovery.md) | 认证恢复行为规范（恢复链路、状态映射、阶段边界） |
| [auth/auth_runtime_reload_recovery_path.md](auth/auth_runtime_reload_recovery_path.md) | `_try_login()` / runtime reload 的 login、verify、runtime swap 阶段定义 |
| [auth/auth_recovery_state_machine.md](auth/auth_recovery_state_machine.md) | 恢复流程状态机详细说明 |
| [auth/auth_recovery_entrypoint_unification.md](auth/auth_recovery_entrypoint_unification.md) | 恢复入口统一方案 |
| [auth/auth_recovery_singleflight.md](auth/auth_recovery_singleflight.md) | 并发恢复互斥方案 |
| [auth/auth_recovery_fallback_path.md](auth/auth_recovery_fallback_path.md) | 降级路径方案 |
| [auth/auth_auto_runtime_reload_acceptance.md](auth/auth_auto_runtime_reload_acceptance.md) | 自动重载验收标准 |

### Relay 术语

描述 relay/proxy/delivery mode 等术语定义与语义约束。

| 文档 | 职责 |
|---|---|
| [relay_terminology.md](relay_terminology.md) | relay/proxy/delivery mode 术语定义与语义澄清 |

---

## 文档优先级（冲突裁决）

当多份文档对同一事项有描述时，按以下优先级裁决：

```
1. docs/api/api_v1_spec.md          ← v1 接口契约最终权威
2. docs/spec/*                     ← 状态语义、SSE 协议、auth 恢复等运行时行为规范
3. docs/architecture/*             ← 系统结构、模块归属
   └─ system_overview.md           ← architecture 内优先级最高
4. ARCHITECTURE.md                 ← 高层概述（与 system_overview 互补）
```

---

## 相关入口

| 文档 | 说明 |
|---|---|
| [Architecture 文档地图](../architecture/README.md) | architecture 文档索引 |
| [API v1 规范](../api/api_v1_spec.md) | v1 接口契约、白名单、错误模型 |
| [系统宪法](../architecture/constraints.md) | 禁止清单、必须清单 |
| [ADR 目录](../adr/README.md) | 架构决策记录 |
