# 自动切歌延迟修复执行清单

## 文档状态

**文档性质：** 本文是自动切歌延迟问题的专项调研/实验记录，涵盖 Round 1~4 的实验过程、根因定位与修复执行清单。

**本文不是全局 playback 权威规范。** 本文档不定义播放器状态投影模型、SSE 推送协议或播放控制接口的正式语义。这些内容分别以以下文档为准：
- `docs/spec/player_state_projection_spec.md`（权威播放状态快照）
- `docs/spec/player_stream_sse_spec.md`（SSE 推送协议）
- `docs/api/api_v1_spec.md`（v1 API 正式契约）
- `docs/spec/playback/playback_coordinator_interface.md`（PlaybackCoordinator 接口）

**当前是否可作为实现依据：** 只可作为该问题背景、根因分析和修复方向的参考依据。正式状态/SSE/接口仍以上述四份规范文档为准，不得将本文档作为接口契约或状态字段定义的使用来源。

**阅读顺序建议：** 建议先阅读 `docs/spec/playback/README.md` 了解 playback 模块整体结构，再读本文。

**当前可复用结论（5 条以内）：**
1. 自动切歌主链已优化，`_confirm_playback_started()` 改为后台任务，不再同步阻塞主链
2. `group_force_stop_xiaoai(fast=True)` 已启用，显著压低 stop 链耗时
3. 组内 stop 状态检查的 device_id 问题已修复
4. 自动切歌主链日志耗时已收敛至约 `1.7s ~ 1.9s`
5. 从状态观测看，真实设备进入 playing 的近似时间大致落在 `2.6s ~ 3.9s`，剩余瓶颈主要为 `play 链波动` 与设备尾段延迟

---

## 目标

将“自动切歌比预期晚 5-7 秒”的问题，收敛为一份可执行、可验证、可回退的修复清单。

目标结果：

- 去掉 `_confirm_playback_started()` 对自动切歌主链的阻塞
- 压缩 `group_force_stop_xiaoai()` 的 stop 链耗时
- 修正组内 stop 状态检查的设备 ID 问题
- 用埋点确认 `timer_fired -> 新歌实际开始播放` 明显缩短

---

## 当前状态总览

### 已完成修改

以下修改已完成并进入测试服务器验收阶段：

- [x] 自动切歌路径将 `_confirm_playback_started()` 改为后台确认
- [x] 自动切歌路径启用 `group_force_stop_xiaoai(fast=True)`
- [x] 修复组内 stop 状态检查错误使用 `self.device_id` 的问题
- [x] 补充本地回归测试并通过
- [x] 全量同步到测试服务器并重建容器

### 当前结论

- `delay_sec` 已在**代码层**和**测试服务器运行时**双重验证生效
- 自动切歌主链优化已在真实设备上生效
- 已完成 `OH2P` 的 Round 1 播放路径 A/B 实验支持与实机采样
- 已完成 Round 2 stop/play 衔接实验，并验证 `overlap + 200ms grace` 可显著压低主链耗时
- 已完成 Round 3 自动切歌轻量确认策略，实现更短的后台确认参数并在测试服务器生效
- 已完成 Round 4 状态驱动的“近似出声时间”测量，拿到更强的验收口径
- 自动切歌主链日志耗时已明显下降，并已首次进入约 `1.7s ~ 1.9s` 区间
- 从状态观测看，真实设备进入 playing 的近似时间大致落在 `2.6s ~ 3.9s`
- 当前剩余主要瓶颈已进一步收敛到 `play 链波动` 与 `设备真正进入 playing 的尾段延迟`

---

## 已确认事实

### 现象

- 自动切歌体感明显偏晚
- 将 `delay_sec` 设为负值后，延迟仍存在
- 说明问题不在定时器触发时刻，而在触发后的执行链

### 已定位的主要耗时

自动切歌完整路径：

```text
定时器 sleep 到期
  → _do_next()
    → _play_next()
      → _playmusic(name)
        → group_force_stop_xiaoai()
        → group_player_play(url, name)
        → _confirm_playback_started()
```

本地埋点结果：

| 步骤 | 耗时 | 结论 |
|---|---:|---|
| 定时器触发 → 进入 stop | 0.002s | 可忽略 |
| `group_force_stop_xiaoai()` | 1.92s | 次要瓶颈 |
| `group_player_play()` | 0.80s | 正常但不可忽略 |
| `_confirm_playback_started()` | 4.36s | 主瓶颈 |
| 总计 | ~7.07s | 定时器后串行链路过长 |

### 根因结论

### 第一阶段根因（优化前）

1. `_confirm_playback_started()` 在自动切歌主链里同步阻塞，天然引入 3-5 秒延迟
2. `group_force_stop_xiaoai()` 当前 stop 链存在额外 API 往返
3. `delay_sec` 只能调整定时器触发时刻，不能消除上述固有执行耗时

### 第二阶段根因（优化后）

真实设备验收后，根因已进一步收敛：

