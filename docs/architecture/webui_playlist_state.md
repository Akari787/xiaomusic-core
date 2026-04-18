# WebUI 歌单选择状态架构（WebUI Playlist Selection State Architecture）

版本：v1.1
状态：正式架构文档（已实施）
最后更新：2026-04-18

本文档定义 WebUI 中歌单选择、曲目选择与后端播放状态之间的职责边界，解决当前 playlist selector 在播放中或停止后仍可能被后端状态覆盖、无法自由浏览歌单的问题。本文档只覆盖 WebUI 侧状态模型与 v1 API 消费语义，不修改 `docs/spec/player_state_projection_spec.md` 中的播放器快照字段定义。

相关规范：

- `docs/architecture/webui_architecture.md`
- `docs/spec/player_state_projection_spec.md`
- `docs/spec/player_stream_sse_spec.md`
- `docs/api/api_v1_spec.md`

当前实现状态（2026-04-18）：

- P0 已实施并完成测试服务器验收
- P1 已实施并经人工验收通过
- P2 已实施，`/api/v1/play` 已返回最新 snapshot

---

## 1. 问题陈述

当前 HomePage 中以下三类语义长期共用了同一组前端状态：

- 用户当前想浏览的歌单/曲目
- 用户已经选中但尚未点击播放的待提交选择
- 后端当前真实播放中的 playlist / track 上下文

现状中常见的本地状态为：

- `playlist`
- `music`
- `selectedTrackId`

这三者同时承担：

1. selector 受控值
2. 曲目列表渲染依据
3. `POST /api/v1/play` 的请求来源
4. 后端 SSE / `/api/v1/player/state` 同步后的回写目标

该设计会产生以下冲突：

- 用户切换歌单进行浏览时，选择可能被后端 snapshot 的播放态回写覆盖
- 播放中与停止后都可能出现 selector 无法稳定停留在用户新选择上的问题
- 仅靠“交互期间暂停更新”或“局部跳过 snapshot”只能缓解 UI 抖动，不能定义正确状态语义

因此必须将“浏览/待提交状态”和“真实播放状态”正式拆分。

---

## 2. 行为契约

下表定义 WebUI 对 playlist selector 的正式行为契约。

| 场景 | 前端行为 | 后端行为 | pending 状态 |
|---|---|---|---|
| 初次加载 / 刷新页 | 以前端接收到的最新 `serverState` 初始化 UI | 提供当前真实播放快照 | 清空 |
| 用户下拉选择歌单 | 更新 pending，允许自由切换浏览 | 无需立即变化 | 建立 / 更新 |
| 选择后关闭下拉（不点播放） | 保持用户当前 pending 选择 | 无需立即变化 | 保持 |
| 用户点击播放 | 使用 pending 选择作为请求目标调用 API | 成功后通过 SSE / `/player/state` 返回新的真实播放状态 | 在新播放状态确认后清空 |
| 用户点击上一首 / 下一首 | 若存在 pending，则按 pending 选择导航；否则按后端当前播放态导航 | 成功后返回新的真实播放状态 | 在新播放状态确认后清空 |
| 自动切歌（timer） | 接受后端状态切换并同步 UI | 产生新的播放会话 / 新曲目 | 清空 |
| API 停止 | 接受后端停止态并同步 UI | 返回 `stopped/idle` 等真实态 | 清空 |

### 2.1 pending 过期机制

pending 表示“用户当前在前端选中了新的歌单/曲目，但尚未由后端确认成为真实播放状态”。
pending 必须在后端播放状态发生变化时自动失效。

pending 失效后：

- selector 与曲目列表重新回到后端真实播放态
- UI 不再保留旧的未提交选择
- 不允许 pending 长时间悬挂并继续污染后续播放操作

---

## 3. 状态模型

WebUI 必须使用三层状态模型，不得再让单一 `playlist / music / selectedTrackId` 同时承担三种语义。

## 3.1 `serverState`

