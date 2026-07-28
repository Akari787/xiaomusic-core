# xiaomusic-core 架构文档

> 入口文档 · 请先阅读本文档

## 项目简介

xiaomusic-core 是小米音箱的 Python 后端服务，提供音乐播放、设备控制、认证管理等功能。核心职责：
- **播放控制**：通过小米 API 控制音箱播放音乐
- **Source 抽象**：支持多种音乐来源（Jellyfin、本地音乐库、URL、站点媒体）
- **流媒体转发**：relay 模式支持网络音频流推送
- **认证管理**：处理小米账号认证和 token 刷新

---

## AI 开工协议

任何 AI 开始工作前，按以下顺序阅读：

1. **先判定改动边界**：读 `docs/architecture/system_overview.md` 的九大边界定义
2. **再按领域读对应文档**：
   - 架构层：读 `docs/architecture/README.md`
   - 行为规范：读 `docs/spec/README.md`
3. **涉及 API 必读**：`docs/api/api_v1_spec.md`
4. **涉及状态/生命周期/跨层依赖**：读 `docs/architecture/state-authority.md` 与 `docs/architecture/constraints.md`
5. **提交前必读**：`docs/architecture/constraints.md` 与 `docs/architecture/contributor_guide.md`

---

## 主要模块（九大边界）

| 边界 | 职责 | 入口文件 |
|---|---|---|
| **api** | 对外 HTTP 接口层（v1 Public API、Internal API） | `xiaomusic/api/` |
| **runtime** | 系统主协调对象，管理生命周期与依赖注入 | `xiaomusic/xiaomusic.py` |
| **playback** | 播放编排层，策略决策、队列管理、状态快照 | `xiaomusic/playback/`、`xiaomusic/core/` |
| **source** | 媒体来源解析，SourcePlugin.resolve() | `xiaomusic/adapters/sources/` |
| **device** | 设备抽象与命令执行（transport mina/miio） | `xiaomusic/device_player.py` |
| **auth** | 小米账号认证状态与会话维护 | `xiaomusic/auth.py` |
| **config** | 运行时配置对象管理与持久化 | `xiaomusic/config.py` |
| **relay** | 站内流媒体中转，relay session 与 `/relay/stream/{sid}` | `xiaomusic/relay/` |
| **webui** | 前端展示层，消费 Public API 与 Internal API | `xiaomusic/webui/` |

---

## 绝对不能碰的区域

以下区域在未充分理解前不要修改：

| 区域 | 原因 |
|---|---|
| `auth.py`（AuthManager） | 认证逻辑复杂，token 管理脆弱，BUG-007/008 未完全覆盖 |
| `device_player.py` | 播放状态机，Bug-011（stop 后切歌显示 switching）未修复 |
| `relay/runtime.py` | 承担 6 个职责领域，God Object 症状，Bug-009 反复调研 |
| `xiaomusic/xiaomusic.py` | 主应用对象，承担 9+ 职责，超级上帝对象 |

---

## 禁止越界规则

以下跨边界调用**严格禁止**：

- `source` 不得直接调用 `device`
- `webui` 不得依赖 `playback` / `runtime` 内部对象
- `api` 层不得直接读取设备底层状态绕过 `playback` 快照构建器
- `relay` 不得主动触发播放命令（只提供流服务）

**文档优先级**（冲突时按此裁决）：
1. `docs/api/api_v1_spec.md`
2. `docs/spec/*`
3. `docs/architecture/*`
4. `ARCHITECTURE.md`

---

## 开始工作前必须读

| 文档 | 内容 | 何时必读 |
|---|---|---|
| [系统宪法](./docs/architecture/constraints.md) | 禁止和必须清单 | 改任何边界前 |
| [状态权威](./docs/architecture/state-authority.md) | 每个状态的唯一权威归属 | 改状态字段前 |
| [贡献指南](./docs/architecture/contributor_guide.md) | 改动前置规则与文档更新约束 | 改任何模块前 |

---

## 当前已知技术债

### 高优先级

| ID | 问题 | 影响 |
|---|---|---|
| BUG-007 | auto runtime reload 未完全覆盖 | 认证降级场景可能失败 |
| BUG-008 | singleflight/fallback 未完全覆盖 | 并发认证恢复时可能冲突 |
| — | `SiteMediaSourcePlugin` 持有 `runtime_provider` | Source 插件化了但未完全解耦 |
| — | `RelayRuntime` 承担 6 个职责领域 | 维护性差，难以独立测试 |

### 中优先级

| ID | 问题 | 影响 |
|---|---|---|
| BUG-011 | stop 后 next/prev 长期显示 switching | 用户体验问题 |
| BUG-009 | playback restart 反复调研 | 播放中断恢复逻辑不稳定 |
| — | 播放列表双写（`_play_list` + `music_list`） | 数据可能不同步 |
| — | 异步任务 owner 不明确 | zombie task 风险 |
| — | 事件系统仅有 3 种事件 | 状态变化通知不完整 |

### 低优先级

| ID | 问题 | 影响 |
|---|---|---|
| BUG-003 | `disabled_plugins` 不持久化 | 重启后恢复 |
| BUG-004 | 浏览器自动化不稳定 | 测试不稳定 |
| — | v1.py 行数过多（1425行） | 维护性下降 |

---

## 文档入口

| 文档 | 说明 |
|---|---|
| [Architecture 文档地图](docs/architecture/README.md) | 架构文档索引 |
| [Spec 文档地图](docs/spec/README.md) | 规范文档索引：core、playback、auth、relay 规范（规范 > 架构） |
| [API v1 规范](docs/api/api_v1_spec.md) | v1 接口契约、白名单、错误模型、Class A/B/C 分级 |
| [ADR 目录](docs/adr/README.md) | 架构决策记录 |

## ADR 流程

架构决策记录（ADR）是变更架构约束的唯一方式。任何系统宪法中的约束变更，必须经过 ADR 流程：

1. 在 `docs/adr/` 下创建新 ADR 文件
2. 包含：**状态**（Proposed/Accepted/Rejected）、**上下文**、**决策**、**后果**
3. 如果是拒绝的决策，说明拒绝原因和替代方案
4. ADR 经讨论接受后，更新 `constraints.md` 和相关文档

---

*最后更新：2026-05-16 · 文档体系整理 v1.0*