1. **自动切歌主链不再被 confirm 阻塞**，这一项已从主瓶颈中移除
2. **剩余主瓶颈是 `fast stop + group_player_play` 本身仍然偏慢**
3. `player_stop` 和 `play_by_music_url` 的 Mina API 往返耗时仍然较高
4. `delay_sec` 现在确认只影响“下一首定时器触发时刻”，不影响 stop/play 固有耗时

---

## 修复策略

采用“先降主链阻塞，再压 stop 链，再补正确性”的顺序。

优先级：

1. **P0：异步化播放确认**
2. **P1：自动切歌 fast stop**
3. **P1：修复组内 stop 状态检查 device_id 问题**
4. **P2：清理不可达/混乱的失败补救分支**

---

## 执行清单

## P0：把播放确认移出自动切歌主链

> 状态：**已完成并已通过测试服务器验收**

### 目标

让自动切歌主链从：

```text
timer -> stop -> play -> confirm started -> set timer
```

改成：

```text
timer -> stop -> play -> 立即记录播放开始/设置下一首定时器 -> 后台 confirm -> 失败再补救
```

### 需要修改的文件

- `xiaomusic/device_player.py`

### 具体动作

- [x] 梳理 `_playmusic()` 当前成功路径和失败路径
- [x] 将 `_confirm_playback_started()` 从自动切歌主链同步等待改为后台任务
- [x] 新增后台确认任务，至少包含：
  - [x] 带 `sid` 校验，避免旧任务误伤新会话
  - [x] 确认失败时取消错误定时器
  - [x] 调用现有失败补救逻辑或重试逻辑
- [x] 保证主链在 `group_player_play()` 返回后即可继续：
  - [x] 更新 `_start_time`
  - [x] 更新 `_paused_time`
  - [x] 写入 `_duration`
  - [x] 设置 `set_next_music_timeout()`
- [x] 确认手动播放/其他路径仍保留同步确认，避免一刀切

### 验收标准

- [x] 自动切歌时，`after_group_player_play` 后不再同步等待 `after_confirm_playback_started`
- [x] 新歌开始播放的体感延迟明显下降
- [x] 后台确认失败时，仍能进入补救路径，不会静默卡死

---

## P1：给自动切歌 stop 链增加 fast path

> 状态：**已完成并已通过测试服务器验收**

### 目标

减少自动切歌场景下 stop 链的 API 串行往返。

### 需要修改的文件

- `xiaomusic/device_player.py`

### 具体动作

- [x] 为 `group_force_stop_xiaoai()` 增加 fast 模式参数，例如 `fast=True/False`
- [x] 自动切歌路径调用 fast stop
- [x] fast stop 中优先尝试最短路径：
  - [x] 直接 `player_stop`
  - [x] 不走 `pause -> get_status -> stop` 的完整保守链
- [x] 保留非自动切歌路径的保守 stop 逻辑，降低回归风险
- [ ] 如果直停不稳定，准备 fallback 方案：
  - [ ] 先 direct stop
  - [ ] 失败时再走旧链路

### 验收标准

- [x] `group_force_stop_xiaoai()` 平均耗时下降
- [x] 没有观察到明显的尾音重叠、停止失败、随机不切歌等副作用

---

## P1：修复组内 stop 状态检查的 device_id 问题

> 状态：**已完成，本地测试已覆盖**

### 现状问题

`stop_if_xiaoai_is_playing(device_id)` 接收了 `device_id`，但内部调用 `get_if_xiaoai_is_playing()` 时实际仍使用 `self.device_id`。

这会导致组内多个设备 stop 时，状态检查对象不准确。

### 需要修改的文件

- `xiaomusic/device_player.py`

### 具体动作

- [x] 让 `get_if_xiaoai_is_playing()` 支持显式传入 `device_id`
- [ ] 或新增按设备 ID 查询播放状态的方法
- [x] `stop_if_xiaoai_is_playing(device_id)` 使用传入的目标设备进行状态判断
- [x] 检查同类调用点，避免只修一个入口

### 验收标准

- [x] 组内多个设备执行 stop 时，每台设备使用自己的 device_id 做状态判断
- [x] 相关日志能明确看出目标设备 ID

---

## P2：整理失败补救逻辑

> 状态：**已做第一轮整理，仍可继续收敛**

### 现状问题

`_playmusic()` 里存在 `started is False` 提前返回后，后续又判断 `all(ele is None for ele in results) and started is False` 的分支，逻辑顺序存在混乱，部分 fallback 分支可能不可达。

### 需要修改的文件

- `xiaomusic/device_player.py`

### 具体动作

- [x] 重新梳理 `_playmusic()` 中 play 失败、confirm 失败、proxy fallback 三类路径
- [x] 合并不可达或重复判断分支
- [x] 保证 fallback 的进入条件与返回顺序一致

### 验收标准

- [ ] 失败路径结构清晰
- [ ] 不存在明显不可达分支
- [ ] proxy fallback 逻辑可解释、可追踪

---

## 测试服务器验收结果

### 环境与方式

