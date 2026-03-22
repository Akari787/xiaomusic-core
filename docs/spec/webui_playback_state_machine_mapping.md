# WebUI 播放状态机实现映射清单
版本：基于 `Akari787/xiaomusic-core` `1be171a`  
状态：实现映射清单 / 开发对照文档  
建议归档位置：`docs/spec/webui_playback_state_machine_mapping.md`

---

## 1. 文档目的

本文档不是新的产品规范，而是把前一份《WebUI 播放状态机规范》映射回当前实现，回答三个问题：

1. 当前 `HomePage.tsx` 里每一段核心逻辑对应状态机的哪一部分
2. 哪些实现已经接近规范，哪些实现仍然偏离规范
3. 后续如果要修复自动切歌标题问题，应该优先修改哪些位置

---

## 2. 相关文件

主实现文件：

- `xiaomusic/webui/src/pages/HomePage.tsx`

相关接口消费文件：

- `xiaomusic/webui/src/services/v1Api.ts`

相关契约文档：

- `docs/api/api_v1_spec.md`
- `docs/spec/playback_coordinator_interface.md`

---

## 3. 后端输入契约与前端消费边界

### 3.1 `GET /api/v1/player/state` 的前端最小依赖

当前前端 `v1Api.ts` 中 `PlayerStateData` 只依赖以下字段：

- `device_id?: string`
- `is_playing?: boolean`
- `cur_music?: string`
- `offset?: number`
- `duration?: number`

这与 `docs/api/api_v1_spec.md` 中对 `GET /api/v1/player/state` 的最小正式字段定义一致。  
因此，前端播放状态机应被视为：

> 在最小后端输入（is_playing / cur_music / offset / duration）之上做展示侧状态收敛。

### 3.2 与 Playback Coordinator 文档的关系

`docs/spec/playback_coordinator_interface.md` 主要约束的是后端统一播放协调层，包括：

- 解析结果结构
- 适配器职责
- 框架职责

它没有定义 WebUI 如何处理：

- 页面刷新恢复
- 轮询与本地计时器的并行收敛
- 自动切歌时的标题确认问题

因此，WebUI 播放状态机规范与本文档补的是前端展示层，而不是替代后端协调契约。

---

## 4. 状态机视角下的实现分区

当前 `HomePage.tsx` 可以按状态机职责拆成 8 个实现分区。

### 4.1 基础显示与选择状态

对应状态机层：**UI Context**

主要 state：

- `activeDid`
- `playlist`
- `music`
- `volume`
- `showSearch`
- `showTimer`
- `showPlaylink`
- `showVolume`
- `showSettings`

职责：

- 维护当前页面选择
- 决定控制目标设备
- 决定当前歌单 / 当前歌曲下拉框选中项
- 控制各种弹层显示

状态机关系：

- 这些状态不等于真实播放状态
- 它们属于 UI 上下文，不应反向决定“当前确实正在播放哪首歌”

实现风险：

- 当前 `music` 会被“自动同步逻辑”反向更新，因此它既是用户选择状态，也是播放展示状态的影子，容易被污染

---

### 4.2 后端聚合播放状态

对应状态机层：**Remote Snapshot**

主要 state：

- `status`

字段：

- `status.is_playing`
- `status.cur_music`
- `status.offset`
- `status.duration`

职责：

- 承接 `getPlayerState()` 的后端轮询结果
- 经过前端合并后成为最终展示值

状态机关系：

- 它不是纯后端原样值
- 它是状态机对外展示的“聚合视图”

实现风险：

- `status.cur_music` 当前仍被前端旧值 fallback 污染
- `status` 同时承担“事实层”和“展示层”的职责，耦合较高

---

### 4.3 本地播放补偿层

对应状态机层：**Local Progress Projection**

主要 state / ref：

- `localPlaybackStartedAt`
- `localPlaybackDuration`
- `localPlaybackSong`
- `localPlaybackStartedAtRef`
- `localPlaybackDurationRef`
- `localPlaybackSongRef`

