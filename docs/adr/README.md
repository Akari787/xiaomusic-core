# ADR（Architecture Decision Records）

> 目录用途：记录重要架构决策、上下文及后续状态
> 何时读取：引入新边界约束或修改架构规范时

ADR 是决策历史。只有 **Accepted 且未被撤回或取代** 的记录可以作为当前实现依据；Rejected / Withdrawn 仅用于解释历史。

## 状态约定

- Proposed：提案，尚未生效
- Accepted：已接受并生效
- Rejected：评审后拒绝
- Withdrawn：曾接受或计划实施，后续明确撤回
- Superseded：已被后续 ADR 取代

## 决策索引

| ADR | 状态 | 当前作用 |
|---|---|---|
| [0001 API 作为唯一正式边界](0001-api-boundary.md) | Accepted | WebUI / 外部调用通过 API |
| [0002 Runtime 职责边界](0002-runtime-ownership.md) | Accepted | runtime 生命周期与协调职责 |
| [0003 Source 抽象职责边界](0003-source-abstraction.md) | Accepted | Source 解析边界与注册规则 |
| [0004 状态权威单一化](0004-state-authority.md) | Accepted | 状态 ownership 原则 |
| [0005 统一事件模型](0005-event-model.md) | Withdrawn | 未实施设计的历史记录，不约束当前代码 |
| [0006 WebUI 播放请求与在线搜索引用边界](0006-webui-playback-contract.md) | Accepted | typed request 与 opaque play reference |
| [0007 音箱持有播放队列，服务端跟随](0007-speaker-owned-queue.md) | Proposed | 队列下发、稳定流地址、按拉流请求跟随当前曲目（分步实施中） |
| [0008 每台设备一个播放版本号](0008-playback-version.md) | Proposed | 把 sid / q / c / a / 切歌代际收敛为一个 version；受理与执行分开，修复自动切歌顶掉手动切歌的竞态 |

## 编写要求

文件名使用 `NNNN-<short-description>.md`。正文至少包含状态、上下文、决策和后果；状态变化时保留原记录并写清变化日期与依据。

## 相关文档

- [现行系统约束](../architecture/constraints.md)
- [系统总览](../architecture/system_overview.md)
