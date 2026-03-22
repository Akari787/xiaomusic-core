# WebUI 播放状态逻辑验证与状态机规范文档

版本：v0.1
状态：建议纳入仓库
最后更新：2026-03-22
适用范围：`xiaomusic/webui/src/pages/HomePage.tsx` 及其依赖的播放状态展示、轮询、补偿与前端同步逻辑

---

## 1. 文档目的

本文档分为两部分：

1. **现状验证**：对当前前端逻辑说明与代码、现有契约文档进行交叉校验，确认哪些结论成立，哪些需要修正。
2. **状态机规范**：把当前混合式的前端播放逻辑整理为可执行、可验收、可落库的状态机规范，供后续前端修复与验收使用。

本文档不定义后端 API 契约，不替代 `docs/api/api_v1_spec.md`，只定义 WebUI 如何消费 `GET /api/v1/player/state` 以及如何在前端维护展示状态。

---

## 2. 输入材料与交叉验证范围

本次整理基于以下材料：

- 当前前端主实现：`xiaomusic/webui/src/pages/HomePage.tsx`
- 前端 API 封装：`xiaomusic/webui/src/services/v1Api.ts`
- Runtime API 契约：`docs/api/api_v1_spec.md`
- 播放协调接口草案：`docs/spec/playback_coordinator_interface.md`

---

## 3. 与现有代码/文档的交叉验证结论

### 3.1 与 API v1 契约的一致性

`docs/api/api_v1_spec.md` 已将 `GET /api/v1/player/state` 的最小正式字段约束为：

- `data.device_id`
- `data.is_playing`
- `data.cur_music`
- `data.offset`
- `data.duration`

并明确：

- `offset / duration` 单位固定为秒
- 查询型接口不承诺 `transport`
- `player/state` 是只读状态聚合路径

这与 `v1Api.ts` 中 `PlayerStateData` 的定义完全一致：

```ts
export interface PlayerStateData {
  device_id?: string;
  is_playing?: boolean;
  cur_music?: string;
  offset?: number;
  duration?: number;
}
```

因此，前端逻辑文档中关于“当前 WebUI 能稳定依赖的后端播放状态字段只有 `is_playing / cur_music / offset / duration`”这一判断成立。

### 3.2 与 Playback Coordinator 文档的边界关系

`docs/spec/playback_coordinator_interface.md` 当前只定义了播放协调器在后端侧的职责边界：

- Adapter 负责 resolve query / select context / provide queue facts
- Framework 负责 apply play_mode / compute next previous / maintain state

该文档没有定义 WebUI 的前端展示状态机，也没有定义页面刷新恢复、本地计时器、标题待确认等逻辑。

因此，本文档与它不冲突；相反，本文档正好补齐了“后端播放状态产出之后，前端如何收敛为 UI 状态”的缺失层。

### 3.3 与当前 HomePage.tsx 代码的一致性

以下判断与当前 `HomePage.tsx` 一致：

1. **`status` 不是后端原样值，而是前端合并后的展示状态**
   - `loadStatus()` 会对 `getPlayerState()` 返回值做二次合并。
2. **存在独立的本地计时器链路**
   - `localPlaybackStartedAt / localPlaybackDuration / localPlaybackSong` 与对应 ref 用于在轮询间隙推进 UI。
3. **存在页面刷新恢复链路**
   - `rememberedPlayingSong`、`refreshRestoreUntilRef`、localStorage 缓存共同参与刷新后的过渡显示。
4. **存在稳定性窗口**
   - `lastPositivePlaybackAtRef` 用于在后端短暂抖动时，维持 `is_playing` 的连续性。
5. **存在自动同步当前歌曲到 playlist/music 选择框的逻辑**
   - 基于 `status.cur_music` 在 playlists 内定位并同步下拉框。

以上结论可直接保留。

### 3.4 原逻辑文档中需要修正的地方

以下表述需要收紧或修正：

#### 3.4.1 `xm_playback_snapshot_*` 不能再描述为“完整恢复机制”

当前代码确实仍在写入 `playbackSnapshotKey(did)`，但并没有形成独立完整的读取-合并-恢复链路；真正参与 UI 恢复的主要仍是：

- `rememberedPlayingSong`
- `refreshRestoreUntilRef`
- `localPlayback*`

因此，文档应把 `playbackSnapshot` 表述为：

