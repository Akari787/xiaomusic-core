# Architecture 文档地图（Architecture Documentation Map）

> 目录用途：作为 architecture 目录的入口，提供文档分类索引
> 何时读取：理解系统架构、确认模块边界、进行跨模块改动前
> 权威级别：architecture 文档描述系统结构与边界约束，低于 spec 文档定义的行为/字段规范

---

## 文档分类

本文档将 architecture 目录下的文档分为三类：当前权威文档、深度体检/审计文档、设计/治理文档。

### 第一类：当前权威架构文档

描述系统当前已确立的结构、边界与模块归属。是理解系统如何组织的首选参考。

| 文档 | 职责 |
|---|---|
| [system_overview.md](system_overview.md) | 系统一级边界（九大边界）、调用规则、文档优先级总纲 |
| [runtime_architecture.md](runtime_architecture.md) | Runtime 内部运转与生命周期管理 |
| [source_architecture.md](source_architecture.md) | Source 边界与插件体系 |
| [webui_architecture.md](webui_architecture.md) | WebUI 接口依赖边界与状态消费模型 |
| [authentication_architecture.md](authentication_architecture.md) | 认证系统两层状态模型（长期态/短期态） |
| [unified_playback_model.md](unified_playback_model.md) | 统一播放模型、来源接入、执行路径 |
| [contributor_guide.md](contributor_guide.md) | 改动前置规则与文档更新约束 |

### 第二类：深度体检/审计文档

深度调研与风险审计产出，用于理解当前系统的已知风险、技术债与边界症状。

**用途说明**：审计文档用于理解当前风险，描述系统现状与已发现的问题，不覆盖正式架构文档的约束定义。当审计文档与正式架构文档存在冲突时，以 `system_overview.md` 的优先级裁决。

| 文档 | 职责 |
|---|---|
| [runtime-dependency.md](runtime-dependency.md) | 运行时依赖分析、调用链图 |
| [state-flow.md](state-flow.md) | 状态流分析、状态变量表 |
| [lifecycle.md](lifecycle.md) | 生命周期（async task、资源创建/销毁） |
| [source-system.md](source-system.md) | Source 系统审计、插件化障碍点 |
| [runtime-boundary.md](runtime-boundary.md) | Runtime 边界审计、God Object 症状 |
| [api-contract.md](api-contract.md) | API 路由审计、错误格式一致性 |
| [state-authority.md](state-authority.md) | 状态权威偏差表、质量门禁判断 |

### 第三类：设计/治理文档

架构设计决策与系统治理约束，包含 ADR 流程约束与禁止/必须清单。

| 文档 | 职责 |
|---|---|
| [event-model.md](event-model.md) | 统一事件模型设计、标准事件列表 |
| [correlation-id.md](correlation-id.md) | Correlation ID 设计（request_id、play_id、session_id） |
| [observability.md](observability.md) | Snapshot 端点设计与可观测性方案 |
| [constraints.md](constraints.md) | 系统宪法：禁止清单、必须清单 |
| [../adr/README.md](../adr/README.md) | ADR（Architecture Decision Records）目录 |
| [../ai-review-checklist.md](../ai-review-checklist.md) | 提交前 AI Review Checklist |

---

## 文档优先级（冲突裁决）

当多份文档对同一事项有描述时，按以下优先级裁决：

```
1. docs/api/api_v1_spec.md          ← v1 接口契约最终权威
2. docs/spec/*                      ← 状态语义、SSE 协议、auth 恢复等运行时行为规范
3. docs/architecture/*              ← 系统结构、模块归属
   └─ system_overview.md            ← architecture 内优先级最高
4. ARCHITECTURE.md                  ← 高层概述（与 system_overview 互补）
5. docs/archive/*                   ← 历史归档，不作为当前实现依据
```

---

## 相关入口

| 文档 | 说明 |
|---|---|
| [API v1 规范](../api/api_v1_spec.md) | v1 接口契约、白名单、错误模型 |
| [Spec 文档地图](../spec/README.md) | spec 文档索引（规范 > 架构） |
| [ADR 目录](../adr/README.md) | 架构决策记录 |
| [AI Review Checklist](../ai-review-checklist.md) | 提交前检查清单 |