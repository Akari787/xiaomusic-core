# WebUI 播放状态机规范（消费型）

版本：v1.0
状态：正式规范
最后更新：2026-03-22
适用范围：`xiaomusic/webui/src/pages/HomePage.tsx` 及所有消费播放状态的 WebUI 模块

---

## 1. 文档定位

本文档定义 WebUI 播放状态机的正式规范。

本文档所描述的状态机是**消费型状态机**，不是推测型状态机。其核心约束是：

- **前端不拥有播放真相。** 播放的权威状态由服务端唯一决定，通过 `GET /api/v1/player/stream`（SSE 主通道）或 `GET /api/v1/player/state`（初始化与降级通道）下发。
- **前端只负责渲染状态。** 前端的职责是忠实消费服务端状态快照，将其映射为可见 UI，不得对服务端状态做重新裁决、补充或覆盖。
- **本地状态只服务于 UI 过渡。** 前端维护的本地状态（加载中、切歌动画缓冲、断线重连提示等）是纯粹的展示辅助，不得影响对"当前播放何首歌曲"的判断。

本文档不定义后端 API 契约，不定义播放状态字段的语义。上述内容分别由以下文档承接：

- `docs/api/api_v1_spec.md`：v1 API 正式契约
- `docs/spec/player_state_projection_spec.md`：权威播放状态快照的字段模型与语义（以下简称"投影规范"）
- `docs/spec/player_stream_sse_spec.md`：SSE 传输协议（以下简称"SSE 规范"）

当本文档与上述文档存在冲突时，以上述文档为准。

---

## 2. 模型根本转变

### 2.1 旧模型（推测型）已废弃

旧 WebUI 实现采用推测型状态机，其特征包括：

- 以 `is_playing`、`cur_music`、`offset`、`duration` 四个字段为输入，在前端自行合并为"展示状态"。
- 通过轮询 `GET /api/v1/player/state` 获取状态，轮询间隔期间使用本地计时器补偿进度。
- 依靠 `offset` 回退、`duration` 突变、`cur_music` 变化等信号推断切歌边界。
- 通过稳定性窗口（`lastPositivePlaybackAtRef`）抑制后端抖动。
- 通过 `rememberedPlayingSong`、`localPlaybackSong`、`pendingTitle` 等机制在切歌过程中维持或预测标题。

以上机制整体属于旧模型，在新规范下均视为废弃。旧模型中的具体问题见第 13 章。

### 2.2 新模型（消费型）

新 WebUI 状态机采用消费型模型，其基本原则是：

1. **SSE 是唯一主动状态来源。** 前端通过持久 SSE 连接（`GET /api/v1/player/stream`）接收服务端推送的状态快照事件，不再依赖周期性轮询作为主状态来源。
2. **服务端状态快照是唯一权威。** 每一条 `player_state` SSE 事件携带完整的权威状态快照，前端直接以此为渲染输入，不做二次推断。
3. **`transport_state` 是展示决策的首要依据。** 前端不再通过 `offset` 回退或 `cur_music` 变化推断播放状态，所有状态均从 `transport_state` 枚举值直接读取。
4. **`play_session_id` 是曲目切换的唯一边界信号。** 切歌边界不再通过时间轴信号推断，而是通过 `play_session_id` 的变化直接触发。
5. **`track.id` 是曲目身份的唯一标识。** 选中项同步、切歌确认、进度条绑定均以 `track.id` 为依据，不以 `track.title` 文本匹配为依据。
6. **`revision` 是去重与顺序保证的唯一依据。** 前端通过 `revision` 丢弃过期快照，不依赖事件到达顺序。

---

## 3. 状态来源模型

前端维护两类状态，两类状态的边界必须严格分离。

### 3.1 serverState（唯一权威来源）

`serverState` 是来自服务端的完整权威播放状态快照，其字段模型完全由投影规范第 5 节定义。

`serverState` 的唯一合法来源：

- **主通道**：`GET /api/v1/player/stream` 的 `player_state` SSE 事件携带的 `data` 字段。
- **降级通道**：SSE 断线期间对 `GET /api/v1/player/state` 的轮询响应中的 `data` 字段。

