# WebUI 播放状态机实现映射清单（重构版）

版本：v2.0
状态：重构对照文档
最后更新：2026-03-22
适用范围：`xiaomusic/webui/src/pages/HomePage.tsx`、`xiaomusic/webui/src/services/v1Api.ts`

---

## 1. 文档定位

本文档是实现映射文档，不是规范文档。

本文档的目的是将当前 `HomePage.tsx` 的具体实现结构，与新消费型状态机规范（`docs/spec/webui_playback_state_machine_spec.md`，以下简称"状态机规范"）建立一一对应关系，明确指导代码重构的方向与顺序。

本文档回答三个问题：

1. 当前 `HomePage.tsx` 中的每个关键变量、函数、数据流，在新规范下属于哪种处置方式（删除 / 改造 / 保留）？
2. 新规范要求的结构（`serverState`、`uiState`、SSE handler、revision gate 等）如何与现有代码对应落地？
3. 数据流如何从"轮询 → 本地合并"改为"SSE → revision check → serverState → render"？

本文档不定义字段语义，字段语义以投影规范（`docs/spec/player_state_projection_spec.md`）为准。

相关规范文件：

- `docs/spec/webui_playback_state_machine_spec.md`（消费型状态机规范，本文档的上位规范）
- `docs/spec/player_state_projection_spec.md`（投影规范）
- `docs/spec/player_stream_sse_spec.md`（SSE 规范）
- `docs/api/api_v1_spec.md`（API v1 契约）

---

## 2. 旧模型结构识别

本章逐一说明当前 `HomePage.tsx` 中属于旧推测型模型的关键构件，包括其原职责、导致的问题，以及新规范下的处置方式。

### 2.1 `mergePlayingViewState()`

**原职责**

在 `loadStatus()` 内部定义，负责将后端轮询原始值（`offset`、`duration`、`cur_music`）与本地状态合并为最终展示值。包含：切歌边界检测（基于 `trackIdChanged`、`indexChanged`、`songChangedByHeuristic`、`atBoundary`）、进度推算（基于 `elapsed` = `Date.now() - localPlaybackStartedAtRef`）、标题等待进入逻辑（设置 `awaitingTrackTitleRef`、`lastConfirmedSongRef`）。

**导致的问题**

- 这个函数承担了"状态合并"、"边界检测"、"标题确认"三个不同职责，逻辑高度耦合。
- 边界检测依赖 `offset` 回退、`duration` 突变、`cur_music` 变化等启发式信号，在网络抖动或后端延迟时容易误判。
- `mergePlayingViewState()` 是本地推测的核心，与"服务端是唯一权威来源"的新模型根本冲突。

**新规范处置：删除**

新模型中，切歌边界由 `play_session_id` 变化直接提供，不再需要启发式边界检测。`offset`/`duration`/`cur_music` 字段已被 `position_ms`/`duration_ms`/`track.title` 取代。此函数整体删除，其中合法的部分（进度插值辅助）由独立的进度插值逻辑承接。

---

### 2.2 `lastPositivePlaybackAtRef` + `stopSuppressUntilRef` 稳定性窗口

**原职责**

- `lastPositivePlaybackAtRef`：记录最后一次 `is_playing=true` 的时刻，用于在后端短暂返回 `is_playing=false` 时，维持前端认为"仍在播放中"的状态（稳定性窗口 12 秒）。
- `stopSuppressUntilRef`：停止操作后短时间内抑制后端返回的"仍在播放"状态，避免 UI 回弹。

**导致的问题**

- 稳定性窗口维持播放态时，会同时保留旧 `cur_music` 并写回 `merged.cur_music`，这是切歌后旧标题"复活"最关键的污染源。
- 稳定性窗口掩盖了后端真实状态，导致前端无法准确感知 `transport_state` 的变化。

**新规范处置：删除**

新模型中，`transport_state` 由服务端维护，前端直接消费，不再需要前端自行抑制抖动。`transport_state == "switching"` 是切歌中的正式状态，`transport_state == "playing"` 是播放中的权威状态，两者均不需要前端稳定性补偿。

---

### 2.3 `rememberedPlayingSong` / `rememberedPlayingSongRef` 及相关函数

**原职责**

- `rememberedPlayingSong`：React state，存储前端记忆的"最后已确认歌曲名"，通过 localStorage 持久化（`loadRememberedPlayingSong` / `saveRememberedPlayingSong`）。
- 在 `currentMusicName` 的派生计算中作为 fallback，在 `refreshRestoreUntilRef` 窗口内或 `status.cur_music` 为空时填充标题显示。
- 在切歌确认逻辑中被写入（`setRememberedPlayingSong(mergedSong)`），用于后续恢复。