- 验收时间：2026-04-26 夜间
- 验收环境：`192.168.7.178` 测试服务器
- 部署方式：**全量同步**仓库后，重建 `xiaomusic-core` 容器
- 运行版本：日志显示 `1.1.1`
- 设备：`981257654` / `Xiaomi 智能音箱 Pro`

### 验收结果 A：`delay_sec` 运行时生效

通过真实服务接口直接修改 `delay_sec`，再触发实际播放并读取运行日志，确认定时器计算值随配置变化。

#### 对照 1：`delay_sec = 0`

日志摘录：

- 原始歌曲时长：`249.000`
- 获取音乐时长耗时：`2.273`
- 调整后定时器时长：`246.727`
- `timer_start(delay_sec=246.727)`

公式对上：

```text
249.000 - 2.273 + 0 = 246.727
```

#### 对照 2：`delay_sec = -5`

日志摘录：

- 原始歌曲时长：`249.000`
- 获取音乐时长耗时：`1.358`
- 调整后定时器时长：`242.642`
- `timer_start(delay_sec=242.642)`

公式对上：

```text
249.000 - 1.358 - 5 = 242.642
```

#### 结论

- [x] `delay_sec` 在测试服务器运行时确实参与定时器计算
- [x] 负值会让下一首定时器提前触发
- [x] 该配置项生效范围是“定时器触发时刻”，不是 stop/play 主链耗时

### 验收结果 B：自动切歌主链优化已生效

为了快速触发自动下一首，临时将 `delay_sec` 调到极小值，使当前歌曲几乎立即进入自动下一首路径，然后抓取真实设备日志。

#### 优化前基线（历史本地测量）

| 步骤 | 耗时 |
|---|---:|
| stop | ~1.9s |
| play | ~0.8s |
| confirm | ~4.4s |
| 总计 | ~7.1s |

关键问题：confirm 在主链里，用户需要等待它结束。

#### 优化后：自动下一首 session=6

日志关键点：

- `timer_fired`：`21:26:27`
- `group_force_stop_xiaoai fast:True`
- `after_group_force_stop_xiaoai dt=2.249`
- `after_group_player_play dt=1.654`
- `【色は匂へど散りぬるを-岚aya】已经开始播放了`
- `play_start_confirmation_result(... background=true)` 晚于主链很多出现

主链耗时约：

```text
2.249 + 1.654 = 3.903s
```

#### 优化后：自动下一首 session=7

日志关键点：

- `timer_fired`：`21:26:33`
- `group_force_stop_xiaoai fast:True`
- `after_group_force_stop_xiaoai dt=1.506`
- `after_group_player_play dt=2.889`
- `【初音ミクの激唱-Storyteller】已经开始播放了`
- `play_start_confirmation_result(... background=true)` 仍在后台完成

主链耗时约：

```text
1.506 + 2.889 = 4.395s
```

#### 验收结论

- [x] 自动切歌已经进入 `fast stop` 路径
- [x] `_confirm_playback_started()` 已移到后台，不再阻塞主链
- [x] 自动切歌主链耗时已从约 `7s+` 降到约 `3.9s ~ 4.4s`
- [x] 用户体感延迟明显下降
- [ ] 尚未达到 `1-2s` 目标

---

## 进一步深挖：剩余瓶颈

### 本轮选择的调查重点

本轮优先调查 **stop 链**，原因：

- 自动切歌主链里它是第一段真实设备 API 耗时
- 即使进入 `fast=True`，它仍占到约 `1.5s ~ 2.2s`
- 如果 stop 本身还有可压缩空间，收益会直接体现在每一次自动切歌上

同时顺手排查 play 链里是否还存在代码层面的隐藏额外开销。

### 已查明原因

真实设备日志表明，当前剩余延迟主要来自以下两段：

1. **`group_force_stop_xiaoai(fast=True)` 仍需 1.5s ~ 2.2s**
   - 虽然已去掉 `pause -> get_status -> stop` 的完整旧链
   - 但 `player_stop` 本身的设备响应时间仍不低
   - 某些轮次还会叠加状态读取/认证恢复抖动

2. **`group_player_play()` 仍需 1.6s ~ 2.9s**
   - `play_by_music_url` 的设备接收与启动也存在稳定耗时
   - 不同歌曲、不同轮次波动明显

3. **后台 confirm 已不再影响体感，但在极短下一首间隔测试中仍可能制造额外状态查询压力**
   - 这一段不再是主链阻塞问题
   - 但在“人为把下一首定时器压到 0.1 秒”的测试条件下，上一首的后台 confirm 可能与下一首 stop/play 共享同一设备 API 通道
   - 这更像极端测试条件下的竞争放大，不足以解释正常歌曲间隔下的主要剩余延迟

### 连续自动切歌采样结果（5 轮有效样本）

为了把剩余瓶颈从偶发日志提升为稳定结论，本轮追加做了 **5 轮连续自动下一首采样**。

方法：