`serverState` 的更新规则：

- 收到新状态快照时，必须先执行 `revision` 去重（见第 6 节），通过检查后才能更新 `serverState`。
- `serverState` 一旦更新，UI 必须立即以新 `serverState` 为准重新渲染，不得延续旧状态的任何字段。
- 前端不得对 `serverState` 的任何字段做修改、补全或覆盖。`serverState` 必须原样保存服务端下发的快照内容。

`serverState` 必须包含的字段（字段语义见投影规范）：

- `device_id`
- `revision`
- `play_session_id`
- `transport_state`
- `track`（对象或 `null`）
- `context`（对象或 `null`）
- `position_ms`
- `duration_ms`
- `snapshot_at_ms`

### 3.2 uiState（本地短暂状态）

`uiState` 是纯粹的 UI 辅助状态，用于在 `serverState` 尚未到达或正在过渡时提供合理的展示体验。`uiState` 不代表任何播放事实。

`uiState` 的合法内容：

| 字段 | 说明 | 有效期 |
|---|---|---|
| `connectionStatus` | SSE 连接状态：`connected` / `reconnecting` / `fallback_polling` | 持续有效 |
| `initializing` | 页面初次加载、首次快照尚未到达 | 首次 `serverState` 到达后立即清除 |
| `switchingHint` | 收到 `transport_state == "switching"` 时的切歌动画缓冲标记 | 收到 `play_session_id` 变化且 `transport_state == "playing"` 后清除 |
| `progressInterpolation` | 在两次快照之间基于本地时钟推算的进度插值 | 收到新 `serverState` 后立即用 `position_ms` 覆盖 |

以下内容明确不属于 `uiState` 的合法内容：

- 任何形式的"前端认定的当前歌曲"
- 任何形式的"前端推断的播放状态"
- 基于旧快照的标题缓存，用于在新快照到达前"维持"展示

---

## 4. SSE 驱动模型

### 4.1 SSE 是状态更新的驱动源

前端的 `serverState` 更新，必须完全由 SSE 事件驱动。每当收到一条 `player_state` 事件，前端执行以下处理序列：

1. 解析事件 `data` 字段，得到状态快照对象。
2. 执行 `revision` 去重检查（见第 6 节）。
3. 通过检查后，以完整快照替换本地 `serverState`。
4. 基于新 `serverState` 执行 UI 更新（见第 5 章）。

此处理序列是原子的，不得在中间插入任何本地状态合并逻辑。

### 4.2 禁止并行状态来源

在 SSE 连接正常运行期间，前端不得同时对 `GET /api/v1/player/state` 发起轮询。两路来源并行运行会导致不同 `revision` 的快照交替写入 `serverState`，产生状态竞争。

以下行为明确禁止：

- SSE 连接正常时，仍以固定间隔轮询 `/player/state` 并将结果合并到 `serverState`。
- 以"SSE 可能不及时"为由保留并行轮询链路。
- 将 SSE 事件与 HTTP 轮询响应合并后，由前端自行裁决哪一份"更准确"。

### 4.3 禁止本地猜测当前歌曲

前端不得通过任何本地逻辑猜测或推断"当前正在播放哪首歌曲"。具体禁止行为包括：

- 通过 `offset` 值接近 0 推断"新歌已开始"。
- 通过 `duration` 突变推断"切歌已发生"。
- 通过 `cur_music` / `track.title` 文本变化推断"新歌标题"。
- 通过超时等待推断"切歌应该已完成"。
- 通过上下文中的歌单位置推断"下一首应该是哪首"。

所有此类信息必须等待服务端快照到达。

---

## 5. UI 渲染规则

本章定义如何将 `serverState` 映射为可见 UI。所有渲染决策必须以 `serverState` 中的字段为唯一依据。

### 5.1 渲染决策的优先字段顺序

前端在渲染时必须按以下字段优先级顺序做决策：