> 当前仍保留写入，但不是前端展示恢复的主链路，不应视为完整 snapshot 状态机。

#### 3.4.2 标题问题不能只归因于 render fallback

原逻辑说明里容易给人一种印象：标题问题主要是 render 层 fallback 过宽。

这不够准确。当前代码中，标题污染至少发生在三层：

1. `loadStatus()` 的稳定性窗口补偿
2. `mergePlayingViewState()` 的 `prev.cur_music` / `fallbackSong` 合并
3. render 层的 remembered/local fallback

因此，规范文档必须把“标题确认”单独建模，不能只当作 render 细节。

#### 3.4.3 当前实现没有正式的“标题待确认状态”

现有代码虽然尝试检测 song switch boundary，但没有一个显式状态来表达：

- 已确认上一首结束
- 下一首时间轴已开始
- 但下一首标题尚未被确认

这正是当前“进度条正常、标题未知/旧标题复活”的根本原因。规范文档必须把这个状态补出来。

---

## 4. 当前前端逻辑的真实结构（验证后版本）

当前 `HomePage.tsx` 的播放展示逻辑可以分为四层：

### 4.1 后端真值层

来源：`GET /api/v1/player/state`

输入字段：

- `is_playing`
- `cur_music`
- `offset`
- `duration`

这是唯一正式后端输入。

### 4.2 前端补偿层

由 `loadStatus()` 和本地计时器构成，目的不是改变业务事实，而是让页面更连续：

- 稳定性窗口：短暂维持 `is_playing`
- 本地计时器：在轮询间隙推进 `offset`
- duration 继承：在后端 duration 缺失时维持进度条

### 4.3 页面刷新恢复层

仅服务于“页面重载/切换设备后”的短恢复窗口：

- `rememberedPlayingSong`
- `refreshRestoreUntilRef`
- localStorage 缓存

它不应参与自动切歌边界的标题判断。

### 4.4 UI 同步层

由下列展示/同步逻辑组成：

- `currentMusicName`
- `playbackText`
- progress/time 显示
- playlist/music 自动同步 effect

UI 层只能消费“已确认”的标题和时间轴，不应承担标题纠错职责。

---

## 5. 需要纳入规范的核心问题

当前问题不是单点 bug，而是状态边界未被显式建模。必须纳入规范的问题有三类：

### 5.1 时间轴切歌边界

当出现以下情况之一时，前端应判定为切歌边界：

- `offset` 从较大值回到接近 0
- `duration` 明显变化
- 上一首接近末尾，随后 offset 回到 0~3 秒
- `cur_music` 直接变成另一首

### 5.2 标题确认边界

切歌边界被识别后，不能立刻假设新标题已确认。应允许存在：

- 时间轴已属于下一首
- 标题尚未确认

### 5.3 刷新恢复边界

页面刷新恢复是独立场景，只允许在短恢复窗口使用 remembered/local fallback；不得与自动切歌态混用。

---

## 6. 状态机版规范

以下为建议纳入仓库的正式状态机规范。

---

# 第二部分：WebUI 播放状态机规范

## 7. 设计目标

前端播放展示状态机必须满足以下目标：

1. 页面刷新后尽快恢复可理解的播放信息。
2. 自动切歌时，进度条不闪烁、不回跳。
3. 自动切歌时，旧标题不得复活为当前标题。
4. 在后端短暂返回空标题或 `is_playing=false` 时，UI 不应误判为真正停止。
5. 只有“已确认的新标题”才能驱动 playlist/music 自动同步。

---

## 8. 状态机术语

### 8.1 原始状态（Raw State）

后端 `GET /api/v1/player/state` 的返回结果。

### 8.2 展示状态（View State）

前端写入 `status` 后用于渲染 UI 的结果。

### 8.3 已确认标题（Confirmed Title）

满足前端接纳条件、允许展示与同步的歌曲标题。

### 8.4 待确认标题（Awaiting Title）

切歌边界已发生，但下一首标题尚未被前端确认的阶段。

---

## 9. 状态定义

建议将前端展示状态抽象为以下五个主状态。

### S0 Idle

含义：当前没有活跃播放。

判定条件：

- `status.is_playing === false`
- 且不处于 stop 抑制后的补偿窗口

UI 要求：

- `playbackText = "空闲"`
- 进度条置 0 或停留最后状态但不可继续推进
- 不做自动同步

---