- 临时将 `delay_sec` 调成极小值，让每首歌的下一首定时器都压到 `0.1s`
- 真实设备连续跑 5 轮自动切歌
- 逐轮记录 `stop_dt`、`play_dt`、`main_chain_dt`

采样结果：

| session | stop_dt | play_dt | main_chain_dt |
|---|---:|---:|---:|
| 16 | 1.475s | 1.081s | 2.556s |
| 17 | 1.641s | 4.788s | 6.429s |
| 18 | 1.671s | 2.445s | 4.116s |
| 19 | 2.046s | 1.752s | 3.798s |
| 20 | 3.373s | 1.605s | 4.978s |

统计值：

| 指标 | min | P50 | avg | max |
|---|---:|---:|---:|---:|
| stop_dt | 1.475s | 1.671s | 2.041s | 3.373s |
| play_dt | 1.081s | 1.752s | 2.334s | 4.788s |
| main_chain_dt | 2.556s | 4.116s | 4.375s | 6.429s |

### 已确认的 stop 链结论

#### 1. `fast stop` 已经有效，但 stop 仍然构成稳定底噪

对比日志：

- **旧链 / 非 fast**：
  - `pause -> get_status -> stop`
  - 实测曾出现 `3.699s`、`5.422s`
- **新链 / fast**：
  - 直接 `player_stop`
  - 采样结果主要落在 `1.475s ~ 2.046s`
  - 极端样本到 `3.373s`

结论：

- `fast stop` 已经实际节省了约 `2s+`
- 剩余 stop 耗时的大头不是 Python 逻辑，而是 `player_stop` API 往返和设备确认
- stop 链现在更像是**稳定底噪成本**：通常至少要付出约 `1.5s ~ 1.7s`

#### 2. stop 链没有再藏着额外的同步确认逻辑

代码路径已经收敛为：

```text
_play_next()
  → _play(..., fast_stop=True)
    → _playmusic(..., fast_stop=True)
      → group_force_stop_xiaoai(fast=True)
        → force_stop_xiaoai(device_id, fast=True)
          → mina_call("player_stop", ...)
```

也就是说，在自动切歌路径下，stop 侧已经没有额外的 `pause`、`get_status`、`confirm started` 同步等待。

### 顺手排除的 play 链隐藏开销

#### 1. 当前设备被硬件分支强制走 `play_by_music_url`

当前测试设备为 `OH2P`，而 `OH2P` 被列在 `NEED_USE_PLAY_MUSIC_API` 中。

因此当前代码路径固定为：

```python
play_by_music_url(device_id, url, audio_id=...)
```

即使：

- `use_music_api = False`
- `continue_play = False`

也不会走 `play_by_url`。

#### 2. 当前 play 链没有额外的在线 `audio_id` 搜索请求

代码检查显示：

- `_get_audio_id(name)` 只有在 `use_music_api=True` 或 `continue_play=True` 时，才会额外调用 `mina_request('/music/search', ...)`
- 当前测试路径不满足该条件，因此直接返回默认 `audio_id`
- 这意味着当前 `group_player_play()` 的慢，不是被额外的搜索 RPC 拖慢

#### 3. play 链是当前**最主要的尾部风险来源**

采样结果显示：

- stop 的中位数：`1.671s`
- play 的中位数：`1.752s`
- stop 的最大值：`3.373s`
- play 的最大值：`4.788s`

结论：

- play 链剩余耗时主要就是 `play_by_music_url` 这次 Mina 调用本身，以及设备接收 URL 后的启动反应时间
- 如果看“稳定底噪”，stop 和 play 都不便宜
- 如果看“最坏情况拖尾”，当前**play 链比 stop 链更危险**
- 因此更彻底的结论是：
  - **stop 决定了主链的基础下限**
  - **play 决定了主链的最坏情况上限**

### 对后台 confirm 干扰的判断

在强行把下一首定时器压到 `0.1s` 的连续切歌实验中，日志出现了这种现象：

- 上一首 session 的 `play_start_confirmation_result(... background=true)` 尚未结束
- 下一首已经进入 `fast stop` 或 `group_player_play()`
- 两者之间会夹杂 `player_get_status` 日志

这说明：

- **后台 confirm 确实可能与后续 API 调用并发出现**
- 但这是在极端缩短歌曲剩余时间后的放大观测
- 对于正常 200 秒级歌曲，不会在这么短时间内连续触发下一首，因此它更像**次要干扰项**，不是当前 4 秒级主链延迟的主因

### 当前链路结构（优化后）

```text
timer_fired
  → fast stop
  → player_play
  → 立即标记开始播放 / 设置下一首定时器
  → 后台 confirm started
```

### 现在不是瓶颈的项

以下项已经确认不再是主链首要问题：

- `delay_sec`
- Python 层 `_do_next()` 调度本身
- `_confirm_playback_started()` 对自动切歌主链的同步阻塞

---

## 下一步方案（只记录，不立即改）

以下方案按“收益优先 + 风险可控”排序，目标不是一次性拍脑袋换实现，而是把后续修改压缩成可验证的几轮实验。