1. 首先读取 `transport_state`，确定当前所处的传输阶段。
2. 基于 `transport_state` 决定 UI 大状态（播放中、暂停、切歌中、加载中、空闲、出错）。
3. 在大状态确定后，读取 `play_session_id` 判断当前 session 是否已切换（驱动 UI 切换动作）。
4. 读取 `track.id` 驱动歌单选中项同步与进度条绑定。
5. 读取 `track.title` 作为展示文本（标题展示）。
6. 读取 `position_ms`、`duration_ms`、`snapshot_at_ms` 驱动进度条。

### 5.2 各 transport_state 的 UI 行为

#### `idle`

- 展示：播放器无活跃播放，进度条归零，不展示曲目信息。
- 歌单选中项：不自动更新，保持用户最后一次手动选择的状态。
- 禁止行为：不得展示"未知歌曲"，不得用历史标题填充。

#### `starting`

- 展示：显示"加载中"状态标记（通过 `uiState.initializing` 或专用加载指示器）。
- 若快照中 `track` 非空，可展示 `track.title`（即将播放的曲目）；若 `track` 为 `null`，应展示加载占位符，不得展示"未知歌曲"。
- 进度条：归零或保持静止，不推进插值。
- 禁止行为：不得以旧 `play_session_id` 对应的曲目信息填充展示区。

#### `switching`

- 展示：标记 `uiState.switchingHint = true`，可在 UI 上展示切歌过渡效果（如歌名淡出、进度条重置动画）。
- 若快照中 `track` 非空，可展示即将播放的曲目信息；若 `track` 为 `null`，应保持上一个 `play_session_id` 的曲目信息作为过渡展示（仅展示用，不更新歌单选中项）。
- 禁止行为：不得以"未知歌曲"代替过渡展示；不得在 `switching` 期间更新歌单选中项；不得推进进度插值。

#### `playing`

- 展示：正常播放状态。展示 `track.title`，进度条运行。
- 清除 `uiState.switchingHint`。
- 歌单选中项：以 `track.id` 为依据，将歌单列表中对应项高亮。
- 进度条：基于 `position_ms` 和 `snapshot_at_ms` 进行进度插值（见第 9 节）。
- 若此时 `play_session_id` 相比上一次 `playing` 状态发生了变化，必须触发完整 UI 切换（标题更新、进度归零、选中项重定位）。

#### `paused`

- 展示：暂停状态。展示 `track.title`，进度条静止，不推进插值。
- 歌单选中项：保持当前 `track.id` 对应项高亮。
- 禁止行为：不得以任何本地逻辑推断"暂停时间够长是否视为停止"。

#### `stopped`

- 展示：停止状态。如快照中 `track` 非空，可展示最后播放的曲目信息；如 `track` 为 `null`，展示器清空。
- 进度条：静止。
- 歌单选中项：保持，不自动清除。

#### `error`

- 展示：展示明确的错误状态提示，不得展示"正在播放"或"加载中"字样。
- 若快照中 `track` 非空，可展示曲目名（表示出错时正在播放的曲目）。
- 禁止行为：不得通过轮询尝试自动从 `error` 状态恢复；错误恢复应由用户动作或服务端新事件驱动。

---

## 6. revision 处理规则

### 6.1 去重规则

前端必须维护一个 `lastAppliedRevision` 值，初始为 `-1`。

每次收到状态快照时，执行以下检查：

- 若快照的 `revision` **严格小于** `lastAppliedRevision`：丢弃该快照，不更新 `serverState`，不触发任何 UI 更新。
- 若快照的 `revision` **等于** `lastAppliedRevision`：视为重复快照，丢弃，不触发 UI 重渲染。
- 若快照的 `revision` **大于** `lastAppliedRevision`：应用该快照，更新 `serverState`，更新 `lastAppliedRevision`，触发 UI 更新。

### 6.2 禁止回写旧 revision

`lastAppliedRevision` 只允许向前推进，不允许回退。以下情况下不得降低 `lastAppliedRevision`：

- 页面可见性恢复（切回前台）后收到旧快照。
- SSE 重连后，重连前积压的旧事件延迟到达。
- 降级轮询期间，某次轮询响应的 `revision` 小于 SSE 之前已应用的 `revision`。

