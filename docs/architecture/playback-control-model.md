# 播放控制模型

版本：v1.1
状态：现行（迁移中）
最后更新：2026-11-22

本文描述**当前代码**的播放队列、随机会话与上一首/下一首控制边界（服务端逐首下发）。

> **迁移说明**：[ADR-0007](../adr/0007-speaker-owned-queue.md) 已确定改为“音箱持有播放队列，服务端跟随”，正在分步实施（第 1–3 步已完成：队列下发见第 5 节）。第 1、4 节的意图边界（UI 只发意图、next/previous 不重建队列、随机只洗一次）在新架构中继续有效；第 2、4、5 节中关于计时器、`_play_list_items` 和设备导航方法的内容会在对应步骤完成时改写。新增代码按 ADR-0007 的组件划分写，不要再往本文描述的逐首路径上叠加机制。

---

## 1. 核心不变量

1. `music_library` 是歌单成员事实的权威来源。
2. 每台设备的 `XiaoMusicDevice` 是当前运行时播放队列快照、当前索引与播放 session 的唯一权威。
3. WebUI、Home Assistant 与第三方调用方只发送播放意图，不计算下一首或上一首的曲目 ID。
4. 正常的 next/previous 只移动当前队列索引，不重新解析歌单、不重建队列、不重新洗牌。
5. 新播放请求可以建立新 session；控制请求不得隐式建立新 session。
6. 音箱上报状态用于确认投递和完成状态，不得反向改写随机队列顺序。

---

## 2. 状态归属

| 状态 | 权威 | 说明 |
|---|---|---|
| 歌单成员事实 | `music_library` | 提供结构化 membership 与 entity record |
| 当前设备队列快照 | `XiaoMusicDevice._play_list_items` | session 内稳定，不随 next/previous 重建 |
| 当前索引 | `XiaoMusicDevice._current_index` | next/previous 的唯一位置指针 |
| 当前播放 session | `XiaoMusicDevice._play_session_id` | 仅作为 async media session invalidation id，使旧 timer、确认任务和异步回调失效；不承担 queue/command/attempt 语义 |
| runtime phase / lifecycle | `PlaybackRuntimeState` + `LifecycleToken` | runtime phase 是播放状态权威；token 拆分 queue session、command generation、track attempt |
| 异步播放任务 | `PlaybackTaskRegistry` | 每类任务唯一 owner；retry 不允许裸 `asyncio.create_task` |
| 持久播放模式 | `device.play_type` | 用户设备偏好，例如随机、全部循环、单曲循环 |
| 单次随机请求 | `PlayOptions.shuffle` | 只影响本次新 session，不修改 `device.play_type` |
| 播放状态投影 | `PlaybackFacade.build_player_state_snapshot()` | 对外只读投影，不拥有队列 |

`_play_list_items` 是从 `music_library` 建立的运行时快照，不是第二份歌单事实库。歌单内容变化仍由 `music_library` 管理；当前播放 session 是否保持旧顺序由 `XiaoMusicDevice` 决定。

---

## 3. 新随机会话

正式入口：

```http
POST /api/v1/play
Content-Type: application/json

{
  "device_id": "<device_id>",
  "query": "<playlist_name>",
  "source_hint": "auto",
  "options": {
    "shuffle": true
  }
}
```

建立过程：

1. `PlaybackFacade` 从歌单 membership 选择起始成员，并根据 entity record 推断来源。
2. `PlaybackCoordinator` 解析媒体并通过 transport 投递。
3. `XiaoMusicDevice.on_external_url_play()` 建立新的播放 session。
4. `XiaoMusicDevice` 从 `music_library` 复制当前歌单成员，生成设备运行时队列快照。
5. 当 `options.shuffle=true` 或设备持久模式为随机时，只在该新 session 建立时洗牌一次。
6. 当前曲目被定位到该快照中的真实索引。

随机语义是“稳定的打乱队列”，不是每次 next 时随机抽取一首歌。

---

## 4. next/previous 单一控制链

手动 next 的正式调用链：

```text
WebUI / 外部调用方
  → POST /api/v1/control/next
  → PlaybackFacade.next
  → PlaybackCoordinator.next
  → TransportRouter
  → MinaTransport
  → XiaoMusicDevice._play_next(manual=True)
  → current_index + 1
  → 播放队列快照中的目标曲目
```

previous 同理，最终进入 `XiaoMusicDevice._play_prev(manual=True)`。

自动完成路径也必须汇入同一个设备导航入口：

```text
next timer 到期
  → 提交 AUTO_NEXT（reason=timer_track_end）
  → XiaoMusicDevice._play_next(manual=False, auto_advance=True)
```

### 禁止行为

WebUI 点击 next/previous 时不得：

- 根据当前页面歌曲数组自行计算目标曲目；
- 先调用 `POST /api/v1/play` 播放目标曲目；
- 在 next/previous 后重新发送歌单名；
- 重新建立或重新洗牌播放队列；
- 直接依赖音箱原生队列决定 xiaomusic session 的下一首。

`POST /api/v1/play` 表达“建立或替换播放 session”；`POST /api/v1/control/next` 表达“在当前 session 中向前移动”。两者不得混用。

---

## 5. 播放完成与失败