### Phase 1：优先处理 play 链长尾

### 方案 A：`OH2P` 播放路径 A/B 实验

> 状态：**已完成第一轮实现与测试服务器实机验证**

目标：确认 `OH2P` 上 `play_by_music_url` 是否就是当前 `4s+` 长尾的直接来源。

#### 要验证的问题

- `OH2P` 是否真的必须走 `play_by_music_url`
- 如果切到 `play_by_url`，是否能明显缩短 `group_player_play()`
- 变更播放接口后，是否会引入起播失败、偶发无声、兼容性问题

#### 实验设计

A 组（现状基线）：

- 保持当前逻辑
- `OH2P -> play_by_music_url`
- 连续自动切歌多轮采样

B 组（候选实验）：

- 在受控分支上临时放开 `OH2P`
- 强制改走 `play_by_url`
- 用同一设备、同一歌单、同样的极短 `delay_sec` 做多轮采样

#### 需要记录的指标

- `play_dt` 的 min / P50 / avg / max
- `main_chain_dt` 的 min / P50 / avg / max
- 起播成功率
- 是否出现：
  - 无声
  - 重放
  - 立即停止
  - 设备拒绝播放

#### 决策标准

只有同时满足以下条件，才认为值得切换：

- `play_dt` 的 P50 明显下降
- `play_dt` 的 max 明显下降
- 起播成功率不低于现状
- 没有新增明显兼容性异常

#### 实现内容

已增加实验开关：

- `play_url_mode=auto`
- `play_url_mode=play_by_music_url`
- `play_url_mode=play_by_url`

并在日志中输出：

```text
play_one_url dispatch_mode=...
```

用于确认每次真实播放走的接口路径。

#### 第一轮 A/B 实机结果

测试环境：

- 测试服务器：`192.168.7.178`
- 设备：`OH2P / Xiaomi 智能音箱 Pro`
- 方法：极短 `delay_sec` 连续自动切歌 4 轮采样

A 组：`play_url_mode=auto`（实际走 `play_by_music_url`）

| session | stop_dt | play_dt | main_dt |
|---|---:|---:|---:|
| 8 | 1.176s | 1.372s | 2.548s |
| 9 | 1.541s | 1.770s | 3.311s |
| 10 | 2.107s | 1.625s | 3.732s |
| 11 | 4.240s | 2.408s | 6.648s |

汇总：

- `play_dt`：min `1.372s` / P50 `1.770s` / avg `1.794s` / max `2.408s`
- `main_dt`：min `2.548s` / P50 `3.732s` / avg `4.060s` / max `6.648s`

B 组：`play_url_mode=play_by_url`

| session | stop_dt | play_dt | main_dt |
|---|---:|---:|---:|
| 21 | 1.473s | 1.643s | 3.116s |
| 22 | 1.289s | 2.975s | 4.264s |
| 23 | 1.490s | 1.495s | 2.985s |
| 24 | 1.664s | 22.703s | 24.367s |

汇总：

- `play_dt`：min `1.495s` / P50 `2.975s` / avg `7.204s` / max `22.703s`
- `main_dt`：min `2.985s` / P50 `4.264s` / avg `8.683s` / max `24.367s`

额外现象：

- `play_by_url` 组出现过 `play_start_confirmation_result(... started=false)`
- `play_by_url` 组出现过单次 `play_dt` 拉长到 `22.703s` 的严重长尾
- `play_by_music_url` 组虽然仍有 stop 拖尾，但 play 链整体明显更稳

#### 第一轮结论

- [x] `OH2P` 上可以强制走 `play_by_url`
- [x] 但 `play_by_url` 在真实设备上**比 `play_by_music_url` 更差且更不稳定**
- [x] `OH2P` 当前不应切换到 `play_by_url` 作为默认实现
- [x] Round 1 的实验结论是：
  - **保留 `play_by_music_url` 作为 `OH2P` 默认路径**
  - 问题并没有被播放接口切换直接解决

#### 风险

- `OH2P` 可能确实依赖 `play_by_music_url`
- 强切 `play_by_url` 会显著增加长尾和异常概率

---

### Phase 2：继续压 stop 链下限

### 方案 B：stop / play 更激进衔接实验

> 状态：**已完成第一轮实现与测试服务器实机验证**

目标：判断 `player_stop` 返回是否必须作为 `player_play` 的硬前置条件。

#### 要验证的问题

- 当前 stop 是否存在“等设备完全确认”导致的硬等待
- 是否可以接受“stop 发出后立即继续 play”
- 提前衔接能否换来可观收益，而不明显损害稳定性

#### 候选方向

1. **弱化 stop 完成等待**
   - stop 请求发出成功后即进入 play
   - 不再把 stop 的返回完成当成绝对屏障

2. **更早发起 play**
   - 允许 stop/play 部分时间重叠
   - 以设备是否真的出现重叠播放为验收标准

3. **保留 fast stop，但缩短保护窗口**
   - 不是完全并发
   - 而是在 stop 返回前后插入更小的等待窗口