**导致的问题**

- `rememberedPlayingSong` 在多个地方被写入（`loadStatus` 中的 `awaitingTitle` 结束时、稳定播放时、`playSongByName` 中），写入时机不统一，容易在切歌过渡期写入错误值。
- 作为 `currentMusicName` 的 fallback，它在轮询失败、切歌中、刷新恢复等场景下都可能被误用为"当前正确标题"。
- 这是前端"拥有播放真相"的典型表现，与新规范直接冲突。

**新规范处置：限制职责至初始化过渡**

`rememberedPlayingSong` 只允许在 `uiState.initializing = true` 期间（页面首次加载、首次快照到达前）用于过渡展示。一旦首次 `serverState` 到达，立即让位，不再参与任何状态逻辑。`currentMusicName` 的计算逻辑必须以 `serverState.track.title` 为唯一来源，不得保留对 `rememberedPlayingSong` 的 fallback 引用（初始化窗口除外）。

---

### 2.4 `localPlaybackStartedAt` / `localPlaybackDuration` / `localPlaybackSong` 及对应 Ref

**原职责**

- `localPlaybackStartedAt`：本地记录播放开始时刻，用于在轮询间隙推算 `elapsed = (Date.now() - startedAt) / 1000`，推进进度条。
- `localPlaybackDuration`：本地记录当前曲目时长，在后端 `duration` 缺失时维持进度条比例。
- `localPlaybackSong`：本地记录最近一次确认的歌曲名，在 `currentMusicName` 中作为辅助 fallback。
- 三者都有对应的 Ref（`localPlaybackStartedAtRef` 等）用于在计时器 effect 中访问最新值而不触发重渲染。

**导致的问题**

- `localPlaybackSong` 参与了 `currentMusicName` 的 fallback 计算，使得本地缓存歌名可能在切歌过渡期被展示为"当前歌曲"。
- `localPlaybackStartedAt` 在 boundary 检测时被重置，但 state 与 ref 有时不同步，导致进度和标题出现分叉。
- 整套机制在新模型下不再需要，进度插值应完全基于 `position_ms + snapshot_at_ms`。

**新规范处置：`localPlaybackSong` 删除，其余降级为进度插值辅助**

- `localPlaybackSong` 和 `localPlaybackSongRef`：删除，不得再参与任何标题展示逻辑。
- `localPlaybackStartedAt` / `localPlaybackStartedAtRef`：改造为单纯记录 `serverState.snapshot_at_ms` 的本地副本，用于进度插值，不再承担边界检测信号职责。
- `localPlaybackDuration` / `localPlaybackDurationRef`：改造为单纯记录 `serverState.duration_ms / 1000` 的本地副本，不再承担兜底职责。

---

### 2.5 `pendingPlayRef`

**原职责**

- 记录用户手动点播的"待确认播放"信息（`{ did, song, expiresAt }`）。
- 在 `loadStatus()` 中，若后端尚未返回新状态，使用 `pendingPlayRef.current.song` 填充 `merged.cur_music`，实现乐观更新。

**导致的问题**

- `pendingPlayRef` 机制只覆盖手动点播，不覆盖自动切歌，导致两种切歌路径处理不一致。
- 在新模型下，播放命令（`POST /api/v1/play`）不承诺立即状态，状态变化由 SSE 推送，乐观更新不再必要。

**新规范处置：删除**

手动点播发出后，前端展示"加载中"状态（通过 `transport_state == "starting"` 的 SSE 事件驱动，或通过 `uiState.initializing` 短暂标记），等待 SSE 推送新的 `player_state` 事件。不再需要前端自行维持乐观标题。

---

### 2.6 `refreshRestoreUntilRef` 及恢复窗口逻辑

**原职责**

- 在设备切换或页面初始化后，短时间（12 秒）内允许 `currentMusicName` 从 `rememberedPlayingSong` / `localPlaybackSong` 读取歌名。
- 通过 `refreshRestoreUntilRef.current = Date.now() + 12000` 开启，时间到期自动关闭。

**导致的问题**

- 当前实现中，普通轮询失败也可能重新触发恢复窗口，导致恢复逻辑越权进入正常播放态和切歌态。
- 12 秒窗口过长，可能覆盖多次自动切歌周期。

**新规范处置：改造为 `uiState.initializing`**