- `get_offset_duration()` 是纯查询，不得触发切歌或创建任务。
- **队列下发**（ADR-0007 第 3 步，2026-11-23 起，支持 music API 的型号）：歌单曲目按窗口一次下发（默认 20 首），音箱自己切歌；StreamGateway 收到音箱拉取某一项时，服务端把当前曲目切到该项（`follow_track`）。窗口放完时音箱拉取静音哨兵，服务端以下一首为起点下发新窗口。此时 next timer 只做兜底，在预期结束后 8 s 才触发；单曲循环不设 timer。
- 逐首路径（不支持 music API 的型号、`queue_window_size=0`、没有歌单上下文的单独链接、网关不处理的地址）：自动完成判定只在 next timer 到期路径执行；到期后**直接**推进下一首，不再查询音箱状态（2026-11-22 起：音箱在循环单项队列时总报告 playing，查询只会推迟切歌）。
- 自动切歌不先停止（2026-11-23 起固定，`auto_next_preplay_stop` 已删除）；手动操作和新播放仍先停止。
- 顺序播放到歌单末尾时停止，不回到开头；单曲播放到期即停止。
- **开播确认**（ADR-0007 第 4 步）：经 StreamGateway 下发的曲目，以音箱拉取起始项作为开播确认，此后才进入 PLAYING，开播时间取拉流时间；`START_FETCH_TIMEOUT_SEC`（15 s）内未拉流按播放失败交给失败策略。网关不处理的地址（电台、站点中转）下发成功即视为开播。不再轮询音箱状态（`player_get_status`），也不再有 Jellyfin 直连失败改走代理的回退。
- 失败由 `completion_policy` 决策，retry/auto-next 经过 Device arbiter，并由 `PlaybackTaskRegistry` 持有。
- `_bump_play_session()` 不得取消当前正在执行导航的 task 自身。
- 所有延迟回调在执行前必须校验 session ID 与 LifecycleToken，旧 session 回调直接丢弃。

---

## 6. API 与状态确认

`POST /api/v1/play` 的 Coordinator 可返回 deferred accepted receipt：`accepted=true`、`started=null`。这表示命令已进入 Mina/Device arbiter 链路，不代表物理播放已开始。Stop 的 `accepted` 也只表示 stop 命令被接受；物理完成须观察 runtime phase=`STOPPED`。状态查询是纯读，不得创建 task、取消 task、调用 Mina 或隐式修复状态。

控制接口成功只表示动作已进入控制链，不表示音箱已完成切歌。

调用方必须通过以下任一状态通道确认结果：

- `GET /api/v1/player/state`
- `GET /api/v1/player/stream`

确认条件：

- `play_session_id` 已变化；
- `transport_state == "playing"`；
- `context.id` 仍是原歌单；
- `current_index` 沿当前队列快照移动；
- `track.entity_id` 与新索引对应。

远端小米音箱控制可能受认证刷新、云端限流和设备响应延迟影响。客户端不得因 HTTP 请求耗时较长而自行重发 `/play`，否则会替换当前 session。

---

## 7. 回归门禁

随机播放控制改动至少验证：

1. 固定快照 `[C, A, D, B]`，从 `C` 开始：next → `A`，next → `D`，previous → `A`。
2. 整段操作只建立一次外部播放 session，只洗牌一次。
3. next/previous 不调用 `PlaybackFacade.play()`、source resolve 或 playlist bootstrap。
4. `context.id` 与队列快照保持不变。
5. `device.play_type` 不因单次 `options.shuffle=true` 改变。
6. WebUI next/previous 只发送一个 control 请求。
7. WebUI 首页及其引用的实际 JS/CSS bundle 均返回 200。
8. 日志中不出现同曲破坏性重播、epoch 级假 offset 或明文凭据。

---

## 8. 外部设计参照

控制模型参考了公开播放器的通用原则：UI 只发送意图、队列由单一控制器持有、next 与播放结束汇入同一导航入口、随机模式维护稳定队列。外部项目仅用于验证控制原则，不复制其代码。

xiaomusic-core 与本地播放器的关键差异是：播放设备位于远端，状态存在延迟且可能被语音、蓝牙或其他客户端抢占。因此本项目保留本地 timer、session ID、有限状态宽限和投递确认机制。

---

## 9. DeviceCommandArbiter 与 preplay stop 现行规则

- `DeviceCommandArbiter` 的 per-device 单 worker 中，active intent 即事务 owner；物理 stop 不脱离该 executor。
- preplay group stop 不可取消，必须 await `group_force_stop_xiaoai()` 到 exact-terminal，并显式判定 `_group_stop_result_succeeded`；慢只影响后续命令延迟，不改变正确性。
- stop 返回失败或 unknown 时，当前 play/AUTO_NEXT 不得 dispatch；普通 `_playmusic` 会捕获 stop 异常并将 runtime 置为 `FAILED` 后返回 `False`，未吞异常的 executor 路径则由 arbiter 记录 executor failure；最新 pending/after-barrier intent 按现有 generation/token 规则继续处理。
- 若 stop RPC 永久挂死，active owner 保持 fail-closed，后续 intent 不越过它；这是已知运维风险，只能通过 transport/设备侧恢复处理，不以软超时放行。
- 外部 URL 与本地播放共用同一 exact stop 与 stale-token fence。facade probe 只观测；failure 仍是 unknown，不执行 physical-confirm、stop recovery 或放行逻辑，probe response 也不再提供 group-stop recovery extra 字段。
- preplay stop 失败时本曲不下发，交给统一失败处理（`_handle_play_failure`）按重试/下一首策略继续；此时不以“音箱仍在播放”为由跳过重试（那只是上一首或单项队列在循环）。
- 暂停/恢复不在产品范围内，将在 ADR-0007 第 6 步删除；在此之前不修、不扩展。

## 10. 相关文档

- [`../api/api_v1_spec.md`](../api/api_v1_spec.md)
- [`../adr/0004-state-authority.md`](../adr/0004-state-authority.md)
- [`state-authority.md`](state-authority.md)
- [`system_overview.md`](system_overview.md)
- [`../../tests/manual_smoke.md`](https://github.com/Akari787/xiaomusic-core/blob/main/tests/manual_smoke.md)
