# Relay 术语规范

> 版本：v1.0.9
> 生效日期：2026-03-21
> 适用范围：xiaomusic-core 全代码库与项目文档

---

## 1. 术语定义

| 正式术语 | 说明 |
|---|---|
| **relay** | 由 xiaomusic 进行中转输出的流媒体。当播放源无法被目标设备直连时，由服务端代理转发流量。 |
| **relay session** | 一次 relay 传输的会话实例。 |
| **relay session id**（sid） | relay session 的唯一标识，用于路由到对应会话。 |
| **delivery mode** | 流媒体投递方式，取值：`direct`（直连）、`proxy`（透明代理）、`relay`（中转）。 |
| **network source** | 广义的网络来源媒体，不等同于 relay。任何需要网络获取的媒体源均可称为 network source。 |

---

## 2. 废弃术语

**`network_audio`** 为废弃术语（deprecated term）。

- 本文档生效后（v1.0.9），不得在新增代码、文档、日志、路由命名中正式使用 `network_audio`。
- 兼容层若暂时保留旧名，必须标记为 `legacy alias`，不得扩散到主实现。

---

## 3. 新旧映射表

| 废弃名称 | 正式替代 |
|---|---|
| `network_audio` | `relay` |
| `NetworkAudioRuntime` | `RelayRuntime` |
| `/network_audio/stream/{sid}` | `/relay/stream/{sid}` |
| `network_audio.*`（Python 模块路径） | `relay.*` |

---

## 4. 约束原则

1. **命名边界**：relay 语义统一使用 `relay`/`Relay` 前缀，不使用 `network_audio`。
2. **会话标识**：relay session 的标识统一为 `sid`，不在其他语境中与 `session_id` 混用。
3. **兼容保留**：已存在的 `network_audio` 旧名代码作为 legacy alias 保留，但不再主动新增依赖。
4. **文档要求**：所有新增文档、日志、注释必须使用正式术语，旧名仅作为迁移提示保留。
