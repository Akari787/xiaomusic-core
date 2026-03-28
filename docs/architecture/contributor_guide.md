# 开发者指南（Contributor Guide）

版本：v1.0
状态：正式开发规则
最后更新：2026-03-28

本文档是所有开发改动（包括 AI 执行）的前置规则入口。**在改任何代码之前，必须先确认归属边界并阅读对应文档。**

---

## 1. 改动前置规则总表

| 改动类型 | 改动前必须阅读 | 改动后必须同步更新 |
|---|---|---|
| 改 API（新增/修改接口） | `docs/api/api_v1_spec.md`、`docs/architecture/system_overview.md` | `docs/api/api_v1_spec.md` |
| 改播放状态字段或语义 | `docs/spec/player_state_projection_spec.md`、`docs/api/api_v1_spec.md` | `docs/spec/player_state_projection_spec.md` |
| 改 SSE 推送行为 | `docs/spec/player_stream_sse_spec.md`、`docs/spec/player_state_projection_spec.md` | `docs/spec/player_stream_sse_spec.md` |
| 改 runtime / XiaoMusic 类 | `docs/architecture/runtime_architecture.md`、`docs/spec/runtime_specification.md` | 视改动范围更新对应规范 |
| 改 playback / facade / coordinator | `docs/spec/playback_coordinator_interface.md`、`docs/spec/player_state_projection_spec.md` | 视改动范围更新对应规范 |
| 改 auth 相关 | `docs/authentication_architecture.md`、`docs/spec/auth_runtime_recovery.md` | 视改动范围更新对应规范 |
| 改 source / 新增 source 插件 | `docs/architecture/source_architecture.md`、`docs/spec/runtime_specification.md` | `docs/architecture/source_architecture.md`（若新增来源类型） |
| 改 WebUI 状态消费逻辑 | `docs/spec/webui_playback_state_machine_spec.md`、`docs/spec/player_state_projection_spec.md` | `docs/spec/webui_playback_state_machine_mapping.md`（若改了实现结构） |
| 改 WebUI 接口依赖 | `docs/architecture/webui_architecture.md`、`docs/api/api_v1_spec.md` | `docs/architecture/webui_architecture.md` |
| 新增任意模块 | `docs/architecture/system_overview.md`（确认归属边界） | 视归属边界更新对应架构文档 |

---

## 2. 改 API 前

必须确认：

1. 该接口是否已在 `api_v1_spec.md` 白名单中？若否，评估是否满足 v1 准入标准（第 3.5 节）。不满足准入标准的接口不得进入 v1，应归为 Internal API 或不新增。
2. 该接口属于 Class A / B / C 中的哪类？Class A 必须进入统一调度链路，成功响应必须含 `transport`；Class B 本地控制路径，不得假设 `transport`；Class C 只读查询，提供统一 envelope 与结构化错误。
3. 成功响应字段是否符合对应 Class 的契约矩阵？
4. 错误响应是否使用统一 envelope 加合法 `stage` 枚举值？

改动后必须更新 `api_v1_spec.md`，包括接口白名单、归属总表、逐项契约。

---

## 3. 改 Runtime 前

必须确认：

1. 该改动是否涉及子系统的启动顺序或依赖注入？若是，核查 `runtime_architecture.md` 的生命周期说明。
2. 该改动是否影响 `api` 层通过 `runtime_provider` 访问 runtime 的方式？若是，需同步修改 `dependencies.py` 的注入点。
3. 该改动是否将原本属于 `playback` 的职责（如状态快照构建、revision 生成）移入了 runtime？这是禁止的。

runtime 过渡期内，`XiaoMusic` 类仍承担部分协调职责，改动前需评估该职责最终应下沉到哪个边界。

---

## 4. 改 Playback 前

必须确认：

1. 改动是否涉及 `build_player_state_snapshot()`？若是，必须同步阅读 `player_state_projection_spec.md`，确保快照输出符合全部字段规范。
2. 改动是否影响 `revision` 递增逻辑或 `play_session_id` 切换逻辑？这两者的变化会直接影响前端状态消费，必须谨慎。
3. `PlaybackFacade` 是 api 层调用播放能力的唯一入口，任何新的播放能力必须通过 facade 暴露，不得在 router 中直接调用 coordinator 或 transport。

---

## 5. 改 Auth 前

必须确认：

1. 该改动是否影响两层认证状态的边界（长期态 / 短期态）？见 `authentication_architecture.md`。
2. `auth.json` 是事实来源，任何认证状态的持久化必须通过 `TokenStore`，不得绕过。
3. 认证恢复流程有固定步骤，改动前必须阅读 `auth_runtime_recovery.md`。

