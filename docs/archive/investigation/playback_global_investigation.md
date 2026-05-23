# xiaomusic 播放问题全局调查报告

**调查时间**：2026-05-12  
**项目路径**：D:/AI/xiaomusic-core  
**测试服务器**：192.168.7.178（SSH 无法访问，无实时日志）

---

## 执行摘要

本次调查验证了前两轮修复（`no_retry` + `is_playing` 重置）的代码实现，并分析了剩余的潜在问题点。

**结论**：
- ✅ `no_retry` 修复已正确实施
- ✅ `is_playing=False` + `_start_time=0` 重置已正确实施
- ⚠️ timer 未取消是设计选择，但依赖 timer 自然触发
- ⚠️ autonext_guard 依赖 `get_if_xiaoai_is_playing()` 状态检查，存在可靠性风险
- ❓ SSH 无法访问，无法获取实时日志验证实际行为

---

## 节点1: timer 基准偏移分析

### 代码分析

**位置**：`device_player.py` L1052-1110（`_mark_play_started` 函数）

```python
async def _mark_play_started(self, *, name: str, sid: int, cur_playlist: str, ...):
    # 重置播放失败次数
    self._play_failed_cnt = 0
    self._play_fail_first_ts = 0.0
    self._play_fail_last_reason = ""

    self.log.info(f"【{name}】已经开始播放了")
    ...
    # L1068: 记录歌曲开始播放的时间
    self._start_time = time.time()
    self._paused_time = 0
    ...
    
    # L1079: 获取音乐时长
    sec = await self.xiaomusic.music_library.get_music_duration(name)
    self._duration = sec
    
    # L1090: 计算获取时长的执行耗时
    duration_execution_time = time.time() - self._start_time
    self.log.info(f"获取音乐时长耗时: {duration_execution_time:.3f} 秒")
    
    # L1094: 调整定时器时长，减去获取音乐时长的执行时间
    adjusted_sec = sec + self.config.delay_sec - duration_execution_time
    adjusted_sec = max(adjusted_sec, 0.1)
    
    await self.set_next_music_timeout(adjusted_sec)
```

### 分析结论

| 问题 | 答案 |
|------|------|
| `_start_time` 在 `get_music_duration()` 之前设置？ | **是**（L1068 vs L1079） |
| `duration_execution_time` 的计算逻辑 | `time.time() - self._start_time`（获取 duration 的实际耗时） |
| 是否存在"duration 获取完成但播放命令还未发出"的延迟？ | **存在**，但已被 `duration_execution_time` 补偿 |

**Timer 基准偏移分析**：

```
时间线：
T+0.0s   _start_time = time.time()  ← timer 基准点（假设）
T+0.0s   发送播放命令到设备
T+0.5s   设备开始播放
T+1.0s   get_music_duration() 返回 (duration=180s)
T+1.0s   duration_execution_time = 1.0s
T+1.0s   adjusted_sec = 180 + delay_sec - 1.0 ≈ 179s

理想 timer 触发时间：T+0.5s + 180s = T+180.5s
实际 timer 触发时间：T+1.0s + 179s = T+180.0s

误差：约 0.5s（设备实际开始播放的时间）
```

**结论**：timer 基准偏移约 0.5 秒，是可接受的范围。`duration_execution_time` 补偿了获取时长的时间，但无法补偿设备实际开始播放的时间。

---

## 节点2: autonext_guard offset 计算分析

### 代码分析

**位置**：`device_player.py` L255-310（`get_offset_duration` 函数）

```python
def get_offset_duration(self):
    duration = self._duration
    if not self.is_playing:
        return 0, duration  # ← is_playing=False 时直接返回 (0, duration)
    
    offset = time.time() - self._start_time - self._paused_time
    ...
```

### 确认失败时的状态重置

**位置**：`device_player.py` L1217-1231（`_background_confirm_playback_started` 函数）

```python
# 自动切歌确认失败时，不触发 retry，让歌曲继续播放
# 不取消 timer，让歌曲自然播放或被 autonext_guard 接管
self._play_failed_cnt += 1
self._play_fail_last_reason = "play_start_not_confirmed"
if self._play_fail_first_ts <= 0:
    self._play_fail_first_ts = time.time()
self.log.info(
    "play_start_not_confirmed (auto_next) no_retry cnt=%d name=%s",
    self._play_failed_cnt,
    name,
)
# 重置播放状态，避免设备停止时 is_playing=True 导致下一次 timer 触发重播
self.is_playing = False  # ← 关键修复
self._start_time = 0      # ← 关键修复
return
```

### 分析结论

**关键问题**：确认失败后 `is_playing=False` + `_start_time=0`，但 timer 没取消。此时 `get_offset_duration()` 被调用会怎样？