### 6.3 服务端重启后的 revision 处理

若服务端重启后 `revision` 从 0 重新开始，前端收到的新快照 `revision` 可能小于 `lastAppliedRevision`。此时前端不应拒绝该快照，而应识别为服务端重启场景，重置 `lastAppliedRevision` 并应用新快照。

识别服务端重启的信号：SSE 连接重建后收到的初始快照中，`revision` 为 0 或极小值，且与之前已知的 `revision` 存在明显的不连续性。具体识别逻辑由实现侧决定，但必须有此处理，不得因为 `revision` 回退而永远拒绝服务端状态。

---

## 7. play_session_id 行为规则

### 7.1 play_session_id 是曲目切换的唯一边界信号

前端必须监听 `play_session_id` 的变化。当检测到 `play_session_id` 与上一次已应用快照的值不同时，视为发生了曲目切换，必须执行以下 UI 动作：

- **进度条重置**：`position_ms` 归零，进度插值从新 `snapshot_at_ms` 重新开始。
- **标题更新**：以新快照的 `track.title` 更新展示标题，若 `track` 为 `null` 则展示占位符。
- **歌单选中项重定位**：以新快照的 `track.id` 在歌单列表中定位，高亮对应项（见第 8 节）。
- **切歌动画触发**：标记 `uiState.switchingHint`，触发切歌过渡视觉效果。

以下操作不得以 `play_session_id` 未变化为由跳过：

- 若 `transport_state` 从非 `playing` 变为 `playing`，且 `play_session_id` 已变化，必须触发完整 UI 切换。

### 7.2 同一 session 内的状态变化

当 `play_session_id` 未变化时，收到新快照表示同一曲目的状态更新（如暂停、恢复、进度推进）。此时：

- 不得重置进度条（除非 `position_ms` 明显回退，表示后端发生了 seek）。
- 不得更新歌单选中项（已在 `playing` 状态时定位完成）。
- 应更新 `track.title`（处理 metadata 延迟到达的情况）。
- 应更新 `transport_state` 对应的 UI 大状态。

---

## 8. track.id 驱动 UI 规则

### 8.1 歌单选中项必须以 track.id 为依据

前端在更新歌单列表的当前播放高亮项时，必须以 `track.id` 为依据，在歌单数据结构中查找对应项，并更新高亮状态。

以下做法不得使用：

- 以 `track.title`（标题文本）在歌单列表中进行字符串匹配定位选中项。
- 以 `context.current_index` 直接作为歌单列表的数组下标（`current_index` 可用作辅助参考，但不能是唯一依据）。
- 在 `track.id` 未变化的情况下，仅因 `track.title` 变化而重新定位选中项（这属于 metadata 更新，不是曲目切换）。

### 8.2 歌单选中项的更新时机

歌单选中项应在以下时机更新：

- `play_session_id` 发生变化，且新快照的 `transport_state` 为 `playing`（切歌已确认完成）。
- 页面首次加载收到初始快照，且 `transport_state` 为 `playing` 或 `paused`。

在以下时机不得更新歌单选中项：

- `transport_state` 为 `switching` 期间。
- `transport_state` 为 `starting` 期间。
- `transport_state` 为 `idle` 或 `stopped`。

### 8.3 title 仅用于展示，不参与逻辑判断

`track.title` 只用于向用户展示曲目名称，不得参与以下逻辑判断：

- 判断曲目是否发生切换（应使用 `play_session_id` 和 `track.id`）。
- 判断切歌是否完成（应使用 `play_session_id` + `transport_state == "playing"`）。
- 判断歌单选中项（应使用 `track.id`）。

---

## 9. 进度条规则

### 9.1 进度条的权威基准

进度条的位置必须以 `serverState.position_ms` 为权威基准，以 `serverState.snapshot_at_ms` 为该基准值的观测时刻。

### 9.2 允许的短时进度插值

在两次快照之间，前端可以基于本地时钟对进度做线性插值，使进度条展示更平滑：