### S1 RefreshRestoring

含义：页面刷新/切换设备后，前端处于短恢复窗口。

进入条件：

- 页面初始挂载后第一次进入设备轮询
- 或 `activeDid` 切换后
- 且 `Date.now() < refreshRestoreUntilRef.current`

允许行为：

- 使用 `rememberedPlayingSong` 做一次短恢复显示
- 使用本地缓存的 volume / did / playlist / music 做页面恢复

禁止行为：

- 不得把该状态下的 fallback 结论视为“已确认新标题”
- 不得把 remembered/local song 直接用于自动切歌确认

退出条件：

- 收到可靠后端标题
- 或恢复窗口到期

---

### S2 PlayingConfirmed

含义：当前正在播放，且当前标题已确认。

判定条件：

- `status.is_playing === true`
- `status.cur_music` 非空
- 不处于标题待确认态

允许行为：

- 正常展示歌曲名
- 自动同步 playlist/music
- 本地计时器推进 offset
- 更新 remembered/local playback 缓存

---

### S3 SwitchingAwaitTitle

含义：已识别切歌边界，下一首时间轴已开始，但新标题尚未确认。

进入条件：

满足任一边界判据：

- 旧歌接近结束且 offset 回到 0~3
- duration 明显变化
- `cur_music` 明确变成另一首
- offset 出现切歌式重置

进入动作：

- 记录 `lastConfirmedSong = previous confirmed song`
- 记录 `lastBoundaryAt = now`
- 重置本地计时器起点到 `now`
- 清空 `localPlaybackSong`
- `status.cur_music = ""`
- 保持 `status.is_playing = true`

UI 要求：

- 允许短暂显示“未知歌曲”
- 绝不允许显示上一首标题冒充当前歌曲
- 不执行 playlist/music 自动同步

标题接纳规则：

在该状态下，如果后端返回：

1. `cur_music = ""`：继续等待
2. `cur_music == lastConfirmedSong`：视为旧标题回流，拒绝接纳
3. `cur_music` 非空且不等于 `lastConfirmedSong`：视为新标题确认，转入 `PlayingConfirmed`

---

### S4 PlayingUncertain

含义：后端短暂返回 `is_playing=false` 或不稳定值，但前端依据稳定性窗口仍判断播放未真正结束。

进入条件：

- `Date.now() - lastPositivePlaybackAt < stabilityWindow`
- 且上一个稳定状态仍为播放中

允许行为：

- 维持 `is_playing = true`
- 维持 offset/duration 连续性

禁止行为：

- 不得在该状态中回填旧标题
- 不得用 remembered/localPlaybackSong 替代当前标题

与 `SwitchingAwaitTitle` 的关系：

- 若已处于 `SwitchingAwaitTitle`，则 `PlayingUncertain` 只能维护时间轴，不得结束标题等待。

---

## 10. 状态转换规则

### 10.1 Idle -> RefreshRestoring

触发：页面初始化或切换设备后，开始第一次状态恢复。

### 10.2 RefreshRestoring -> PlayingConfirmed

触发：收到非空且可信的 `cur_music`。

### 10.3 RefreshRestoring -> Idle

触发：恢复窗口到期且后端未给出播放中状态。

### 10.4 PlayingConfirmed -> SwitchingAwaitTitle

触发：识别到 song switch boundary。

### 10.5 SwitchingAwaitTitle -> PlayingConfirmed

触发：收到新的、非空、且不同于上一首的标题。

### 10.6 SwitchingAwaitTitle -> Idle

触发：后端明确停止，且稳定性窗口结束。

### 10.7 PlayingConfirmed -> PlayingUncertain

触发：后端瞬时返回 `is_playing=false`，但仍在稳定性窗口内。

### 10.8 PlayingUncertain -> PlayingConfirmed

触发：后端重新返回稳定的播放中状态，且标题已确认。

### 10.9 PlayingUncertain -> Idle

触发：稳定性窗口结束，仍未恢复播放。

---

## 11. 字段写入规范

### 11.1 `status.cur_music`

允许写入来源：

- 后端返回的非空、已确认标题

禁止写入来源：

- `prev.cur_music` 作为自动切歌边界 fallback
- `rememberedPlayingSong` 作为切歌标题 fallback
- `localPlaybackSong` 作为切歌标题 fallback
- 稳定性窗口中的旧标题回填

### 11.2 `rememberedPlayingSong`