职责：

- 在轮询间隙内推进进度条
- 在后端 offset 不稳定时推导 elapsed
- 在部分情况下参与歌曲名兜底

状态机关系：

- 它应只负责“时间轴连续性”
- 不应成为歌曲标题真值来源

当前实现映射：

- `safeOffset / safeDuration` 计算
- 本地 250ms 计时器 effect
- `mergePlayingViewState()` 中对 elapsed 的使用

实现风险：

- 当前实现中 `localPlaybackSong` 仍然可能越权参与标题显示
- boundary 时 ref 和 state 不一致会导致进度与标题分叉

---

### 4.4 页面刷新恢复层

对应状态机层：**Refresh Recovery Window**

主要 state / ref：

- `rememberedPlayingSong`
- `rememberedPlayingSongRef`
- `refreshRestoreUntilRef`

职责：

- 刷新页面或切换设备后，在短窗口内避免播放标题空白
- 从 localStorage 恢复最近一次确认歌曲名

状态机关系：

- 这是“页面恢复”特有状态
- 不能用于自动切歌过渡态

当前实现映射：

- `loadRememberedPlayingSong(activeDid)`
- `currentMusicName` 中的恢复窗口 fallback
- `activeDid` effect 中 `refreshRestoreUntilRef.current = Date.now() + 12000`

实现风险：

- 当前普通轮询失败也会重开该窗口
- 导致恢复逻辑越权进入正常播放态与切歌态

---

### 4.5 用户动作同步层

对应状态机层：**Action Sync / Pending Dispatch**

主要 ref：

- `statusRequestSeqRef`
- `activeActionSeqRef`
- `pendingPlayRef`
- `fastPollUntilRef`
- `lastStatusPollAtRef`
- `statusPollInFlightRef`

职责：

- 区分普通轮询与动作后的快速收敛阶段
- 避免过期轮询覆盖当前状态
- 在播放指令刚发出时保留一个 pending 播放期

状态机关系：

- 对应“用户动作后等待状态收敛”这段过渡态
- 是 play / next / previous 之后的前端同步机制

当前实现映射：

- `beginActionSync()`
- `finishActionSync()`
- `isLatestStatusRequest()`
- `waitForPlayerState()`
- `triggerFastPolling()`

实现风险：

- `pendingPlayRef` 更偏向“手动点播”
- 自动切歌时没有对应的“标题待确认”状态，造成自动切歌标题问题缺口

---

### 4.6 稳定性补偿层

对应状态机层：**Playback Stability Window**

主要 ref：

- `lastPositivePlaybackAtRef`
- `stopSuppressUntilRef`

职责：

- 避免后端一两拍异常把播放态立刻打断
- 停止后短时间抑制误判为正在播放

状态机关系：

- 它只应稳定 `is_playing / offset / duration`
- 不应承担标题确认职责

当前实现映射：

- `Date.now() < stopSuppressUntilRef.current`
- `Date.now() - lastPositivePlaybackAtRef.current < 12000`

实现风险：

- 当前实现中稳定窗口仍会把旧歌名灌回 `merged.cur_music`
- 这是歌曲名错误最关键的污染源之一

---

### 4.7 自动同步 UI 选择层

对应状态机层：**Playback → UI Selection Sync**

主要 ref：

- `lastAutoSyncedPlayingSongRef`

职责：

- 当 `status.cur_music` 变化时，自动把下拉框同步到当前播放歌曲
- 保持 `playlist / music` 与当前播放一致

状态机关系：

- 它只能消费“已确认的当前歌曲”
- 不能倒过来决定当前歌曲真值

当前实现映射：

- `status.is_playing` 相关 effect
- `playingName = String(status.cur_music || "").trim()`

实现风险：