恢复窗口概念保留，但改造为 `uiState.initializing` 标记：仅在页面首次加载或设备切换后的"首次快照到达前"期间为 true，首次 `serverState` 到达后立即清除。`uiState.initializing` 期间允许展示 `rememberedPlayingSong`（从 localStorage 读取的上次播放歌名）。轮询失败不得重新打开此标记。

---

### 2.7 `awaitingTrackTitleRef` + `lastConfirmedSongRef` + `lastBoundaryAtRef`

**原职责**

- `awaitingTrackTitleRef`：标记当前是否处于"切歌边界已触发、新标题待确认"状态。
- `lastConfirmedSongRef`：记录上一首已确认的歌曲名，用于拒绝旧标题回流（`mergedSong !== lastConfirmedSongRef.current` 才接受新标题）。
- `lastBoundaryAtRef`：记录边界发生时刻，辅助调试。

**导致的问题**

- 这三个 ref 是对旧模型缺陷的补丁：因为没有 `play_session_id`，只能靠这些辅助标记来模拟切歌边界管理。
- 它们的写入分散在 `mergePlayingViewState`、`loadStatus` 的多个分支中，状态转换逻辑复杂且容易出错。

**新规范处置：删除，职责由 `play_session_id` 取代**

新模型中，`play_session_id` 变化直接标志切歌边界，`track.id` 提供稳定曲目身份，`transport_state` 表达切歌中的过渡状态。`awaitingTrackTitleRef`、`lastConfirmedSongRef`、`lastBoundaryAtRef` 所模拟的能力已由服务端显式提供，不再需要前端自行维护。

---

### 2.8 `statusRequestSeqRef` + `activeActionSeqRef` + `fastPollUntilRef` + 轮询频率控制

**原职责**

- `statusRequestSeqRef`：请求序列号，用于丢弃过期的轮询响应（慢请求保护）。
- `activeActionSeqRef`：动作序列号，用于区分用户动作前后的轮询。
- `fastPollUntilRef`：动作触发后短时间内加速轮询（350ms 间隔），等待状态收敛。
- `lastStatusPollAtRef` / `statusPollInFlightRef`：防重入保护。

**导致的问题**

- 整套机制是为了弥补"轮询延迟 → 状态收敛慢"问题而建立的，在 SSE 主通道下不再需要。

**新规范处置：删除轮询频率控制部分，保留慢请求保护语义**

- `fastPollUntilRef` + 动态轮询间隔：删除，SSE 是推送模型，不需要加速轮询。
- `statusRequestSeqRef`（慢请求丢弃）：降级保留，仅用于降级轮询期间（SSE 断线时）的慢请求保护。SSE handler 本身不需要此机制（SSE 是单向推送，无并发请求竞争问题）。
- `statusPollInFlightRef`：仅在降级轮询期间保留，防止降级轮询请求重入。

---

### 2.9 `currentMusicName` 派生计算

**原职责**

`currentMusicName` 是渲染层的派生值，当前计算逻辑为：

```
status.cur_music
|| (refreshRestoreWindow && is_playing && !awaitingTitle ? rememberedPlayingSong : "")
|| (refreshRestoreWindow && localSongFresh && !awaitingTitle ? localPlaybackSong : "")
|| ""
```

包含了三层 fallback：后端值 → remembered → local。

**导致的问题**

- render 层承担了事实修复职责，这违反了"展示层只消费已收敛状态"的原则。
- 三层 fallback 链使得歌名来源不清晰，任何一层写入脏值都会污染展示。

**新规范处置：改造为单一来源**

```
serverState.track?.title || (uiState.initializing ? rememberedPlayingSong : "")
```

非初始化期间：直接读取 `serverState.track.title`，为空时展示占位符，不读取任何本地缓存。初始化期间（`uiState.initializing = true`）：允许展示 `rememberedPlayingSong` 作为过渡。

---

### 2.10 `playSongByName()` + `switchTrack()` 的乐观更新逻辑

**原职责**

- `playSongByName()`：手动点播，在命令发出后立即乐观更新 `status.cur_music` 为目标歌名，同时写入 `pendingPlayRef`，等待轮询确认。
- `switchTrack()`：上一首/下一首，发出命令后通过 baseline 对比等待切歌确认。

**导致的问题**

- 乐观更新写入本地 `status`，当 SSE 推送实际状态时会产生冲突。
- `playSongByName()` 的乐观路径与自动切歌路径处理不一致（后者没有 pending 机制）。

**新规范处置：移除乐观更新，改为 `uiState` 过渡标记**