允许写入时机：

- 进入 `PlayingConfirmed` 后
- 手动点播且标题已明确时

禁止写入时机：

- `SwitchingAwaitTitle`
- 空标题
- 与 `lastConfirmedSong` 相同的旧标题回流

### 11.3 `localPlaybackSong`

用途限定：

- 仅表示“前端最近一次确认的本地展示标题”
- 不得作为切歌后的标题推断来源

### 11.4 `localPlaybackStartedAt`

允许写入时机：

- 点播开始
- 收到可靠 offset 后重算 startedAt
- 进入切歌边界时重置到新边界起点

规范要求：

- 必须同时更新 state 与 ref
- 不得只更新 ref 而不更新 state

---

## 12. 本地计时器规范

本地计时器职责仅限：

- 推进 `offset`
- 辅助维持 `duration`

不得承担：

- 标题推断
- 标题确认
- 标题回填

因此，本地计时器中：

- `cur_music` 只能沿用当前 `status.cur_music`
- 若当前 `status.cur_music` 为空，则保持为空
- 不得从 `localPlaybackSong` 补标题

---

## 13. 刷新恢复规范

刷新恢复仅适用于：

- 页面初始挂载
- 设备切换后的短窗口

恢复窗口内允许：

- 用 `rememberedPlayingSong` 做短展示

恢复窗口内禁止：

- 参与自动切歌边界判断
- 参与标题确认
- 参与 playlist/music 自动同步

普通轮询失败不得重新打开恢复窗口。

---

## 14. 自动同步规范

自动同步 playlist/music 的前提必须是：

- 当前状态为 `PlayingConfirmed`
- `status.cur_music` 已确认非空

以下场景必须直接 return：

- `Idle`
- `RefreshRestoring` 但标题未确认
- `SwitchingAwaitTitle`
- `PlayingUncertain` 且当前标题为空

---

## 15. 最小实现建议

若要把当前 `HomePage.tsx` 收敛到本规范，建议至少引入以下轻量状态：

- `awaitingTrackTitleRef`
- `lastConfirmedSongRef`
- `lastBoundaryAtRef`

其职责分别为：

- `awaitingTrackTitleRef`：标记当前是否处于标题待确认态
- `lastConfirmedSongRef`：保存上一首已确认标题，阻止旧标题复活
- `lastBoundaryAtRef`：记录边界时刻，供调试和条件收敛使用

---

## 16. 验收口径

修复后必须满足：

### 16.1 自动切歌

- 进度条不闪烁
- 不回跳到旧 offset
- 标题允许短暂为空
- 但不允许显示上一首标题
- 新标题到达后必须更新 UI 和下拉框

### 16.2 切歌期间后端返回空标题

- UI 可以显示“未知歌曲”
- 但不能把旧标题当作当前歌曲

### 16.3 后端短暂回旧标题

- 前端必须拒绝把上一首标题重新当作当前标题

### 16.4 页面刷新恢复

- remembered song 可以短暂参与恢复显示
- 但不能影响后续自动切歌

---

## 17. 建议仓库落点

建议将本文档放在：

`docs/spec/webui_playback_state_machine.md`

理由：

1. 它不是对外 API 契约，不应放入 `docs/api/`
2. 它也不是一次性的实施计划，不适合放入 `docs/implementation/`
3. 它定义的是前端播放展示与状态收敛规则，属于“系统行为规范”，最适合放在 `docs/spec/`
4. 与现有 `docs/spec/playback_coordinator_interface.md` 属于同层级：
   - 一个定义后端播放协调接口边界
   - 一个定义前端播放展示状态机边界

如需进一步细分目录，也可以考虑：

`docs/spec/webui/webui_playback_state_machine.md`

但在当前仓库体量下，先直接放在 `docs/spec/` 更直接。

---

## 18. 结论

本次交叉验证后的结论是：

1. 原前端逻辑文档关于“状态分层、轮询、本地计时器、刷新恢复”的大方向判断基本成立。
2. 需要修正的关键点，是把“标题问题”从 render fallback 层，上升为独立的状态机问题。
3. 当前代码与现有文档之间并不冲突；真正缺失的是一份正式的 **WebUI 播放状态机规范**。
4. 本文档建议作为后续前端修复、验收、文档驱动开发的基线，路径建议为：
   - `docs/spec/webui_playback_state_machine.md`