**答案**：由于 `is_playing=False`，在 L257 直接返回 `(0, duration)`，不会进入后续的 autonext_guard 检查逻辑。

```
is_playing=False 时的执行路径：
L256: if not self.is_playing: return 0, duration
     → 直接返回，不计算 offset
     → 不触发 overdue_without_timer（因为没有计算 offset）
     → 不触发 near_end_with_timer（因为没有计算 offset）
```

**结论**：✅ 修复已正确实施，不会因为 `_start_time=0` 导致 offset 计算异常。

---

## 节点3: near_end_with_timer 误触发分析

### 代码分析

**位置**：`device_player.py` L270-276

```python
overdue_without_timer = (
    self._next_timer is None and offset >= duration + 15.0
)
near_end_with_timer = self._next_timer is not None and offset >= max(
    duration - 1.0, duration * 0.9
)
should_check_autonext = overdue_without_timer or near_end_with_timer
```

### 分析结论

| 条件 | 触发条件 | 当前状态 | 是否触发 |
|------|----------|----------|----------|
| `overdue_without_timer` | `_next_timer is None` **且** `offset >= duration + 15.0` | timer 存在（`_next_timer is not None`） | ❌ 不触发 |
| `near_end_with_timer` | `_next_timer is not None` **且** `offset >= max(duration-1, duration*0.9)` | `is_playing=False` → offset=0 | ❌ 不触发 |

**结论**：✅ 修复后，`is_playing=False` → `get_offset_duration()` 返回 `(0, duration)` → `near_end_with_timer` 不触发。

---

## 节点4: _play_list 同步问题分析

### 代码分析

**位置**：`device_player.py` L629-661

```python
def _stage_playlist_navigation_transition(self, name: str, *, reason: str) -> None:
    """在切歌前重置播放状态"""
    self.is_playing = False
    self._start_time = 0
    self._paused_time = 0
    self._duration = 0
    self._last_cmd = reason
    self._set_runtime_track_reference(...)

async def _play_next(self, manual: bool = False):
    """播放下一首（内部实现）"""
    self.log.info("开始播放下一首")
    name = self.get_cur_music()
    if (
        manual
        or self.device.play_type == PLAY_TYPE_ONE
        ...
    ):
        name = self.get_next_music()  # ← 从播放列表获取下一首
        self.log.info(f"get_next_music {name}")
```

### 分析结论

| 场景 | 行为 |
|------|------|
| `manual=True` | 始终调用 `get_next_music()` |
| `play_type == ONE/SIN/ALL/RND/SEQ` | 始终调用 `get_next_music()` |
| `name == ""` | 调用 `get_next_music()` |
| `name not in _play_list` | 调用 `get_next_music()` |
| **默认自动切歌** | **使用 `get_cur_music()`（当前歌曲）** |

**关键发现**：在自动切歌场景（timer 触发），`_play_next()` 使用的是 `get_cur_music()`，而不是 `get_next_music()`。

**问题分析**：如果 `_current_index` 和 `_play_list` 不同步，可能导致切到错误的歌曲。

**结论**：需要通过日志验证 `get_next_music` 返回的歌曲名称和实际播放的歌曲名称是否一致。

---

## 节点5: 完整触发时序分析（基于代码分析）

### 正常自动切歌时序

```
T+0.0s   timer_fired → _play_next() [sid=X]
           ↓
T+0.0s   _stage_playlist_navigation_transition() 重置状态
           ↓
T+0.0s   name = get_cur_music() → 获取当前歌曲
           ↓
T+0.0s   _bump_play_session(reason="start_new_play") → sid=X+1
           ↓
T+0.0s   cancel_group_next_timer() → 取消旧 timer
           ↓
T+0.0s   is_playing = True
           ↓
T+0.0s   group_player_play(url, name) → 发送播放命令
           ↓
T+0.0s   _mark_play_started() → 设置 _start_time, timer
           ↓
T+0.0s   _schedule_playback_confirmation() → 调度后台确认
           ↓
T+0.8s   _background_confirm_playback_started() 开始检查
           ↓
T+1.2s   get_if_xiaoai_is_playing() → 检查播放状态
```

### 确认失败时的时序（当前修复）

```
T+0.0s   timer_fired → _play_next() [sid=X]
           ↓
T+0.0s   ...（播放命令发送）
           ↓
T+0.0s   _mark_play_started() → _start_time=now, timer 设置
           ↓
T+0.0s   _schedule_playback_confirmation() → 调度后台确认
           ↓
T+1.2s   _background_confirm_playback_started() 发现 started=False
           ↓
T+1.2s   Jellyfin fallback 尝试
           ↓
T+1.2s   fallback 失败
           ↓
T+1.2s   is_playing = False  ← 关键修复
           ↓
T+1.2s   _start_time = 0     ← 关键修复
           ↓
T+1.2s   return（不取消 timer）
           ↓
T+180s   timer 触发 → _play_next() → 切到下一首
```