---

## 6. 改 Source / Plugin 前

必须确认：

1. 该改动是新增 source 类型，还是修改已有 source 内部实现？新增 source 类型必须在 `source_architecture.md` 确认规范，并更新 `api_v1_spec.md` 的 `source_hint` 允许值。修改已有 source 内部实现时，必须保持 `resolve()` 的返回类型不变。
2. 新增的是 Source Plugin（正式体系）还是 MusicFree JS 插件（`site_media` 内部机制）？两者归属不同，见 `source_architecture.md` 第 5 节。
3. source 实现中是否出现了对 device、transport 或播放队列的直接调用？这是禁止的，必须移除。

---

## 7. 改 WebUI 前

必须确认：

1. 该改动是否需要新的后端字段？若是，该字段必须先在 `api_v1_spec.md` 中声明，后端实现，最后前端消费——不得反向依赖未声明字段。
2. 该改动是否引入了对播放状态的本地推算或补偿逻辑？这是禁止的，见 `webui_playback_state_machine_spec.md` 第 4 章。
3. 歌单选中项同步是否仍基于 `track.id` 对照？不得回退为 `title` 文本匹配。
4. 该改动是否在 SSE 连接正常时引入了并行轮询？这是禁止的。
5. 改动是否读取了 `cur_music`、`is_playing`、`offset`、`duration` 等已废弃的兼容字段？这是禁止的。

---

## 8. 新增模块的约束

新增任何模块（文件、包、子系统）前，必须完成：

1. **确认归属边界**：在 `system_overview.md` 的九个一级边界中找到其归属，或判断是否需要新增边界（新增边界需同步更新 `system_overview.md`）。
2. **确认上游调用方**：该模块由谁调用？通过什么接口？
3. **确认下游依赖**：该模块依赖谁？是否跨越了边界约束？
4. **确认不越界**：对照 `system_overview.md` 第 2 节的禁止调用规则。

不允许边界不明直接落代码。如果不确定归属，先在架构文档中讨论确认，再写实现。

---

## 9. 文档更新规则

以下改动**必须**同步更新对应文档，不允许代码与文档脱节：

| 改动类型 | 必须更新的文档 |
|---|---|
| 新增 v1 接口或修改接口契约 | `docs/api/api_v1_spec.md` |
| 修改播放状态快照字段或语义 | `docs/spec/player_state_projection_spec.md` |
| 修改 SSE 事件格式或推送规则 | `docs/spec/player_stream_sse_spec.md` |
| 修改 source 类型集合 | `docs/architecture/source_architecture.md` |
| 修改 WebUI 接口依赖 | `docs/architecture/webui_architecture.md` |
| 修改认证恢复流程 | `docs/spec/auth_runtime_recovery.md` |
| 修改系统一级边界 | `docs/architecture/system_overview.md` |

以下改动**不需要**更新规范文档（属于内部实现细节）：

- source 插件内部解析逻辑调整（接口不变）
- transport 内部重试策略调整
- 日志格式调整
- 测试文件新增

---

## 10. 文档冲突裁决

当不同文档对同一事项有不同描述时，按以下优先级裁决（从高到低）：

1. `docs/api/api_v1_spec.md`
2. `docs/spec/*`
3. `docs/architecture/*`
4. `docs/development/*`
5. `ARCHITECTURE.md`
6. `docs/dev/*`（历史归档，不作为当前依据）

发现文档冲突时，以高优先级文档为准，并将低优先级文档中的冲突内容标记为待修正，在下一轮文档更新中处理。

---

## 11. AI 执行规则

当 AI（Codex、Claude 等）执行代码改动任务时，必须遵守本指南的全部规则，且：

- 不得以"实现上更简单"为由跳过归属边界确认
- 不得假设某字段存在于 API 响应中（必须查 `api_v1_spec.md` 确认）
- 不得修改 `docs/dev/*` 中的历史归档文档（只读）
- 改动文档时，必须更新文档头部的"最后更新"日期
- 改动涉及多个边界时，必须分步执行，每步明确指出影响边界

---

## 12. 快速检查清单

改代码之前，回答这五个问题：

1. **这个改动属于哪个边界？**（对照 `system_overview.md` 九个一级边界）
2. **改动前我读了哪些规范文档？**（对照第 1 节改动前置规则总表）
3. **这个改动是否会影响对外接口契约？**（若是，先更新 `api_v1_spec.md`）
4. **这个改动是否跨越了边界禁止规则？**（对照 `system_overview.md` 第 2 节）
5. **改完之后需要更新哪些文档？**（对照第 9 节文档更新规则）

五个问题全部能回答，才开始写代码。