命令发出后，不再乐观写入 `serverState`。可在 `uiState` 中标记"命令已发出，等待 SSE 确认"，用于展示"加载中"状态。SSE 推送新的 `player_state` 事件（`transport_state == "starting"` 或 `transport_state == "playing"` 且 `play_session_id` 变化）到达后，清除过渡标记，以 `serverState` 为准渲染。

---

## 3. 新结构设计

本章定义新规范下的实现结构，以及其与现有代码的对应关系。

### 3.1 `serverState` store

**职责**：存储来自服务端的完整权威播放状态快照，是所有播放相关 UI 渲染的唯一数据来源。

**在现有代码中的对应**：替换现有的 `status` state（`PlayingInfo` 类型），但结构完全不同。

**新结构要求**：

`serverState` 的类型应完全对应投影规范第 5 节的字段模型，包含：`device_id`、`revision`、`play_session_id`、`transport_state`、`track`（含 `id`、`title`）、`context`（含 `id`、`name`、`current_index`）、`position_ms`、`duration_ms`、`snapshot_at_ms`。

**更新规则**：

- `serverState` 只能通过 SSE handler 或降级轮询 handler 写入，不得有其他写入路径。
- 写入前必须通过 revision gate（见 3.3 节）。
- 写入时必须替换整个 `serverState` 对象（不得做字段级 merge）。

**与 `v1Api.ts` 的关系**：

`PlayerStateData` 接口需要升级，增加新字段（`revision`、`play_session_id`、`transport_state`、`track`、`context`、`position_ms`、`duration_ms`、`snapshot_at_ms`），并将旧字段（`is_playing`、`cur_music`、`offset`、`duration`）标记为兼容字段，新代码不得读取旧字段。

---

### 3.2 `uiState` store

**职责**：存储纯 UI 辅助状态，不代表任何播放事实，不参与播放状态判断。

**合法内容**（对应状态机规范第 3.2 节）：

| 字段 | 类型 | 说明 | 对应旧实现 |
|---|---|---|---|
| `connectionStatus` | `"connected" \| "reconnecting" \| "fallback_polling"` | SSE 连接状态 | 新增，无对应 |
| `initializing` | `boolean` | 首次快照尚未到达 | 替换 `refreshRestoreUntilRef` 窗口逻辑 |
| `switchingHint` | `boolean` | 切歌过渡动画标记 | 替换旧 `awaitingTrackTitleRef` 的展示侧职责 |
| `progressInterpolation` | `{ baseMs: number, startedAt: number } \| null` | 进度插值基准 | 替换 `localPlaybackStartedAt` 的进度推算职责 |

`uiState` 的写入不受 revision gate 约束，可以独立更新。

---

### 3.3 SSE handler

**职责**：建立并维护 `GET /api/v1/player/stream` 的 SSE 连接，接收 `player_state` 事件，将快照数据写入 `serverState`（经 revision gate）。

**在现有代码中的对应**：无对应。当前 `HomePage.tsx` 没有 SSE 连接逻辑，全部通过 `loadStatus()` 轮询。

**实现位置建议**：建议抽取为独立的自定义 hook（`usePlayerStream`），在 `HomePage` 挂载时初始化，在 `activeDid` 变化时重新连接。

**SSE handler 的职责范围**：

- 建立 `EventSource` 连接，参数为 `device_id=<activeDid>`。
- 监听 `player_state` 事件，解析 `event.data` 为状态快照对象。
- 将快照对象送入 revision gate，通过后写入 `serverState`，触发 session switch handler（见 3.5 节）。
- 监听连接状态（`onopen`、`onerror`），更新 `uiState.connectionStatus`。
- 连接断开时启动降级轮询（见 3.4 节）。
- 重连成功后停止降级轮询，重置 `uiState.connectionStatus = "connected"`。

---

### 3.4 revision gate

**职责**：对收到的每一份状态快照执行 `revision` 去重检查，决定是否应用到 `serverState`。

**在现有代码中的对应**：`statusRequestSeqRef` + `isLatestStatusRequest()` 承担了部分"丢弃过期响应"的职责，但逻辑不同（旧逻辑基于请求序列号，新逻辑基于 `revision`）。

**实现规则**（对应状态机规范第 6 节）：

- 维护 `lastAppliedRevision`，初始值为 `-1`。
- 收到快照时，若 `snapshot.revision > lastAppliedRevision`，更新 `lastAppliedRevision` 并应用快照。
- 若 `snapshot.revision <= lastAppliedRevision`，丢弃快照。
- 服务端重启场景（`revision` 从 0 重新开始）：检测到 `revision` 显著回退（例如小于 `lastAppliedRevision` 的 10%）时，重置 `lastAppliedRevision = -1`，接受新快照。