### 潜在问题：autonext_guard 在 timer 未取消时的行为

```
确认失败后（is_playing=False, _start_time=0, timer 存在）：

任何代码调用 get_offset_duration() 时：
  → is_playing=False → 返回 (0, duration)
  → 不触发 autonext_guard

但如果：
  → 外部代码（如定时任务）调用 get_offset_duration()
  → 并触发 should_check_autonext
  → 调度 _guard_autonext()

则 _guard_autonext():
  → still_playing = get_if_xiaoai_is_playing()
  → 如果返回 False → return（不切歌）
  → 如果返回 True → 切歌到下一首

问题：如果 still_playing 误判为 True，可能导致重播
```

---

## 根因结论

### 已确认的问题点

| # | 问题点 | 严重程度 | 状态 |
|---|--------|----------|------|
| 1 | `_start_time=0` 导致 offset 计算异常 | 高 | ✅ 已修复（`is_playing=False` 使 `get_offset_duration()` 直接返回 0） |
| 2 | 确认失败后重播 | 高 | ✅ 已修复（`is_playing=False` + `_start_time=0`） |
| 3 | Jellyfin fallback 成功后状态不一致 | 中 | ✅ 已修复（fallback 成功时调用 `_mark_play_started()`） |

### 潜在风险点

| # | 风险点 | 描述 | 验证方法 |
|---|--------|------|----------|
| A | `get_if_xiaoai_is_playing()` 状态不可靠 | autonext_guard 依赖此函数判断是否切歌，可能误判 | 添加连续探测机制 |
| B | timer 触发但歌曲实际未播放 | timer 未取消，但歌曲实际停止，会一直等到 timer 触发 | 观察日志中 timer_fired 后的行为 |
| C | `_play_next()` 使用 `get_cur_music()` | 在某些边缘情况下可能切到错误的歌曲 | 验证日志中歌曲名称一致性 |

### 死路分析

| # | 假设的死路 | 原因 |
|---|------------|------|
| 1 | `_start_time=0` 会导致 `near_end_with_timer` 误触发 | ❌ 死路。`is_playing=False` 时 `get_offset_duration()` 直接返回 0，不计算 offset |
| 2 | 取消 timer 后 autonext_guard 会接管 | ⚠️ 部分正确。autonext_guard 会检查 `still_playing`，但状态不可靠 |
| 3 | `_play_list` 同步问题导致切歌不准 | ⚠️ 可能正确。需要日志验证 |

---

## 建议的验证步骤

### 验证1：确认修复已生效

在日志中搜索以下序列：
```
play_start_not_confirmed (auto_next) no_retry → is_playing 已重置 → timer 未取消
```

### 验证2：检查 autonext_guard 是否被误触发

搜索 `autonext_guard_trigger`，观察触发时：
- `_start_time` 的值
- `offset` 的值
- `still_playing` 的值

### 验证3：检查切歌序列一致性

搜索以下日志，确认 `get_next_music` 返回的歌曲和实际播放的歌曲一致：
```
get_next_music → 歌曲名
_play_next. name:歌曲名 → 歌曲名
【歌曲名】已经开始播放了
```

### 验证4：添加连续探测机制（如果状态不可靠）

当前 `autonext_guard` 的 `still_playing` 检查只有一次探测，建议改为：
```python
async def _guard_autonext():
    # 连续探测 3 次，减少误判
    still_playing = False
    for _ in range(3):
        if await self.get_if_xiaoai_is_playing():
            still_playing = True
            break
        await asyncio.sleep(0.5)
    if not still_playing:
        return
    ...
```

---

## 附录：关键代码位置

| 功能 | 文件位置 |
|------|----------|
| `_mark_play_started` | `device_player.py` L1052-1110 |
| `get_offset_duration` | `device_player.py` L255-310 |
| `_background_confirm_playback_started` | `device_player.py` L1160-1270 |
| `set_next_music_timeout` | `device_player.py` L1970-2020 |
| `cancel_next_timer` | `device_player.py` L2193-2210 |
| `_play_next` | `device_player.py` L643-680 |
| `_playmusic` | `device_player.py` L782-960 |
| `_stage_playlist_navigation_transition` | `device_player.py` L629-641 |

---

## 总结

1. **前两轮修复已正确实施**：`no_retry` + `is_playing` 重置
2. **timer 未取消是设计选择**：依赖 timer 自然触发，不依赖 autonext_guard 接管
3. **潜在风险**：`get_if_xiaoai_is_playing()` 状态不可靠，可能导致 autonext_guard 误判
4. **无法获取实时日志**：SSH 无法访问 192.168.7.178，需要其他方式获取日志验证