### 定义
后端权威播放状态，只能来自：

- `GET /api/v1/player/stream`
- `GET /api/v1/player/state`

### 职责

- 表达当前真实播放事实
- 驱动播放文本、时长、进度、音量、transport state 等真实播放 UI
- 作为 pending 是否失效的判断输入

### 约束

- 前端不得手工构造或覆盖 `serverState`
- 前端不得因 selector 交互而拒绝接收权威播放状态
- `serverState` 是播放真相，不是浏览真相

## 3.2 `pendingSelection`

### 定义
用户在前端做出的、尚未提交或尚未被后端确认的临时选择。

建议最小结构：

```ts
interface PendingSelection {
  playlist: string | null;
  trackId: string | null;
  trackTitle: string | null;
  anchorPlaySessionId: string;
  anchorRevision: number;
  submitting: boolean;
}
```

### 职责

- 表达用户当前正在浏览或准备播放的歌单/曲目
- 作为 `play` / pending-aware `next` / pending-aware `previous` 的请求输入
- 在后端真实状态确认前维持 selector 的用户选择

### 约束

- `pendingSelection` 不代表真实播放状态
- `pendingSelection` 只由用户交互或前端提交逻辑修改
- `pendingSelection` 失效后必须清空，不得继续参与渲染和请求决策

## 3.3 `effectiveSelection`

### 定义
给 selector、曲目列表和播放按钮使用的最终显示/操作状态。

推荐规则：

```ts
if (pendingSelection exists) {
  effectiveSelection = pendingSelection
} else {
  effectiveSelection = playback selection derived from serverState
}
```

### 职责

- 作为 playlist selector 的 `value`
- 作为 songs 列表的渲染基准
- 作为当前“播放目标”的最终选择来源

### 约束

- `effectiveSelection` 是派生状态，不单独存成第三套可变真相
- 真实播放态与 pending 态必须通过 `effectiveSelection` 统一进入 UI

---

## 4. 各层来源与职责边界

| 状态层 | 来源 | 是否允许用户直接修改 | 是否代表真实播放 | 主要用途 |
|---|---|---:|---:|---|
| `serverState` | SSE / `/player/state` | 否 | 是 | 权威播放事实 |
| `pendingSelection` | selector / 列表点击 / 待提交操作 | 是 | 否 | 浏览和待提交意图 |
| `effectiveSelection` | `pendingSelection ?? serverState-derived playback selection` | 否（派生） | 条件式 | UI 受控值与播放目标 |

禁止做法：

- 用 `serverState` 直接覆盖用户尚未提交的 selector 值
- 用 `pendingSelection` 反向伪造真实播放状态
- 保留第四套“历史兼容 playlist/music/selectedTrackId 真相”

---

## 5. pending 清除判据

pending 清除必须基于“后端真实播放状态已变化”这一事实，而不是基于普通 UI 交互结束。

## 5.1 主判据

### `play_session_id` 变化

当：

```ts
next.play_session_id !== pending.anchorPlaySessionId
```

则判定 pending 失效并清空。

这是首选判据，适用于：

- 用户点击播放后切到新曲目
- 用户点击上一首 / 下一首后切到新曲目
- 自动切歌
- 外部控制导致新播放会话

## 5.2 辅判据

当以下条件成立时，也可清空 pending：

1. `revision` 已推进，且
2. `context.name` / `track.id` 已切到新的真实对象，且
3. 该变化不再匹配 pending 建立时的 anchor 状态

辅判据用于补偿个别路径里 `play_session_id` 更新不及时的情况。

## 5.3 停止态判据

当 API stop 成功后，若后端快照进入：

- `transport_state = stopped`
- 或 `transport_state = idle`

且该状态对应的 `revision` / `play_session_id` 已发生确认性变化，则应清空 pending。

## 5.4 不应触发 pending 清除的变化

以下变化不得单独导致 pending 失效：