**实现位置建议**：可作为 SSE handler hook 内部的辅助函数，或抽取为独立的 `applySnapshot(snapshot)` 函数供 SSE handler 和降级轮询共同调用。

---

### 3.5 session switch handler

**职责**：检测 `serverState.play_session_id` 变化，触发全套 UI 切换动作。

**在现有代码中的对应**：分散在 `mergePlayingViewState` 的 boundary 检测、`lastAutoSyncedPlayingSongRef` 的更新、进度条重置等多处，无统一入口。

**触发条件**：`serverState` 被 revision gate 应用后，若新 `play_session_id` 不等于旧 `play_session_id`，则触发 session switch。

**session switch handler 的职责**（对应状态机规范第 7.1 节）：

- 重置 `uiState.progressInterpolation`（进度归零）。
- 标记 `uiState.switchingHint = true`（触发切歌过渡动画）。
- 触发歌单选中项重定位（以新 `serverState.track.id` 为依据）。
- 若新 `transport_state == "playing"`，清除 `uiState.switchingHint`。

---

## 4. 数据流重构

### 4.1 旧数据流

```
定时器触发（350ms~1000ms）
  → GET /api/v1/player/state（HTTP 轮询）
  → 响应到达
  → isLatestStatusRequest() 序列号检查
  → mergePlayingViewState()（本地合并：边界检测 + 进度推算 + 标题确认）
  → stability window 稳定性补偿
  → pending 乐观更新覆盖
  → setStatus()（写入 React state）
  → currentMusicName（三层 fallback 派生）
  → 渲染
  
本地计时器（250ms）
  → 独立推进 offset（基于 localPlaybackStartedAt）
  → 通过 safeOffset/safeDuration 影响进度条渲染
```

### 4.2 新数据流

```
SSE 连接（持久，GET /api/v1/player/stream）
  → 服务端推送 player_state 事件
  → 解析 event.data 为状态快照
  → revision gate：snapshot.revision > lastAppliedRevision?
      否 → 丢弃，结束
      是 → 更新 lastAppliedRevision
  → 检测 play_session_id 是否变化
      是 → session switch handler（进度归零、选中项重定位、切歌动画）
  → 更新 serverState（整体替换）
  → 渲染（基于 serverState 字段直接映射）
  
进度插值定时器（60fps 或 250ms，仅在 transport_state == "playing" 时运行）
  → 基于 serverState.position_ms + serverState.snapshot_at_ms 计算插值进度
  → 仅更新 uiState.progressInterpolation，不写 serverState
  → 渲染进度条（基于 progressInterpolation）
  
降级轮询（仅在 SSE 断线时运行，间隔 ≥ 3s）
  → GET /api/v1/player/state
  → 同上：revision gate → session switch handler → 更新 serverState
  → SSE 重连成功后立即停止
```

### 4.3 数据流关键约束

- `serverState` 只有一个写入点：revision gate 通过后的应用逻辑。
- `uiState` 的写入独立于 `serverState` 的写入，不在同一处理链路中。
- 渲染函数只读取 `serverState` 和 `uiState`，不做任何计算或推断。
- 命令接口（`playSongByName`、`switchTrack`）发出后，只更新 `uiState`（过渡标记），不写 `serverState`。

---

## 5. 关键字段替换映射

本章逐条说明旧字段到新字段的替换关系，用于指导 `v1Api.ts` 接口升级和 `HomePage.tsx` 渲染代码改写。

### 5.1 `offset` → `position_ms`

| 维度 | 旧实现 | 新实现 |
|---|---|---|
| 字段来源 | `status.offset`（秒，integer） | `serverState.position_ms`（毫秒，integer） |
| 进度条基准 | `safeOffset`（本地合并后的秒值） | `serverState.position_ms`（直接读取） |
| 进度插值 | 基于 `localPlaybackStartedAt`（时刻戳）+ `elapsed` | 基于 `serverState.snapshot_at_ms`（时刻戳）+ 本地时钟差值 |
| 兼容旧字段 | - | 若旧 `offset` 仍出现，`position_ms = offset * 1000` |

### 5.2 `duration` → `duration_ms`

