# xiaomusic-core 深度体检与长期演化执行方案 v2

项目：[xiaomusic-core](https://github.com/Akari787/xiaomusic-core)

---

# 一、目标

这次工作的目标不是：

* 修几个 bug
* 优化几个函数
* 重构几个文件

而是：

## 建立"长期可控的演化能力"

最终希望达到：

* 系统结构可理解
* 状态流可追踪
* 生命周期可管理
* API 边界稳定
* AI/Codex 不容易跑偏
* 重构风险可控
* 项目半年后仍能快速恢复上下文

---

# 二、当前阶段判断

项目已经明显进入：

```
长期演化系统（Long-lived System）
```

特征包括：

* API v1
* Source abstraction
* Runtime lifecycle
* SSE/WebUI 状态同步
* Auth restore
* 多来源播放
* React WebUI
* 自托管部署

此阶段最大的风险已经不是功能实现，而是**系统失控**。

---

# 三、核心战略

以后开发的核心原则：

## AI 只能在规则内工作

目标：

```
从"写代码"
转向
"管理系统演化"
```

---

# 四、Day 0：现状快照（执行前必须完成）

> **在任何审计开始之前**，先做这一步。它是所有后续工作的对照基准。

## 4.1 已知 Bug 清单

将现在已知的所有 bug 全部列出，**只记录，不修复**。

格式：

```markdown
| ID | 描述 | 复现路径 | 怀疑原因 | 首次发现时间 |
|----|------|----------|----------|--------------|
| B001 | ... | ... | ... | ... |
```

输出文档：`/docs/snapshot/known-bugs.md`

---

## 4.2 "不知道为什么能用"清单

记录哪些功能是"能用，但不理解原理"的区域。

格式：

```markdown
| 功能 | 现象 | 为什么不确定 |
|------|------|--------------|
| auth restore | 断线后有时自动恢复 | 不清楚触发条件 |
```

输出文档：`/docs/snapshot/mystery-working.md`

---

## 4.3 "不敢动"区域清单

记录哪些代码区域是"改了就容易出问题"的雷区。

格式：

```markdown
| 文件/模块 | 原因 | 上次出问题的场景 |
|-----------|------|-----------------|
| runtime.py | 改了播放状态就乱 | ... |
```

输出文档：`/docs/snapshot/danger-zones.md`

---

# 五、第一阶段：系统深度体检（必须优先完成）

预计时间：第 1 周

> **第一阶段禁止大规模重构。** 只允许：观察、记录、建模、标记问题。不要急着修。

---

## 5.1 Runtime 依赖图

建立：

```
入口
 → API
   → Service
     → Runtime
       → Player
         → Transport
           → Source
```

重点不是 import，而是：

* 谁持有状态
* 谁拥有生命周期
* 谁能跨层调用
* 谁能反向依赖（危险信号）

输出文档：`/docs/architecture/runtime-dependency.md`

---

## 5.2 状态流图

建立：

```
事件源
 → runtime state
   → player state
     → SSE
       → WebUI
```

重点检查：

* 是否存在多个权威状态源
* 是否存在 UI 猜状态
* 是否存在 runtime/player 状态漂移

输出文档：`/docs/architecture/state-flow.md`

---

## 5.3 生命周期审计

建立表格（**必须如实填写当前实际情况，而不是理想情况**）：

| 对象 | 创建者 | 持有者 | 销毁者 | 当前是否正确 | 风险备注 |
|------|--------|--------|--------|--------------|----------|
| runtime | ? | ? | ? | ? | |
| SSE stream | ? | ? | ? | ? | |
| auth task | ? | ? | ? | ? | |
| playback session | ? | ? | ? | ? | |

重点检查：

* 是否存在 zombie task（创建了无人销毁）
* 是否存在 ghost session（断线后残留）
* 是否存在无人管理的 async task

输出文档：`/docs/architecture/lifecycle.md`

---

# 六、第二阶段：边界与架构审计

预计时间：第 2 周

---

## 6.1 Source abstraction 审计

检查 Source 是否已经开始：

* 控制播放器
* 管理 auth
* 感知 WebUI
* 修改 runtime
* 管理 queue

如果存在，说明 abstraction 已经开始失效。

**危险信号（必须重点标记）：**

```python
if source == "xxx":
    ...

hasattr(source, "some_special_attr")
```

输出文档：`/docs/architecture/source-system.md`

---

## 6.2 Runtime God Object 审计

检查 runtime 是否开始承担：

* auth 管理
* player 管理
* queue 管理
* reconnect 管理
* event 管理
* source 管理

如果 runtime 变成全知全能核心对象，后期将极难维护。

输出文档：`/docs/architecture/runtime-boundary.md`

---

## 6.3 API 契约审计

检查：

* 是否存在旁路访问（WebUI 绕过 API 直接读内部状态）
* 是否存在未结构化错误返回
* 是否存在历史 API 漂移（同一接口行为已发生变化但未更新文档）
* 是否存在 WebUI 绕过 API 直接修改状态

目标：API 必须成为唯一正式边界。

输出文档：`/docs/architecture/api-contract.md`

---

# 七、第三阶段：状态权威系统审计

预计时间：第 2 周（与第二阶段并行）

> 这一阶段的核心是暴露"应该是谁负责"和"实际上是谁负责"之间的偏差。

---

## 理想状态权威表

| 状态 | 应有权威 | **当前实际权威** | 偏差说明 |
|------|----------|-----------------|----------|
| player state | runtime | ? | |
| queue | runtime | ? | |
| auth | auth manager | ? | |
| UI state | frontend store | ? | |
| playback session | player manager | ? | |
| SSE 连接状态 | event bridge | ? | |

**填写原则：** "当前实际权威"必须如实记录，哪怕答案是"runtime 和 player 都在改"。

其它层只能观察，不能偷偷修改——但首先要知道当前实际上是谁在改。

输出文档：`/docs/architecture/state-authority.md`

---

# 八、第四阶段：建立可观测性

预计时间：第 3 周

---

## 8.1 Event Timeline

建立统一事件系统，定义标准事件名称：

```
PLAY_REQUESTED
SOURCE_RESOLVED
PLAYER_CONNECTED
PLAYER_DISCONNECTED
STREAM_FAILED
AUTH_EXPIRED
AUTH_RESTORED
QUEUE_UPDATED
PLAYBACK_STARTED
PLAYBACK_STOPPED
```

**事件系统约定（必须提前确定，防止 AI 各自实现）：**

| 约定项 | 决策 |
|--------|------|
| 触发方式 | 同步 emit / 异步 emit（二选一，必须统一） |
| emit 失败处理 | 抛出 / 吞掉 / 记录日志（三选一） |
| event bus 位置 | 全局单例 / 注入依赖（二选一） |
| 事件 schema 变更 | 必须走 ADR，禁止静默修改 |

输出文档：`/docs/architecture/event-model.md`

---

## 8.2 Correlation ID

建立追踪 ID 体系：

```
request_id    → API 请求粒度
play_id       → 一次播放会话粒度
session_id    → SSE 连接粒度
```

用于：bug 追踪、日志关联、生命周期追踪。

---

## 8.3 State Snapshot（runtime dump）

实现 `GET /debug/snapshot` 或命令行触发，导出：

* runtime 状态
* queue 状态
* auth 状态
* player 状态
* source 状态
* 当前 session

输出文档：`/docs/architecture/observability.md`

---

# 九、第五阶段：建立 AI 开发约束系统

预计时间：第 4 周

> 这是未来长期稳定性的核心。

---

## 9.1 ADR（Architecture Decision Record）

建立：`/docs/adr/`

示例：

```
0001-api-boundary.md
0002-runtime-ownership.md
0003-source-abstraction.md
0004-state-authority.md
0005-event-model.md
```

ADR 只记录：**为什么这样设计**，而不是代码怎么写。

**ADR 触发条件（必须强制执行）：**

凡涉及以下变更，必须先有对应 ADR，否则 block merge：

* 修改 API contract
* 修改 runtime ownership
* 修改 event schema
* 修改状态权威归属
* 新增跨层依赖

---

## 9.2 系统宪法（最重要）

创建：`/docs/architecture/constraints.md`

### 禁止

```markdown
- WebUI 直接访问 runtime 内部状态
- Source 持有 runtime 引用
- runtime 特判 source 类型（禁止 if source == "xxx"）
- API 返回非结构化错误
- 绕过 event system 直接修改状态
- 无标注的 silent except（禁止裸 except: pass）
- 未经 ADR 修改 API contract / event schema
```

### 必须

```markdown
- 所有状态变化通过 event system 通知
- 所有 async task 可取消，且有明确 owner
- 所有状态有唯一权威，并在 state-authority.md 中登记
- 所有生命周期明确 ownership（创建/持有/销毁）
- 所有临时方案必须标注 TEMP-HACK（见下文规范）
```

---

## 9.3 TEMP-HACK 治理规范

所有临时方案必须按以下格式标注，禁止裸 `except: pass`：

```python
# TEMP-HACK:
# reason: 解释为什么这样写
# remove_after: v1.x / 某个功能完成后 / 某个 bug 修完后
# related_issue: #123
# owner: 谁负责跟进
try:
    ...
except SomeError:
    pass  # 不允许不写原因
```

定期扫描 `TEMP-HACK` 标注，作为技术债清单。

输出文档：`/docs/architecture/constraints.md`

---

## 9.4 AI Review Checklist

创建：`/docs/ai-review-checklist.md`

```markdown
# AI PR Checklist

在提交任何 AI 生成的 PR 之前，逐项确认：

## 边界检查
- [ ] 是否破坏 API contract？
- [ ] 是否新增 WebUI 绕过 API 直接访问 runtime？
- [ ] 是否新增双向依赖？

## 所有权检查
- [ ] 是否新增 runtime god object 行为（runtime 承担了新的职责）？
- [ ] 是否修改了某状态的权威归属（未更新 state-authority.md）？
- [ ] 是否引入隐藏生命周期（async task 无明确 owner）？

## 事件系统检查
- [ ] 是否绕过 event system 直接修改状态？
- [ ] 是否修改了 event schema（未走 ADR）？

## Source 系统检查
- [ ] 是否新增 source-specific hack（if source == "xxx"）？
- [ ] 是否新增 hasattr 特判？

## 技术债检查
- [ ] 是否新增无标注的 silent except？
- [ ] 是否新增状态双写（两处同时维护同一状态）？
```

---

## 9.5 Codex 上下文限制

禁止全仓库自由修改。推荐按模块限制：

```markdown
允许修改：
- source/*
- runtime/auth/*
- tests/auth/*

禁止修改（需要人工审核）：
- API contract 定义文件
- event schema 定义文件
- runtime 核心状态管理
```

---

# 十、第六阶段：事件驱动模型迁移

逐渐减少：

```python
obj.xxx = xxx  # 直接赋值
```

改为：

```python
emit(Event(type="STATE_CHANGED", payload={...}))
```

迁移优先级：先改最容易漂移的状态，不要一次全改。

---

# 十一、第七阶段：Ownership Discipline

每个模块只能有一个 owner：

| 模块 | Owner |
|------|-------|
| runtime state | runtime manager |
| auth restore | auth manager |
| source resolve | source manager |
| playback lifecycle | player manager |
| SSE bridge | event bridge |

禁止：跨 ownership 直接改内部状态。

---

# 十二、第八阶段：重构执行（第 5 周后才开始）

> 前四周不做这一步。

---

## 12.1 Runtime 拆分

**拆分前提：** 已完成 runtime-boundary.md，清楚知道哪些职责需要剥离。

**拆分策略：**

1. 先用接口隔离（不改实现，先定义接口）
2. 通过接口逐步迁移实现
3. 每次只移动一个职责，单独测试

---

## 12.2 Source 插件化

**插件化前提：** 已完成 source-system.md，知道所有 source-specific hack 的位置。

**插件化策略：**

1. 定义标准 Source 接口（必须能覆盖所有现有 source）
2. 逐个将现有 source 迁移到接口实现
3. 最后清除所有 `if source == "xxx"` 逻辑

---

## 12.3 WebUI 状态收敛

**收敛前提：** 已完成 state-flow.md 和 state-authority.md。

**收敛策略：**

1. 确定 frontend store 为 UI 状态唯一权威
2. 所有 WebUI 读状态改为订阅 SSE event
3. 禁止 WebUI 直接 fetch runtime 内部状态

---

# 十三、面向 AI 的项目入口文档

> 每次让 AI 开始工作之前，必须先给它读这个文件。

创建：`/ARCHITECTURE.md`（仓库根目录）

```markdown
# xiaomusic-core 架构概览

## 这个项目是什么
（一段话描述）

## 主要模块
| 模块 | 职责 | 入口文件 |
|------|------|----------|
| runtime | ... | ... |
| source | ... | ... |
| player | ... | ... |
| auth | ... | ... |
| API | ... | ... |

## 绝对不能碰的区域
- （列出 danger-zones.md 的摘要）

## 开始工作前必须读的文档
1. /docs/architecture/constraints.md（系统宪法）
2. /docs/architecture/state-authority.md（状态权威）
3. /docs/ai-review-checklist.md（提交前检查）

## 当前已知的技术债
- （列出 known-bugs.md 的摘要）
```

---

# 十四、推荐目录结构

```
/ARCHITECTURE.md                        ← AI 工作入口，每次必读

/docs
  /snapshot                             ← Day 0 现状快照
    known-bugs.md
    mystery-working.md
    danger-zones.md

  /adr                                  ← 架构决策记录
    0001-api-boundary.md
    0002-runtime-ownership.md
    0003-source-abstraction.md
    0004-state-authority.md
    0005-event-model.md

  /architecture                         ← 架构文档
    runtime-dependency.md
    state-flow.md
    lifecycle.md
    source-system.md
    runtime-boundary.md
    api-contract.md
    state-authority.md
    event-model.md
    constraints.md
    observability.md

  ai-review-checklist.md
```

---

# 十五、执行顺序总览

| 时间 | 工作内容 | 禁止事项 |
|------|----------|----------|
| Day 0 | 现状快照（bug 清单、迷之能用清单、雷区清单） | 禁止改代码 |
| 第 1 周 | Runtime 依赖图、状态流图、生命周期表 | 禁止大规模重构 |
| 第 2 周 | Source 审计、Runtime God Object 审计、API 契约审计、状态权威偏差表 | 禁止大规模重构 |
| 第 3 周 | Event Timeline（含约定）、Correlation ID、State Snapshot | 可小范围加日志 |
| 第 4 周 | ADR、constraints、AI review checklist、ARCHITECTURE.md | 可修 TEMP-HACK 标注 |
| 第 5 周+ | 真正重构：Runtime 拆分、Source 插件化、WebUI 状态收敛 | 按模块隔离推进 |

---

# 十六、最终目标

## 系统可解释

任何模块，以下问题都能快速回答：

* 为什么存在
* 为什么这样设计
* 谁负责它
* 谁能修改它

## 状态可追踪

任何播放问题，以下问题都能追踪：

* 发生了什么
* 在哪一步失败
* 哪个状态漂移了
* 哪个 task 没有被销毁

## AI 可控

Codex 在规则内开发，而不是自由发挥。每次 PR 都经过 AI Review Checklist 过滤。

## 重构可执行

未来以下演化方向都能继续推进而不失控：

* Runtime 拆分
* Source 插件化
* SDK 提取
* AI Agent integration
* 多设备协同

---

## 最终结果

从：

```
"功能不断增长"
```

转向：

## "系统持续演化而不失控"