- `snapshot_at_ms` 推进
- `position_ms` 推进
- `duration_ms` 补全
- `volume` 更新
- 同一播放会话下的 projection 修正
- selector 打开/关闭本身

---

## 6. SSE snapshot 应用语义

`applySnapshotFn` 必须只承担“接收并去重后端权威状态”的职责，不再直接决定 selector 的最终值。

## 6.1 允许的职责

- 根据 `revision / snapshot_at_ms / position_ms` 去重
- 更新 `serverState`
- 更新与播放真相直接相关的辅助 UI（如连接状态、switching hint、remembered title）

## 6.2 不允许的职责

- 因 selector 正在交互就拒绝接收后端真实状态
- 直接把 selector 强制回写到播放 context
- 在 `applySnapshotFn` 内部直接清空或重建 pending 选择

## 6.3 推荐实现

pending 是否失效，应在 snapshot 应用后的独立逻辑中判断：

```ts
serverState <- applySnapshotFn(snapshot)

useEffect([serverState]) {
  if (pendingSelection && shouldInvalidatePending(prevServerState, serverState, pendingSelection)) {
    clearPendingSelection()
  }
}
```

即：

- snapshot 输入层只负责接收权威事实
- pending 生命周期由独立业务逻辑判断

---

## 7. API 语义决策

## 7.1 播放 API（`POST /api/v1/play`）

当存在 pendingSelection 时，前端必须使用 pendingSelection 作为请求输入，而不是后端当前播放态。

即：

- `playlist_name`
- `music_name`
- `track_name`
- `context_name`
- `context_id`

都应来自 `effectiveSelection`，其中优先取 pending。

### 成功后的同步时机

当前实现已经让 `/api/v1/play` 在成功响应中附带最新 `player state snapshot`，字段路径为：

- `data.state`

规范决定：

1. API 成功后，前端可先将 pending 标记为 `submitting = true`
2. 若 `data.state` 存在，则立即应用该 snapshot，缩短 UI 等待窗口
3. 后续仍继续接受 SSE / `/player/state` 的权威更新
4. 当命中 pending 清除判据后，再清空 pending

## 7.2 `next` / `previous` 的正式语义

### 决策
当 pending 不存在时：

- 仍调用 `POST /api/v1/control/next`
- 仍调用 `POST /api/v1/control/previous`

当 pending 存在时：

- **前端不得直接调用 `v1Next` / `v1Previous` 作为 pending 导航语义实现**
- 前端必须在 pending 对应的 playlist 中本地计算目标曲目
- 然后通过 `POST /api/v1/play` 发起播放

### 原因
当前 `v1Next` / `v1Previous` 的语义是“对设备当前真实播放队列导航”，并不接受 pending playlist / pending track 作为输入。

若在 pending 存在时继续调用它们，会出现：

- 用户浏览的是 A 歌单
- 设备真实播放的是 B 歌单
- 用户点 next/previous 后，实际仍对 B 导航

这与本轮确认的行为契约冲突。

因此本规范明确：

> 在 pending 存在时，next/previous 的前端实现语义切换为“基于 pendingSelection 计算目标曲目，再走 `v1Play`”。

---

## 8. selector 交互层与 pending 的关系

当前 `beginSelectorInteraction / endSelectorInteraction` 只能作为 UI 交互噪声抑制层，不得承担业务状态一致性职责。

### 允许用途

- 暂停高频进度插值，减少浏览器 select 交互抖动
- 减少交互过程中不必要的局部重绘

### 禁止用途

- 作为 pending 建立/失效的唯一判据
- 用于拒绝接收后端权威播放状态
- 用于替代 pending 状态模型

### 正确配合方式

- 用户打开 selector：可标记正在交互
- 用户选择歌单/曲目：建立或更新 pendingSelection
- 用户关闭 selector 但未播放：pending 保持
- 后端真实播放状态变化：由 pending 判据决定是否清空

---

## 9. 实施优先级与当前状态

## P0：状态模型收口