| 维度 | 旧实现 | 新实现 |
|---|---|---|
| 字段来源 | `status.duration`（秒） | `serverState.duration_ms`（毫秒） |
| 未知时长 | `duration == 0` 时进度条异常 | `duration_ms == 0` 时展示不确定进度条 |
| 兼容旧字段 | - | 若旧 `duration` 仍出现，`duration_ms = duration * 1000` |

### 5.3 `is_playing` → `transport_state`

| 旧判断 | 新判断 |
|---|---|
| `status.is_playing === true` | `serverState.transport_state === "playing"` |
| `status.is_playing === false` | `serverState.transport_state` 为 `"paused"` / `"stopped"` / `"idle"` / `"error"` |
| 无等价 | `serverState.transport_state === "switching"`（切歌中） |
| 无等价 | `serverState.transport_state === "starting"`（加载中） |
| `playbackText = "正在播放：xxx"` | 基于 `transport_state` 枚举值选择文案 |

进度条推进条件：`transport_state === "playing"` 时允许插值，其他状态停止插值。

### 5.4 `cur_music` → `track.title`

| 维度 | 旧实现 | 新实现 |
|---|---|---|
| 展示标题 | `currentMusicName`（三层 fallback） | `serverState.track?.title \|\| 占位符` |
| 标题为空时 | 显示"未知歌曲" | `transport_state == "switching"/"starting"` 时显示过渡占位符 |
| 歌单选中项同步 | 基于 `cur_music` 文本在列表中搜索 | 基于 `track.id` 在列表中对照（见 5.5 节） |
| 写入 remembered | 标题非空时随时写 | 仅 `transport_state == "playing"` 时，且仅供 `uiState.initializing` 期间使用 |

### 5.5 `cur_music` 文本匹配 → `track.id` 对照

旧的歌单选中项同步逻辑：

```
playingName = status.cur_music.trim()
→ 在 playlists[playlist] 数组中查找 === playingName 的项
→ 找到则 setMusic(playingName)
```

新的歌单选中项同步逻辑：

```
serverState.track.id
→ 在歌单数据结构中查找 track.id 对应的项
→ 找到则更新选中状态
```

触发时机：`play_session_id` 变化且 `transport_state === "playing"` 时执行，其他时机（`switching`、`starting`、`idle`）不执行。

### 5.6 `offset` 回退 / `duration` 突变 / `cur_music` 变化 → `play_session_id` 变化

旧切歌边界检测：

```
trackIdChanged || indexChanged || contextChanged || songChangedByHeuristic
|| (durationChanged && atBoundary)
```

新切歌边界检测：

```
newSnapshot.play_session_id !== serverState.play_session_id
```

直接比较，无需任何启发式判断。

---

## 6. UI 触发点定义

本章定义新模型下各关键 UI 动作的触发条件，替代旧模型中分散的触发逻辑。

### 6.1 session 变化 → 全 UI 切换

**触发条件**：`newSnapshot.play_session_id !== serverState.play_session_id`

**执行动作**：

1. 进度条归零（`uiState.progressInterpolation = { baseMs: 0, startedAt: Date.now() }`）
2. 标记切歌动画（`uiState.switchingHint = true`）
3. 触发歌单选中项重定位（等待 `transport_state == "playing"` 时执行）
4. 标题更新：若新 `track.title` 非空立即更新，若为空展示过渡占位符

### 6.2 `transport_state` → UI 大状态

| `transport_state` | 播放按钮状态 | 进度条行为 | 歌名展示 | 其他 |
|---|---|---|---|---|
| `idle` | 停止 | 归零，静止 | 清空 | - |
| `starting` | 加载中 | 静止 | 过渡占位符（若 `track` 非空展示 `track.title`） | 展示加载指示器 |
| `switching` | 切换中 | 静止 | 过渡展示（保留上一 session 标题，标注"切换中"） | `uiState.switchingHint = true` |
| `playing` | 播放中 | 插值推进 | `track.title` | 清除 `switchingHint` |
| `paused` | 暂停 | 静止 | `track.title` | - |
| `stopped` | 停止 | 静止 | 可保留最后曲目信息 | - |
| `error` | 出错 | 静止 | 可展示出错时的曲目名 | 展示错误提示，不自动恢复 |

### 6.3 `revision` → 更新门槛

`revision` 不直接触发 UI，但决定状态快照是否被应用。只有通过 revision gate 的快照才能流入后续的 session switch handler 和 `serverState` 更新，进而触发 UI 渲染。

revision gate 失败（快照被丢弃）时，UI 不发生任何变化。

---

## 7. 删除清单

以下变量、函数、逻辑必须从 `HomePage.tsx` 中删除，不得保留为"暂时不用的旧代码"。