- 当前它默认相信 `status.cur_music` 已可靠
- 如果 `status.cur_music` 被旧值污染，下拉框也会被同步错
- 如果 `status.cur_music` 长时间为空，下拉框会停在上一首

---

### 4.8 最终展示层

对应状态机层：**Rendered Playback View**

主要派生值：

- `safeOffset`
- `safeDuration`
- `progress`
- `currentMusicName`
- `playbackText`

职责：

- 面向界面渲染
- 生成进度条、播放文案、当前歌曲显示

状态机关系：

- 它应只消费已经收敛后的展示状态
- 不应该再承担二次“事实修复”的职责

实现风险：

- 当前 `currentMusicName` 仍混合了：
  - `status.cur_music`
  - `rememberedPlayingSong`
  - `localPlaybackSong`
- 这会把恢复逻辑继续带到渲染层

---

## 5. 按状态机阶段映射当前实现

下面把“规范状态机阶段”逐一映射到当前代码。

---

### 5.1 Idle（空闲）

定义：

- 没有正在播放
- `status.is_playing = false`

当前实现来源：

- 后端 `getPlayerState()` 返回 `is_playing = false`
- 或用户点击停止后前端主动写入 `setStatus(prev => ({...prev, is_playing:false, offset:0}))`

当前关键代码区域：

- 停止按钮点击处理
- `loadStatus()` 中非播放态分支

当前行为：

- 清空本地播放补偿状态
- 清空 `localPlayback*`
- 移除 snapshot

风险：

- 如果稳定窗口误触发，Idle 可能被重新提升成播放态

---

### 5.2 RefreshRecovering（刷新恢复中）

定义：

- 页面初次载入或切换设备后
- 短时间允许 remembered/local fallback 辅助显示

当前实现标记：

- `refreshRestoreUntilRef.current > Date.now()`

进入条件：

- `activeDid` 变化时
- 页面初始化后首次进入设备状态轮询时

退出条件：

- 时间窗口到期
- 或真实播放状态收敛成功

当前行为：

- `currentMusicName` 可从 remembered/local 恢复

风险：

- 当前轮询失败也会重新进入这一态，边界不清晰

---

### 5.3 PendingPlay（手动点播待收敛）

定义：

- 用户刚点了播放
- 后端状态还未稳定刷新到当前页面

当前实现标记：

- `pendingPlayRef.current !== null`

进入条件：

- `playSongByName()` 调用 `v1Play()` 成功后设置 pending

退出条件：

- 后续轮询命中 `merged.is_playing`
- 或 pending 超时

当前行为：

- 先乐观更新 `status`
- 允许页面立即显示该歌曲
- 等待真实轮询结果接管

优点：

- 手动点播体验较好

局限：

- 这套 pending 机制没有覆盖“自动切歌待确认标题”

---

### 5.4 PlayingStable（播放稳定）

定义：

- 正在播放
- 当前歌曲标题和进度已稳定

当前实现标记：

- `status.is_playing = true`
- `status.cur_music` 非空
- 本地计时器正在推进 offset
- 自动同步已把 UI 选择项同步到当前歌曲

当前行为：

- 进度条由轮询 + 本地计时器共同维护
- `rememberedPlayingSong` 会在这里更新
- `localPlaybackSong` 会在这里更新

风险：

- 标题稳定后写 remembered/local 是合理的
- 但如果“未真正稳定”时也写入 remembered/local，会反向污染后续状态

---

### 5.5 BoundaryDetected（检测到切歌边界）

定义：

- 上一首结束 / 手动切歌 / 条件变化表明当前歌曲可能已切换

当前实现位置：

- `mergePlayingViewState()`

当前判据：

- `mergedSong !== prevSong`
- `durationChanged`
- `atBoundary`

当前行为：

- `resolvedOffset = 0`
- 部分场景下 `resetLocalPlayback = true`

问题：

- 当前 boundary 检测已经有了
- 但 boundary 后没有进入独立的“标题待确认”状态
- 所以只是“发现边界”，没有“管理边界后生命周期”