#### 需要重点观察的副作用

- 尾音未停就切入下一首
- 两首歌短暂重叠
- stop 失效导致设备拒绝下一次 play
- 某些轮次变快，但失败率显著上升

#### 决策标准

只有在以下条件同时成立时，才考虑保留：

- `stop_dt` 或 `main_chain_dt` 有实质下降
- 没有稳定复现的重叠播放
- 起播成功率不显著下降

#### 实现内容

已增加实验开关：

- `auto_next_stop_wait_mode=sync`
- `auto_next_stop_wait_mode=overlap`
- `auto_next_stop_grace_ms=<N>`

并在日志中输出：

```text
group_stop_dispatch session_id=... fast_stop=True wait_mode=... grace_ms=...
group_stop_overlap_state session_id=... stop_done=true|false
```

用于确认：

- 当前自动切歌实际使用的 stop 等待策略
- 进入 play 时 stop 是否已经完成

#### 第一轮实机结果

测试环境：

- 测试服务器：`192.168.7.178`
- 设备：`OH2P / Xiaomi 智能音箱 Pro`
- 播放路径：保持 `play_by_music_url`
- 方法：极短 `delay_sec` 连续自动切歌采样

##### A 组：`sync`

| session | stop_dt | play_dt | main_dt |
|---|---:|---:|---:|
| 8 | 1.698s | 3.346s | 5.044s |
| 9 | 2.065s | 1.667s | 3.732s |
| 10 | 1.855s | 1.934s | 3.789s |
| 11 | 2.764s | 7.328s | 10.092s |

汇总：

- `main_dt`：min `3.732s` / P50 `5.044s` / avg `5.664s` / max `10.092s`

##### B 组：`overlap + 0ms`

| session | stop_dt | play_dt | main_dt | stop_done_after_play |
|---|---:|---:|---:|---|
| 20 | 0.000s | 2.668s | 2.668s | true |
| 21 | 0.000s | 1.798s | 1.798s | false |
| 22 | 0.000s | 1.950s | 1.950s | false |
| 23 | 0.000s | 1.447s | 1.447s | true |

汇总：

- `main_dt`：min `1.447s` / P50 `1.950s` / avg `1.966s` / max `2.668s`

##### C 组：`overlap + 200ms`

| session | stop_dt | play_dt | main_dt | stop_done_after_play |
|---|---:|---:|---:|---|
| 33 | 0.201s | 1.589s | 1.790s | true |
| 34 | 0.201s | 1.521s | 1.722s | true |
| 35 | 0.201s | 1.677s | 1.878s | false |
| 36 | 0.201s | 1.583s | 1.784s | false |

汇总：

- `main_dt`：min `1.722s` / P50 `1.790s` / avg `1.793s` / max `1.878s`

#### 第一轮结论

- [x] stop/play 不必强依赖“等 `player_stop` 完整返回”后再发 play
- [x] `overlap` 模式能显著压低主链耗时
- [x] `overlap + 200ms grace` 比 `sync` 稳定得多，也比 `overlap + 0ms` 更均匀
- [x] 在当前样本中，未观察到稳定复现的重叠播放或明显起播失败
- [x] 因此本轮决定：
  - **将自动切歌默认 stop 等待策略切到 `overlap`**
  - **默认 grace 设为 `200ms`**

#### 当前判断

- 这是目前为止收益最大的真实优化项
- 它已经把自动切歌主链从 `4s+ ~ 10s` 区间压到了约 `1.7s ~ 1.9s` 区间
- 后续仍需继续观察长时间运行下是否出现偶发重叠、异常停播或硬件兼容问题

---

### Phase 3：减少后台 confirm 的竞争干扰

### 方案 C：自动切歌场景下的轻量确认策略

> 状态：**已完成第一轮实现与测试服务器验证**

目标：保留失败补救能力，同时减少后台状态查询对后续轮次的 API 竞争。

#### 要验证的问题

- 当前后台 confirm 的 probe 次数是否偏多
- 它对正常歌曲间隔的影响有多大
- 是否可以只在高风险场景保留完整确认

#### 候选方向

- 降低后台 confirm 的 probe 次数
- 拉大 probe 间隔，减少与 stop/play 紧邻竞争
- 只在以下场景保留完整 confirm：
  - 代理回退后
  - 历史上起播失败率高的路径
  - 某些特定硬件
- 对自动切歌使用更轻量的 started 检测

#### 实现内容

已增加自动切歌专用后台确认参数：

- `auto_next_confirm_delay_ms=800`
- `auto_next_confirm_retries=0`
- `auto_next_confirm_interval_ms=300`

并保留手动播放 / 同步确认路径的原有参数：

- `delay_ms=1200`
- `retries=2`
- `interval_ms=600`

实现原则：

- **自动切歌主链**：继续保留后台确认，但只做更轻量的检测
- **手动播放 / 同步确认**：保持原来的稳妥参数，不因自动切歌优化而降低可靠性

#### 第一轮验证结果