### 7.1 必须删除的 React state

| 变量名 | 删除理由 |
|---|---|
| `localPlaybackSong` | 新模型中标题来源唯一化为 `serverState.track.title` |
| `rememberedPlayingSong`（主路径使用） | 仅保留为 `uiState.initializing` 期间的过渡展示，不作为 state 参与渲染逻辑 |

### 7.2 必须删除的 Ref

| Ref 名 | 删除理由 |
|---|---|
| `localPlaybackSongRef` | 对应 state 已删除 |
| `rememberedPlayingSongRef`（主路径使用） | 对应使用场景已收敛 |
| `lastPositivePlaybackAtRef` | 稳定性窗口机制删除 |
| `stopSuppressUntilRef` | 停止抑制机制删除 |
| `awaitingTrackTitleRef` | 由 `play_session_id` 取代 |
| `lastConfirmedSongRef` | 由 `play_session_id` + `track.id` 取代 |
| `lastBoundaryAtRef` | 由 `play_session_id` 取代 |
| `fastPollUntilRef` | SSE 主通道不需要加速轮询 |
| `pendingPlayRef` | 乐观更新机制删除 |

### 7.3 必须删除的函数 / 逻辑块

| 函数 / 逻辑 | 删除理由 |
|---|---|
| `mergePlayingViewState()`（整体） | 本地状态合并逻辑，新模型由服务端快照直接提供 |
| 稳定性窗口逻辑（`withinStabilityWindow` 分支） | 服务端 `transport_state` 取代前端抑制逻辑 |
| `pendingPlay` 乐观更新分支 | 命令不再乐观更新 `serverState` |
| `mergedSong !== prevConfirmedSong` 标题过滤逻辑 | 由 `play_session_id` 切换机制取代 |
| `triggerFastPolling()` 及快速轮询切换 | SSE 是推送模型，无需加速轮询 |
| `currentMusicName` 的 `rememberedPlayingSong` 和 `localPlaybackSong` fallback 分支 | 仅保留 `serverState.track?.title` 读取 |
| `playbackSnapshotKey()` 的完整写入链路（在轮询中更新 snapshot） | 降级保留读取（`uiState.initializing` 用），删除写入链路 |

### 7.4 必须删除的 `v1Api.ts` 旧字段读取

新代码不得读取以下兼容字段（即使后端仍返回）：

- `data.is_playing` → 改读 `data.transport_state`
- `data.cur_music` → 改读 `data.track?.title`
- `data.offset` → 改读 `data.position_ms`
- `data.duration` → 改读 `data.duration_ms`

---

## 8. 保留与改造清单

### 8.1 保留的函数（需更新输入字段）

| 函数 / 逻辑 | 保留理由 | 需要更新的内容 |
|---|---|---|
| 设备切换 effect | 设备切换初始化逻辑仍需要 | 改造为：关闭旧 SSE 连接 → 重置 `serverState` 和 `lastAppliedRevision` → 标记 `uiState.initializing` → 获取初始快照 → 建立新 SSE |
| 歌单选中项同步 effect | 功能保留 | 触发条件改为 `play_session_id` 变化且 `transport_state === "playing"`；查找逻辑改为 `track.id` 对照 |
| `playSongByName()` 命令部分 | 命令发送功能保留 | 删除乐观更新，改为发出命令后标记 `uiState` 过渡状态，等待 SSE 事件 |
| `switchTrack()` 命令部分 | 命令发送功能保留 | 同上，删除 baseline 对比等待逻辑 |
| `loadRememberedPlayingSong()` | 供 `uiState.initializing` 期间使用 | 仅在初始化时调用一次，结果赋值给局部变量，不写入 state |
| `saveRememberedPlayingSong()` | 保留写入 localStorage | 调用时机收紧：仅在 `serverState.transport_state === "playing"` 时写入 |
| 降级轮询逻辑 | SSE 断线时的 fallback | 保留 HTTP 轮询调用链，但移除 `mergePlayingViewState`；轮询响应直接送入 revision gate |
| `statusRequestSeqRef`（慢请求保护） | 仅用于降级轮询 | 仅在降级轮询 handler 中使用，SSE handler 不需要 |

### 8.2 保留的渲染输出（需更新输入来源）