---

### 5.6 AwaitingTitle（等待新标题确认）

规范定义：

- 已经识别到切歌边界
- 进度条属于下一首
- 但新标题尚未被可靠确认

当前实现状态：

- **缺失**

当前代码表现出的替代性行为：

- 有时把 `cur_music` 清空，于是 UI 显示“未知歌曲”
- 有时又通过稳定窗口 / prev fallback / remembered fallback 显示旧标题

结论：

- 这是当前实现与规范之间最大的缺口
- 也是“切歌后歌曲名显示未知歌曲或上一首”的根因

---

### 5.7 TitleConfirmed（新标题已确认）

规范定义：

- 后端返回一个可靠的新标题
- 前端确认它不是旧标题回流
- 允许更新 remembered/local
- 允许同步 playlist/music

当前实现状态：

- **未独立建模**
- 当前相当于“只要 `merged.cur_music` 非空，就直接当作已确认”

问题：

- 没有防旧标题回流机制
- 没有防空标题长期停留机制
- 没有“确认”与“展示”分层

---

## 6. 当前代码块到状态机职责的逐段映射

本节给出更细的实现映射清单，便于后续实际改代码时逐点核对。

---

### 6.1 设备切换 effect

代码职责：

- 重置本地播放状态
- 读取 remembered song
- 打开恢复窗口
- 启动轮询

对应状态机：

- `Idle -> RefreshRecovering`
- 或 `DeviceChanged -> RefreshRecovering`

规范符合度：

- 基本合理

需要收紧的点：

- 只应在切设备 / 首次加载时打开恢复窗口
- 不应被普通轮询失败重新触发

---

### 6.2 `loadStatus()`

代码职责：

- 拉取远端状态
- 做展示态合并
- 处理稳定窗口
- 处理 pendingPlay
- 更新 remembered/local
- 最终写入 `status`

对应状态机：

- `RefreshRecovering`
- `PendingPlay`
- `PlayingStable`
- `BoundaryDetected`
- （当前缺失）`AwaitingTitle`
- `Idle`

规范符合度：

- 是实现核心
- 但职责过多，导致标题问题和时间轴问题耦合

需要收紧的点：

1. stability window 只稳定播放态，不稳定标题
2. boundary 后需要显式进入 AwaitingTitle
3. 只有 TitleConfirmed 后才能写 remembered/local

---

### 6.3 `mergePlayingViewState()`

代码职责：

- 统一合并 offset / duration / cur_music
- 识别切歌边界

对应状态机：

- `PlayingStable`
- `BoundaryDetected`

规范符合度：

- 边界检测思路基本正确

需要收紧的点：

1. boundary 时必须完整 reset state + ref
2. 不应从 `prev.cur_music` 恢复旧标题
3. 这里只应负责“发现边界”和“切换阶段”
4. 不应在这里做标题最终确认

---

### 6.4 本地计时器 effect

代码职责：

- 根据 `localPlaybackStartedAt` 推进 offset
- 让进度条更平滑

对应状态机：

- `PlayingStable`
- `AwaitingTitle`（仅时间轴层）

规范符合度：

- 适合保留

需要收紧的点：

- 本地计时器只能改 `offset / duration`
- 不应该补 `cur_music`

---

### 6.5 自动同步 playlist/music 的 effect

代码职责：

- 根据 `status.cur_music` 自动同步当前歌单与歌曲选择框

对应状态机：

- `TitleConfirmed -> UI Selection Synced`

规范符合度：

- 消费层逻辑基本正确

需要收紧的点：

- 应只消费“已确认标题”
- 在 AwaitingTitle 期间不应同步

---

### 6.6 `playSongByName()`

代码职责：

- 手动点播
- 建立 pending
- 乐观更新 UI
- 等待轮询收敛

对应状态机：

- `Idle -> PendingPlay -> PlayingStable`

规范符合度：