```
当前展示位置 = position_ms + (本地当前时间戳 - snapshot_at_ms)
```

进度插值的约束：

- 仅在 `transport_state == "playing"` 时允许正向插值。
- `transport_state` 为 `paused`、`switching`、`starting`、`stopped`、`idle`、`error` 时，不得推进插值，进度条应静止。
- 插值时长不得超过两次快照之间的合理间隔（建议不超过 5 秒）；超出后停止推进，等待下一次快照。
- 插值结果不得上报或写回服务端。

### 9.3 新快照到达时的覆盖规则

收到新的有效快照（通过 `revision` 去重检查）时，必须立即执行以下操作：

- 停止当前正在运行的进度插值。
- 以新快照的 `position_ms` 为基准重新初始化进度条。
- 以新快照的 `snapshot_at_ms` 为新的观测起点。

不得因为插值结果与 `position_ms` 相差不大而跳过此覆盖。

### 9.4 `duration_ms` 的处理

`duration_ms` 为 `0` 时表示时长未知，不得将其解释为"播放出错"或"曲目时长为零"。此时进度条应显示为不确定状态（如无限进度条或隐藏时长标签），而不是显示 `0:00 / 0:00`。

---

## 10. 降级策略

### 10.1 SSE 断线时的降级轮询

当 SSE 连接断开且重连尚未成功时，前端应：

1. 标记 `uiState.connectionStatus = "reconnecting"`，在 UI 上给出断线重连提示。
2. 启动对 `GET /api/v1/player/state` 的降级轮询，轮询间隔不低于 3 秒。
3. 降级轮询收到的响应，使用与 SSE 事件相同的处理逻辑（`revision` 去重 → 更新 `serverState` → UI 更新）。

### 10.2 SSE 重连后停止轮询

SSE 重连成功并收到初始 `player_state` 事件后，前端必须：

1. 立即停止降级轮询。
2. 标记 `uiState.connectionStatus = "connected"`。
3. 以 SSE 初始事件的快照更新 `serverState`（执行正常的 `revision` 去重）。

重连后不得继续维持轮询作为"双保险"。

### 10.3 轮询不得替代 SSE 作为主通道

即便轮询在功能上可以工作，前端也必须在 SSE 可用时立即切换回 SSE 主通道。不允许以"稳定性"为由长期运行轮询。

---

## 11. 页面生命周期

### 11.1 初始化流程

页面加载时，前端必须按以下顺序执行初始化：

1. 标记 `uiState.initializing = true`，UI 展示加载中状态。
2. 调用 `GET /api/v1/player/state` 获取初始快照，立即渲染初始 UI。
3. 清除 `uiState.initializing`。
4. 建立 `GET /api/v1/player/stream` 的 SSE 连接，订阅当前设备的 `device_id`。
5. 收到 SSE 初始事件（服务端在连接建立后必须立即推送）后，以 SSE 快照覆盖初始化时的 HTTP 快照（执行 `revision` 去重）。
6. 进入 SSE 主通道正常消费模式。

初始化期间若 HTTP 请求失败，应重试或提示用户，不得以失败状态跳过初始化直接进入 SSE。

### 11.2 设备切换

当用户切换 `device_id` 时，前端必须：

1. 关闭当前设备的 SSE 连接。
2. 重置 `serverState` 为空（或加载占位符），重置 `lastAppliedRevision = -1`，清空 `uiState`。
3. 执行与初始化流程相同的步骤（获取新设备初始快照 → 建立新设备 SSE 连接）。

不得在设备切换后将旧设备的 `serverState` 保留用于新设备的过渡展示。

### 11.3 页面可见性变化

当页面从后台切回前台（`visibilitychange` 事件触发且 `document.visibilityState == "visible"`）时，前端应：

1. 检查 SSE 连接状态。若连接已断开，立即触发重连流程（不等待浏览器自动重连超时）。
2. 若当前处于降级轮询模式，继续轮询直至 SSE 重连成功。