目标：先让 playlist selector 的状态语义正确。

范围：

- 引入 `pendingSelection`
- 从 `serverState` 派生 playback selection
- 用 `effectiveSelection` 驱动 selector / songs / 选择高亮 / 播放入口
- `applySnapshotFn` 收口为权威状态输入层
- 独立实现 pending 失效判定与清理

完成定义：

- 用户可自由切换歌单浏览
- 播放态不会直接覆盖未提交的浏览选择
- 刷新页后回到后端真实播放态

当前状态：已实施。

## P1：pending-aware 导航语义

目标：补齐 next/previous 的契约。

范围：

- pending 存在时，本地按 pending playlist 计算上一首/下一首
- 调用 `POST /api/v1/play` 而不是 `POST /api/v1/control/next|previous`
- 成功后等待后端真实状态确认并清空 pending

完成定义：

- 用户在 pending 浏览态下点击上一首/下一首，行为以当前选择为准，而不是以后端旧播放队列为准

当前状态：已实施，并经人工验收通过。

## P2：体验优化

目标：减少“API 已成功，但还在等 SSE”窗口。

可选范围：

- 评估 `/api/v1/play` 是否返回最新 player state snapshot
- 评估 `/api/v1/control/*` 是否返回最新 player state snapshot
- 用 API 响应加速 pending 清空与 UI 收敛

当前状态：已实施。

---

## 10. 技术债说明

当前实现仍存在以下技术债：

1. **HomePage 状态逻辑仍偏集中**
   - `serverState`、pending 生命周期、effective 派生、播放控制仍集中在 `HomePage.tsx`
   - 后续建议拆出独立 hooks，降低回归风险

2. **pending invalidation 仍依赖前端本地判据组合**
   - 当前以 `play_session_id` 为主判据，辅以 `revision + track/context` 变化
   - 若后端某些路径存在投影延迟，仍可能出现边界误差

3. **P2 当前只覆盖 `/api/v1/play`**
   - `stop / next / previous / pause / resume` 仍主要依赖 SSE 收敛
   - 若要进一步统一体验，可评估 control 类响应也返回 `state`

4. **浏览器自动化仍不稳定**
   - 关键交互验收仍需人工补强
   - 后续应补更稳定的前端测试夹具或更细粒度交互测试

5. **测试服务器静态产物同步路径容易误判**
   - 仅同步 `src` 并重建容器，不一定会让外部页面立即使用新 bundle
   - 验证前应检查 `/webui/` 返回的 bundle hash，必要时同步 `xiaomusic/webui/static`

---

## 11. 实施禁止项

为避免回到旧问题，以下做法明确禁止：

- 再次把 selector 的 `value` 直接绑定到后端播放 context
- 继续用“交互中暂停更新”替代 pending 状态模型
- 在 pending 存在时继续无条件调用 `v1Next` / `v1Previous`
- 保留多套相互覆盖的 `playlist/music/selectedTrackId` 真相源
- 通过前端伪造 `serverState` 来“看起来同步成功”

---

## 12. 相关文件

本规范落地时，预计主要涉及：

- `xiaomusic/webui/src/pages/HomePage.tsx`
- `xiaomusic/webui/src/services/v1Api.ts`
- 如需测试补充：`xiaomusic/webui/tests/*`

---

## 13. 结论

本规范当前已完成从“架构决策”到“真实实现”的闭环，正式确认：

1. WebUI playlist selector 已引入 pending 状态模型
2. `serverState` 与 `pendingSelection` 已完成分离
3. selector/UI 已绑定 `effectiveSelection`
4. pending 清除以 `play_session_id` 变化为主判据已落地
5. pending 存在时的 next/previous 前端语义已切换为“本地算目标曲目 + `v1Play`”
6. `/api/v1/play` 已返回最新 snapshot，用于缩短前端等待窗口

后续实现与回归验证必须继续以本规范为准，不再回退到补丁式状态覆盖方案。