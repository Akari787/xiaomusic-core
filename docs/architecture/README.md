# Architecture 文档地图（Architecture Documentation Map）

> 目录用途：作为 architecture 目录的入口，提供文档分类索引
> 何时读取：理解系统架构、确认模块边界、进行跨模块改动前
> 权威级别：architecture 文档描述系统结构与边界约束，低于 spec 文档定义的行为/字段规范

---

## 当前权威架构文档

描述系统当前已确立的结构、边界与模块归属。是理解系统如何组织的首选参考。

可直接浏览的架构图：`docs/architecture/xiaomusic-core-current-architecture.html`

可编辑源文件：`docs/architecture/diagrams/xiaomusic-core-current-architecture.drawio`

| 文档 | 职责 |
|---|---|
| [system_overview.md](system_overview.md) | 系统一级边界（九大边界）、调用规则、文档优先级总纲 |
| [runtime_architecture.md](runtime_architecture.md) | Runtime 内部运转与生命周期管理 |
| [source_architecture.md](source_architecture.md) | Source 边界与插件体系 |
| [webui_architecture.md](webui_architecture.md) | WebUI 接口依赖边界与状态消费模型 |
| [authentication_architecture.md](authentication_architecture.md) | 认证系统两层状态模型（长期态/短期态） |
| [unified_playback_model.md](unified_playback_model.md) | 统一播放模型、来源接入、执行路径 |
| [playback-control-model.md](playback-control-model.md) | 播放队列、随机 session 与 next/previous 控制不变量 |
| [contributor_guide.md](contributor_guide.md) | 改动前置规则与文档更新约束 |

## 设计 / 治理文档

架构设计决策与系统治理约束。

| 文档 | 职责 |
|---|---|
| [state-authority.md](state-authority.md) | 状态权威偏差表、每个状态字段的唯一权威归属 |
| [event-model.md](event-model.md) | 统一事件模型设计、标准事件列表 |
| [correlation-id.md](correlation-id.md) | Correlation ID 设计（request_id、play_id、session_id） |
| [observability.md](observability.md) | Snapshot 端点设计与可观测性方案 |
| [constraints.md](constraints.md) | 系统宪法：禁止清单、必须清单 |
| [auth_runtime_recovery.md](auth_runtime_recovery.md) | 认证运行时恢复架构 |

## 相关入口

| 文档 | 说明 |
|---|---|
| [API v1 规范](../api/api_v1_spec.md) | v1 接口契约、白名单、错误模型 |
| [Spec 文档地图](../spec/README.md) | spec 文档索引（规范 > 架构） |
| [ADR 目录](../adr/README.md) | 架构决策记录 |