页面切入后台期间，前端不得因"可能错过了状态更新"而在切回前台时立即发起额外的 HTTP 状态查询（SSE 重连后的初始快照足以同步最新状态）。

### 11.4 页面卸载

页面卸载时，前端必须主动关闭 SSE 连接，停止降级轮询，清理所有定时器。

---

## 12. 前端消费边界总结

| 操作 | 正确依据 | 禁止依据 |
|---|---|---|
| 判断当前播放状态（是否播放中、暂停等） | `serverState.transport_state` | `is_playing` 布尔值；本地计时器是否运行 |
| 判断当前正在播放哪首歌曲 | `serverState.play_session_id` + `serverState.track.id` | `track.title` 文本；本地记忆的 `rememberedPlayingSong` |
| 展示曲目标题 | `serverState.track.title` | 本地缓存的旧标题；`pendingTitle`；`localPlaybackSong` |
| 歌单选中项高亮定位 | `serverState.track.id` 与列表项 ID 对照 | `track.title` 字符串匹配；`context.current_index` 直接作为数组下标 |
| 判断切歌是否完成 | `play_session_id` 变化 且 `transport_state == "playing"` | 超时猜测；`offset` 接近 0；`cur_music` 文本变化 |
| 进度条初始位置 | `serverState.position_ms`（基准）+ `serverState.snapshot_at_ms`（观测时刻） | 本地计时器累计时间；上次 offset 加推算值 |
| 进度条是否推进 | `transport_state == "playing"` | `is_playing == true` |
| 丢弃过期快照 | `serverState.revision` 比较 | 事件到达顺序 |
| 判断是否处于切歌过渡 | `transport_state == "switching"` | `cur_music` 为空；offset 回退 |

---

## 13. 与旧实现的关系

本章说明当前 `HomePage.tsx` 中哪些逻辑属于旧模型，在新规范下应删除或降级。

### 13.1 应删除的逻辑

以下逻辑属于旧推测型模型的核心机制，在新规范下必须删除：

| 逻辑 / 变量 | 旧模型职责 | 新规范处理方式 |
|---|---|---|
| `rememberedPlayingSong` 参与播放状态判断 | 用于切歌过渡和刷新恢复期间填充当前标题 | 删除；标题由 `serverState.track.title` 提供 |
| `pendingTitle` 类机制 | 在服务端标题尚未到达前"预填"标题 | 删除；`transport_state` 明确表达过渡状态，无需预填 |
| 基于 `offset` 回退推断切歌边界 | 检测 offset 从大值跌到 0 来判断切歌 | 删除；切歌边界由 `play_session_id` 变化直接提供 |
| 基于 `duration` 突变推断切歌 | 通过时长变化辅助判断切歌 | 删除；同上 |
| `lastPositivePlaybackAtRef` 稳定性窗口 | 在后端 `is_playing` 短暂为 false 时维持播放态 | 删除；`transport_state` 由服务端明确维护，不再需要前端抑制 |
| 基于 `cur_music` 文本匹配歌单选中项 | 在歌单列表中搜索当前标题文字以定位选中项 | 删除；改用 `track.id` 对照 |
| 本地轮询作为主状态来源 | 周期性 `GET /player/state` 轮询驱动 UI | 删除；SSE 主通道取代，轮询仅作降级使用 |
| `localPlaybackStartedAt` / `localPlaybackDuration` / `localPlaybackSong` 参与曲目判断 | 在轮询间隙维持播放信息 | 删除；进度插值仅基于 `position_ms` + `snapshot_at_ms`，不维护本地曲目状态 |

### 13.2 应降级的逻辑

以下逻辑在新规范下可以保留，但职责必须缩减至合法范围：

| 逻辑 / 变量 | 旧模型职责 | 新规范合法职责 |
|---|---|---|
| `rememberedPlayingSong` 写入 | 随时写入以备刷新恢复 | 仅可在 `transport_state == "playing"` 时写入，且仅用于页面刷新过渡展示（`uiState.initializing` 期间），不参与任何状态逻辑判断 |
| localStorage 播放缓存 | 页面刷新后恢复 UI 的完整播放状态 | 仅用于 `uiState.initializing` 期间的短暂过渡展示，首次快照到达后必须立即让位于 `serverState` |
| 本地进度计时器 | 在轮询间隙持续推进 offset | 降级为进度插值辅助（见第 9.2 节），仅在 `transport_state == "playing"` 时运行，收到新快照立即停止并以 `position_ms` 覆盖 |