测试环境：

- 测试服务器：`192.168.7.178`
- 设备：`OH2P / Xiaomi 智能音箱 Pro`
- stop 等待策略：`overlap + 200ms grace`

日志已确认：

```text
play_start_confirmation_attempted(... retries=0, delay_ms=800, interval_ms=300)
```

也就是说，自动切歌后台确认已经切换到新的轻量配置。

#### 第一轮结论

- [x] 自动切歌后台确认参数已成功下调
- [x] 手动播放路径仍保留原有稳妥确认参数
- [x] 轻量确认已经在测试服务器运行时生效
- [x] 当前没有观察到它破坏自动切歌主链成功率的证据

#### 当前判断

- 这一轮不会像 Round 2 那样直接再砍掉数秒主链耗时
- 但它减少了后台确认的状态查询强度，为后续降低波动和减少 API 竞争打基础
- 这一步更像“把主链优化成果稳定住”，而不是继续压低主链下限

#### 适用前提

这个方向应在 stop/play 主链优化之后再做。
原因：

- 它更像“降波动”和“减干扰”
- 不是当前 4 秒级主链延迟的首要决定项

---

### Phase 4：把验收指标升级成更强证据

### 方案 D：建立更精确的“出声时间”指标

目标：把“体感延迟”从日志代理进一步收敛为更强的验收标准。

#### 目前问题

现在主要依赖：

- `timer_fired`
- `after_group_force_stop_xiaoai`
- `after_group_player_play`
- `【xxx】已经开始播放了`

这足以判断主链耗时，但仍然不是“用户真正听到声音”的严格定义。

#### 实现内容

已增加新的测量日志：

```text
[measure] status_playing_observed t=... dt_from_timer=... session_id=... name=...
```

含义：

- 在自动切歌路径下，歌曲进入播放后启动一个轻量状态探测任务
- 当 `player_get_status` 首次观察到 `status == 1` 时，记录：
  - 当前时间戳
  - 距 `timer_fired` 的增量 `dt_from_timer`

这不是严格的物理“出声时间”，但比单纯看 API 返回更接近真实听感。

#### 第一轮实机结果

测试环境：

- 测试服务器：`192.168.7.178`
- 设备：`OH2P / Xiaomi 智能音箱 Pro`
- stop 等待策略：`overlap + 200ms grace`
- confirm 策略：`800ms / 0 retries / 300ms`

采样结果（有效样本 3 轮）：

| session | after_group_player_play | status_playing_observed |
|---|---:|---:|
| 8 | 1.633s | 3.228s |
| 9 | 1.105s | 2.592s |
| 10 | 1.248s | 3.855s |

汇总：

- `status_playing_observed dt_from_timer`
  - min `2.592s`
  - P50 `3.228s`
  - avg `3.225s`
  - max `3.855s`

#### 第一轮结论

- [x] 新验收口径已成功落地
- [x] 它证明“主链 API 完成”与“设备真正进入 playing”之间仍有尾段差距
- [x] 当前自动切歌虽然主链已压到 `1.7s ~ 1.9s`，但真实设备进入 playing 仍大致需要 `2.6s ~ 3.9s`

#### 作用

- 让后续方案比较更可靠
- 避免只优化日志时间、却没有真实体感收益
- 为后续长期采样建立可复用基线

---

## 推荐实施顺序（细化版）

### Round 1：播放路径 A/B

状态：**已完成**

结论：

- `OH2P` 已完成 `play_by_music_url` vs `play_by_url` 实机对照
- `play_by_url` 不仅没有更快，反而带来了更大的长尾和更差的稳定性
- 这一轮已经明确排除“直接改默认播放接口即可解决问题”这条路线

输出物：

- `play_by_music_url` vs `play_by_url` 的对照采样表
- 成功率 / 长尾 / 风险对比
- 不切换默认播放路径的明确结论

### Round 2：stop/play 衔接实验

状态：**已完成第一轮实现、测试与实机验收**

结论：

- `sync` 不是最优策略
- `overlap + 200ms grace` 在当前设备上取得了最稳的主链结果
- 这一轮已经拿到足够证据支撑默认策略切换

输出物：

- stop 强依赖等待与弱依赖等待的对照结果
- 是否出现重叠播放、副作用、失败率变化
- 是否能实质压低主链下限

### Round 3：后台 confirm 轻量化

状态：**已完成第一轮实现与运行时验证**

结论：

- 自动切歌后台确认已切换到轻量参数
- 手动播放路径未被牵连
- 这一步主要用于减少竞争与稳定波动，而不是再大幅压缩主链秒数

输出物：

- confirm 参数/策略变化前后的竞争影响
- 是否能继续缩短 P95

### Round 4：更精确验收标准

状态：**已完成第一轮实现与测试服务器验收**

结论：

- 已新增状态驱动的近似出声指标：`status_playing_observed`
- 相比只看 `after_group_player_play`，它更接近“用户真正开始听到声音”的时刻
- 当前真实设备进入 playing 的近似时间仍明显晚于主链 API 完成时间