- 手动点播链路较完整

局限：

- 它只解决“我自己点播放”的确认问题
- 不解决“自动切歌时下一首标题确认”问题

---

### 6.7 `switchTrack()`

代码职责：

- 手动上一首 / 下一首
- 通过 baseline 对比确认是否切歌成功

对应状态机：

- `PlayingStable -> BoundaryDetected -> (应进入 AwaitingTitle)`

规范符合度：

- 已能检测“曲目大概率已切换”
- 但没有后续标题确认阶段

---

## 7. 偏离规范的关键问题清单

下面按严重程度列出当前实现偏离状态机规范的地方。

### P0-1：缺少 AwaitingTitle 独立状态
后果：

- 切歌后只能出现两种坏结果：
  - 显示未知歌曲
  - 显示上一首

这是当前最核心问题。

---

### P0-2：stability window 仍在越权处理标题
后果：

- 本来应该只保播放态连续性
- 却把旧标题重新灌回 `status.cur_music`

这是当前标题污染主因之一。

---

### P0-3：boundary reset 没有完整同步 state + ref
后果：

- 本地计时器继续沿用旧 startedAt
- 容易形成“进度是新歌，标题还是旧歌/空歌”的分裂状态

---

### P1-1：remembered/local 的写入时机过早
后果：

- 一旦未确认阶段误写 remembered/local
- 后续 render、恢复窗口、稳定窗口都会被继续污染

---

### P1-2：render 层仍承担事实修复职责
后果：

- `currentMusicName` 不只是消费状态，而是在继续做 fallback 决策
- 恢复逻辑与播放逻辑耦合

---

### P1-3：自动同步逻辑没有“只消费已确认标题”的闸门
后果：

- 下拉框容易停在上一首
- 或被旧标题同步回去

---

## 8. 推荐的实现改造顺序

### 第一步：先拆出状态机标记位
最少新增：

- `awaitingTrackTitleRef`
- `lastConfirmedSongRef`
- `lastBoundaryAtRef`

---

### 第二步：把 boundary 和 title confirmation 拆成两个阶段
当前代码只有：
- 检测边界

还缺：
- 等待新标题
- 确认新标题
- 拒绝旧标题回流

---

### 第三步：收紧 remembered/local 的职责
规则应改为：

- 只在 RefreshRecovering 使用 remembered/local 做显示恢复
- 只在 TitleConfirmed 后写 remembered/local
- 自动切歌期间 remembered/local 不得参与标题决策

---

### 第四步：让本地计时器完全退出标题判断
它只负责：

- `offset`
- `duration`

不再触碰：

- `cur_music`

---

### 第五步：把自动同步下拉框建立在 TitleConfirmed 之上
自动同步的输入应是：

- 已确认标题

而不是：

- 任意非空 `status.cur_music`

---

## 9. 建议的文档归档关系

建议最终形成两份文档并列：

1. `docs/spec/webui_playback_state_machine.md`
   - 规范文档
   - 定义状态机、阶段、准入和禁止规则

2. `docs/spec/webui_playback_state_machine_mapping.md`
   - 实现映射清单
   - 把当前 `HomePage.tsx` 对照回规范
   - 说明哪里符合、哪里偏离、下一步怎么改

---

## 10. 一页版结论

当前 `HomePage.tsx` 的实现现状可以概括为：

- **设备切换恢复**：有
- **手动播放 pending 状态**：有
- **轮询 + 本地计时器平滑进度**：有
- **切歌边界检测**：有
- **稳定播放态**：有
- **自动同步当前歌曲到下拉框**：有
- **“等待新标题确认”状态**：没有
- **“拒绝旧标题回流”机制**：没有

因此现在最准确的判断不是“前端完全没有状态机”，而是：

> 当前前端已经有了半套播放状态机，但缺失了自动切歌最关键的标题确认子状态机。

这也是为什么进度条问题比歌曲名问题更容易先修好。