### 13.3 应保留的逻辑

以下逻辑在新规范下保留，但其输入来源需更新为新字段：

| 逻辑 | 新规范下的调整 |
|---|---|
| 歌单列表自动高亮当前播放项 | 输入从 `cur_music` 文本匹配改为 `track.id` 对照 |
| 切歌动画效果 | 触发条件从 offset 回退改为 `play_session_id` 变化 |
| 进度条展示 | 基准从 `offset`（秒）改为 `position_ms`（毫秒）+ `snapshot_at_ms` |
| SSE / 轮询断线提示 | 通过 `uiState.connectionStatus` 驱动，逻辑不变 |

---

## 14. 验收口径

修复实现后，必须满足以下验收标准。

### 14.1 自动切歌

- 切歌发生时，进度条立即归零，不出现回跳或闪烁。
- 切歌发生时，歌单选中项立即切换到新曲目，不停留在旧曲目。
- 切歌过渡期间（`transport_state == "switching"`），展示过渡效果，不展示旧曲目标题为"当前歌曲"。
- 切歌完成后（`play_session_id` 变化且 `transport_state == "playing"`），展示新曲目标题与歌单选中项。

### 14.2 标题可靠性

- 旧曲目标题不得在新曲目开始播放后复现。
- `transport_state == "switching"` 期间，允许展示过渡占位符，但不得展示旧标题为"当前歌曲"。
- `track.title` 为空时，展示加载占位符，不展示"未知歌曲"（除非 `transport_state == "error"`）。

### 14.3 状态一致性

- UI 展示的播放状态（播放中、暂停、停止等）必须与 `serverState.transport_state` 严格对应，不出现前端本地状态与服务端状态不一致的情况。
- 进度条的位置必须与 `serverState.position_ms` 保持一致（允许插值误差不超过 1 秒），不得因本地计时器漂移而与服务端进度偏差超过合理阈值。

### 14.4 SSE 与降级

- SSE 连接正常时，轮询必须停止，UI 不出现两路来源竞争导致的抖动。
- SSE 断线时，自动切换降级轮询，轮询期间 UI 正常工作，并给出断线提示。
- SSE 重连成功后，轮询立即停止，UI 平滑切回 SSE 驱动，不出现闪烁。

### 14.5 revision 去重

- 页面切回前台后，若收到 SSE 重连初始快照的 `revision` 与页面切入后台前已应用的 `revision` 相同，不触发 UI 重渲染。
- 降级轮询期间，收到 `revision` 小于 `lastAppliedRevision` 的快照，不写入 `serverState`，不触发 UI 更新。

---

## 15. 与其他规范的关系

- **`docs/spec/player_state_projection_spec.md`（投影规范）**：本文档的上位规范，定义 `serverState` 的字段模型与语义。本文档所有涉及字段语义的判断，以投影规范为准。
- **`docs/spec/player_stream_sse_spec.md`（SSE 规范）**：定义 SSE 传输协议细节，包括连接行为、事件格式、重连机制与心跳。本文档的 SSE 消费逻辑必须与 SSE 规范保持一致。
- **`docs/api/api_v1_spec.md`（API v1 契约）**：定义 `GET /api/v1/player/state` 与 `GET /api/v1/player/stream` 的接口契约。本文档的降级轮询与初始化逻辑以 API v1 契约为准。
- **`docs/spec/webui_playback_state_machine_mapping.md`（实现映射清单）**：基于旧模型的实现映射文档。该文档描述的旧实现结构（`mergePlayingViewState`、`stabilityWindow`、`localPlayback*` 等）属于本文档第 13 章定义的"应删除或降级"的旧逻辑，不再作为新实现的参考依据。