输出物：

- 更强的“出声时间”验收口径
- 可以长期复用的延迟回归基线

---

## 本轮文档结论

截至当前：

1. `delay_sec` 已确认生效，但它解决的是“何时触发”，不是“触发后多久切过去”
2. 自动切歌主链优化已经成功去掉 confirm 阻塞，效果已在测试服务器真实设备上验证
3. `OH2P` 的播放路径 A/B 已完成第一轮实机验证，结果显示：
   - `play_by_url` 不是更优解
   - 它比 `play_by_music_url` 更慢、更不稳定，并出现严重长尾
4. Round 2 stop/play 衔接实验已完成，结果显示：
   - `sync` 不是当前最优 stop 等待策略
   - `overlap + 200ms grace` 可以把主链稳定压到约 `1.7s ~ 1.9s`
5. Round 3 轻量后台确认已完成，结果显示：
   - 自动切歌后台确认参数可以显著收缩
   - 手动播放路径仍能保留原有稳妥确认策略
   - 该策略已在测试服务器运行时生效
6. Round 4 状态驱动验收已完成，结果显示：
   - 主链 API 完成时间不等于设备真正进入 playing 时间
   - 当前真实设备进入 playing 的近似时间大致落在 `2.6s ~ 3.9s`
7. 因此当前最可靠的组合结论是：
   - **对 `OH2P`，默认播放路径仍保留 `play_by_music_url`**
   - **自动切歌默认 stop 等待策略切到 `overlap + 200ms grace`**
   - **自动切歌后台确认切到轻量参数：`800ms / 0 retries / 300ms`**
   - **自动切歌验收同时看两条指标：主链完成时间 + `status_playing_observed`**
8. 当前剩余问题不再是“自动切歌为什么总是 5~7 秒”，而是：
   - 如何继续降低 `play_dt` 波动
   - 如何缩短设备从 API 完成到进入 playing 的尾段延迟
   - 如何继续长期观察 overlap 策略下是否存在偶发副作用
9. 这轮主修复已经闭环，后续工作重点转入：
   - 长时间样本下的波动/副作用观察
   - 针对尾段播放状态滞后的进一步设备级优化

---

## 验证清单

## 1. 埋点验证

继续使用现有 `[measure]` 日志，重点核对：

- [x] `timer_fired`
- [x] `before_group_force_stop_xiaoai`
- [x] `after_group_force_stop_xiaoai`
- [x] `before_group_player_play`
- [x] `after_group_player_play`
- [x] `after_confirm_playback_started`

预期变化：

- [x] `timer_fired -> after_group_player_play` 成为主要主链耗时
- [x] `after_group_player_play -> after_confirm_playback_started` 不再阻塞自动切歌体感路径
- [x] `group_force_stop_xiaoai()` 耗时下降或至少结构更可控

## 2. 行为验证

至少验证以下场景：

- [x] 单设备自动切歌
- [ ] 组播设备自动切歌
- [ ] 手动下一首
- [x] 播放失败后的自动补救
- [x] Jellyfin / proxy fallback 相关路径未被破坏

## 3. 最终指标

核心指标只有一个：

- [ ] `timer_fired -> 新歌实际开始出声`

目标区间：

- **稳定目标：1-2 秒**
- **激进优化目标：0.5-1.5 秒**
- **接近 0 秒不作为本轮目标**

---

## 回归测试清单

至少补以下测试：

- [x] 自动切歌路径下，播放确认改为后台任务后，不阻塞主链
- [x] 后台确认失败时，能进入失败补救逻辑
- [x] fast stop 路径下不会误取消新会话
- [x] 组内 stop 状态检查使用正确的 `device_id`
- [x] 旧 session 的确认/重试任务不会误伤新 session

如需新增测试文件，优先考虑：

- `tests/test_play_session_timer.py`
- 与 `device_player.py` 对应的单元测试文件

---

## 不做事项

本轮不把以下动作当作主修复：

- [ ] 单独调整 `delay_sec`
- [ ] 只缩短 `_confirm_playback_started()` 的等待参数而不改主链结构
- [ ] 在没有补救机制前，粗暴完全移除播放确认

---

## 实施顺序建议

1. [x] 先改 P0：异步化播放确认
2. [x] 再改 P1：自动切歌 fast stop
3. [x] 同步修 P1：device_id 正确性问题
4. [x] 做第一轮 P2：失败补救分支整理
5. [x] 跑埋点验证与回归测试
6. [x] 根据真实设备日志决定继续深挖 stop/play 延迟

---

## 关键文件

- `xiaomusic/device_player.py` — 切歌执行链路与 stop/play/confirm 主体
- `xiaomusic/config.py` — `delay_sec`、`enable_force_stop` 等配置
- `xiaomusic/relay/runtime.py` — `mina_call` 底层调用链
- `tests/test_play_session_timer.py` — 现有会话/定时器测试基础

---

## 相关 Issue

- #435（追踪切歌延迟讨论）