| 渲染输出 | 原输入 | 新输入 |
|---|---|---|
| 歌名展示 | `currentMusicName`（三层 fallback） | `serverState.track?.title` |
| 播放状态图标 / 文案 | `status.is_playing` | `serverState.transport_state` |
| 进度条进度 | `progress`（基于 `safeOffset / safeDuration`） | 基于 `uiState.progressInterpolation` 推算 |
| 进度条时间文字 | `safeOffset` / `safeDuration`（秒） | `position_ms / 1000` / `duration_ms / 1000`（注意单位转换） |
| 歌单选中高亮 | `music === itemName`（文本比较） | `serverState.track?.id === item.id`（ID 比较） |
| 切歌动画 | `awaitingTrackTitleRef` + `localPlaybackSong` 重置 | `uiState.switchingHint` |
| 断线提示 | 无 | `uiState.connectionStatus !== "connected"` |

---

## 9. 实现建议

### 9.1 React state 拆分建议

建议将当前 `HomePage` 中混合在一起的状态拆分为以下两个独立的 state 块：

**`serverState`**（建议用 `useReducer` 管理，确保整体替换）：

包含投影规范定义的完整快照字段。`useReducer` 的 action 类型只有一种：`APPLY_SNAPSHOT`，附带完整快照对象。dispatch 前由 revision gate 过滤。

**`uiState`**（建议用 `useState` 管理）：

包含 `connectionStatus`、`initializing`、`switchingHint`、`progressInterpolation` 四个字段，可以独立更新任意字段。

### 9.2 hook 结构建议

建议将 SSE 连接和状态消费逻辑抽取为独立 hook，避免 `HomePage` 继续承载过多逻辑：

**`usePlayerStream(deviceId, onSnapshot)`**：

- 封装 SSE 连接生命周期（建立、断线重连、页面可见性变化处理）。
- 每次收到 `player_state` 事件时调用 `onSnapshot(snapshot)`。
- 管理降级轮询（SSE 断线时自动启动，重连后自动停止）。
- 暴露 `connectionStatus` 给调用方。

`HomePage` 侧：

- 调用 `usePlayerStream(activeDid, handleSnapshot)`。
- `handleSnapshot` 内执行 revision gate → session switch handler → dispatch `APPLY_SNAPSHOT`。

**`useProgressInterpolation(serverState)`**：

- 接收 `serverState` 中的 `position_ms`、`duration_ms`、`snapshot_at_ms`、`transport_state`。
- 在 `transport_state === "playing"` 时启动插值定时器。
- 返回当前插值后的 `currentPositionMs`，供进度条渲染使用。

### 9.3 重构顺序建议

建议按以下顺序推进重构，每步均可独立验证：

**第一步：升级 `PlayerStateData` 接口**

在 `v1Api.ts` 中为 `PlayerStateData` 新增投影规范定义的字段（兼容旧字段保留但标注为 deprecated）。此步骤不改变任何行为，只增加类型定义。

**第二步：实现 `usePlayerStream` hook**

实现 SSE 连接、降级轮询切换、心跳处理、重连逻辑。先使用 `console.log` 验证快照是否正确到达，不接入 `serverState`。

**第三步：接入 revision gate 和 `serverState`**

将 revision gate 逻辑加入 `handleSnapshot`，将通过 gate 的快照 dispatch 到 `serverState`。此时 `HomePage` 同时存在旧的 `status` 和新的 `serverState`，可以对照验证数据一致性。

**第四步：实现 session switch handler**

基于 `play_session_id` 变化触发 `uiState` 更新（进度归零、切歌动画、选中项重定位）。

**第五步：改写渲染层**

将 `currentMusicName`、进度条、歌单选中项等渲染输出逐一切换到新字段来源，同时删除旧的 fallback 逻辑。

**第六步：清理旧代码**

按第 7 章删除清单逐项删除旧变量、函数和逻辑块。删除旧的 `status` state，确保代码只使用 `serverState` 和 `uiState`。

---

## 10. 与规范文档的关系

- **`docs/spec/webui_playback_state_machine_spec.md`**（消费型状态机规范）：本文档的上位规范，定义"应该是什么"。本文档定义"当前实现怎么改"。
- **`docs/spec/player_state_projection_spec.md`**（投影规范）：定义 `serverState` 的字段模型与语义，是 `PlayerStateData` 接口升级的依据。
- **`docs/spec/player_stream_sse_spec.md`**（SSE 规范）：定义 `usePlayerStream` hook 的连接行为、事件格式、重连机制，是 SSE handler 实现的依据。
- 本文档中描述的旧实现结构（`mergePlayingViewState`、`stabilityWindow`、`localPlayback*` 等），属于已废弃的旧推测型模型，不得再作为新实现的参考。
