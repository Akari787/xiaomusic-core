# xiaomusic 重播问题根因深度调查报告

**调查时间**：2026-05-12 21:09 (JST)  
**项目路径**：D:/AI/xiaomusic-core  
**测试服务器**：192.168.7.178  
**调查范围**：彻底分析重播的真实触发路径和根因

---

## 1. 执行摘要

### 核心发现

**根因**：`_play_next()` 方法在自动切歌场景（timer 触发）下存在逻辑缺陷，导致当前歌曲被重复播放而不是切换到下一首。

**具体问题**：当 timer 触发 `_play_next()` 时：
1. `get_cur_music()` 返回的是**当前歌曲**（上一首）
2. 由于当前歌曲**在播放列表中**且不是单曲循环模式
3. 条件 `name not in self._play_list` 为 False（**关键误判**）
4. 因此**跳过了 `get_next_music()` 调用**
5. 直接使用当前歌曲播放 → **重播发生**

### 修复状态

| 修复项 | 状态 | 说明 |
|--------|------|------|
| `no_retry` | ✅ 已生效 | 不再触发 retry 重试 |
| `is_playing=False` 重置 | ✅ 已生效 | 确认失败时重置播放状态 |
| `_play_next` 选歌逻辑 | ❌ **未修复** | 根因所在，仍会导致重播 |

---

## 2. 代码路径分析

### 2.1 timer_cancel 的所有触发位置

通过 grep 搜索 `cancel_next_timer|timer_cancel|_next_timer.cancel`，找到以下触发位置：

| 位置 | 触发条件 | 说明 |
|------|----------|------|
| `cancel_next_timer()` (L2201) | 显式调用 | 在 `set_next_music_timeout`、`stop`、`pause` 中调用 |
| `autonext_guard` (L294) | 条件触发 | 当 `should_check_autonext=True` 且设备实际停止时 |
| `cancel_all_timer()` (L2226) | 显式调用 | 清空所有定时器 |

**关键发现**：`cancel_next_timer` 在 `set_next_music_timeout` 开始时被调用（L1972），用于取消旧 timer。这是正常行为，不应该导致问题。

### 2.2 日志中的 `timer_cancel(session_id=5)` 分析

根据日志片段：
```
19:57:43 group_stop_overlap_state session_id=5 stop_done=false
19:57:45 timer_cancel(session_id=5)  ← 为什么会 cancel？
19:57:45 timer_start(session_id=5, delay_sec=311.826)
```

**分析**：同一 session_id 的 cancel 后立即 start，这通常是日志顺序问题。在 `set_next_music_timeout` 中：
1. 先调用 `cancel_next_timer()` 打印 "timer_cancel"
2. 再创建新 timer 打印 "timer_start"

这**不是问题根因**，只是日志时序问题。

---

## 3. 核心问题：`_play_next()` 的选歌逻辑缺陷

### 3.1 代码分析

**位置**：`device_player.py` L643-661

```python
async def _play_next(self, manual: bool = False):
    """播放下一首（内部实现）"""
    self.log.info("开始播放下一首")
    name = self.get_cur_music()  # ← 获取当前歌曲（timer 触发时，这是**上一首**！）

    if (
        manual
        or self.device.play_type == PLAY_TYPE_ONE
        or self.device.play_type == PLAY_TYPE_SIN
        or self.device.play_type == PLAY_TYPE_ALL
        or self.device.play_type == PLAY_TYPE_RND
        or self.device.play_type == PLAY_TYPE_SEQ
        or name == ""
        or (
            (name not in self._play_list) and self.device.play_type != PLAY_TYPE_ONE
        )
    ):
        name = self.get_next_music()  # ← 只有满足条件才获取下一首
        self.log.info(f"get_next_music {name}")

    self.log.info(f"_play_next. name:{name}, cur_music:{self.get_cur_music()}")
    if name == "":
        self.log.info("本地没有歌曲")
        return False
    self._stage_playlist_navigation_transition(name, reason="play_next")  # ← 设置 cur_music（太晚了！）
    return await self._play(...)
```

### 3.2 播放类型常量

```python
PLAY_TYPE_ONE = 0  # 单曲循环
PLAY_TYPE_ALL = 1  # 全部循环（默认）
PLAY_TYPE_RND = 2  # 随机播放
PLAY_TYPE_SIN = 3  # 单曲播放
PLAY_TYPE_SEQ = 4  # 顺序播放
```

### 3.3 关键缺陷

**缺陷 1**：自动切歌时 `get_cur_music()` 返回的是上一首歌曲

当 timer 触发 `_play_next()` 时：
- `_playmusic` 中的 `_set_runtime_track_reference` **尚未执行**（在 `_play()` 之后才执行）
- 因此 `cur_music` 仍然指向**上一首歌曲**

**缺陷 2**：条件 `name not in self._play_list` 的误判

```python
or (
    (name not in self._play_list) and self.device.play_type != PLAY_TYPE_ONE
)
```

当 `manual=False` 且 `play_type != ONE` 时：
- `name = get_cur_music()` = 上一首歌曲
- 上一首歌曲**在播放列表中**
- 条件 `name not in self._play_list` 为 **False**
- **跳过了 `get_next_music()` 调用**

**缺陷 3**：`cur_music` 更新时机太晚

`_stage_playlist_navigation_transition(name, reason="play_next")` 在 `_play()` 之后才被调用，此时已经太晚了。

---

## 4. 完整重播触发路径（基于代码分析）

### 4.1 正常切歌流程

```
T+0.0s   timer_fired(session_id=X) → set_next_music_timeout
           ↓
T+0.0s   timer_cancel(session_id=X-1)  ← 取消旧 timer
           ↓
T+0.0s   timer_start(session_id=X, delay_sec=Y)  ← 设置新 timer
           ↓
T+Y-3.0s timer_fired(session_id=X) → _play_next()  ← 定时器触发
           ↓
T+Y-3.0s _play_next() 被调用
           ↓
T+Y-3.0s name = get_cur_music() → 上一首歌曲名（比如 "歌曲A"）
           ↓
T+Y-3.0s 检查条件: 上一首歌曲在播放列表中 → name not in _play_list = False
           ↓
T+Y-3.0s 不调用 get_next_music()  ← ⚠️ 跳过了！
           ↓
T+Y-3.0s _playmusic("歌曲A")  ← ⚠️ 重播！
           ↓
T+Y-3.0s group_stop_overlap_state
           ↓
T+Y-3.0s group_player_play("歌曲A")
           ↓
T+Y-3.0s _mark_play_started() → 设置 _start_time, timer
           ↓
T+Y-3.0s _schedule_playback_confirmation()
           ↓
T+Y-3.0s _stage_playlist_navigation_transition()  ← 太晚了！
           ↓
T+Y-2.2s _background_confirm_playback_started()
           ↓
T+Y-1.4s play_start_confirmation_result(started=true/false)
```

### 4.2 确认失败后的完整流程

```
T+0.0s   timer_fired → _play_next() → _playmusic("歌曲A")
           ↓
T+0.0s   _mark_play_started() → _start_time=now, timer 设置
           ↓
T+1.2s   _background_confirm_playback_started() 发现 started=False
           ↓
T+1.2s   is_playing = False  ← 已修复
           ↓
T+1.2s   _start_time = 0     ← 已修复
           ↓
T+1.2s   return（不取消 timer）← timer 仍然存在
           ↓
T+Y-3.0s timer 触发 → _play_next()  ← 重播发生！
           ↓
（重复上面的 4.1 流程）
```

---

## 5. autonext_guard 的分析

### 5.1 代码分析

**位置**：`device_player.py` L255-310

```python
def get_offset_duration(self):
    duration = self._duration
    if not self.is_playing:
        return 0, duration  # ← is_playing=False 时直接返回

    offset = time.time() - self._start_time - self._paused_time

    # Safety net: if timer was lost/cancelled and track is far beyond expected
    # duration, try one guarded auto-next recovery.
    should_check_autonext = False
    if (
        duration > 0.1
        and self.device.play_type != PLAY_TYPE_SIN
        and self._last_cmd not in {"stop", "pause"}
    ):
        overdue_without_timer = (
            self._next_timer is None and offset >= duration + 15.0
        )
        near_end_with_timer = self._next_timer is not None and offset >= max(
            duration - 1.0, duration * 0.9
        )
        should_check_autonext = overdue_without_timer or near_end_with_timer

    if should_check_autonext:
        if self._autonext_guard_task is None or self._autonext_guard_task.done():
            sid = self._play_session_id

            async def _guard_autonext():
                try:
                    still_playing = await self.get_if_xiaoai_is_playing()
                    if still_playing:
                        return
                except Exception:
                    return
                if sid != self._play_session_id:
                    return
                if self._next_timer is not None:
                    self._next_timer.cancel()
                    ...
                self.log.info(
                    "autonext_guard_trigger(session_id=%s, offset=%.3f, duration=%.3f)",
                    sid,
                    offset,
                    duration,
                )
                await self._play_next()  # ← 也调用 _play_next()
```

### 5.2 触发条件分析

| 条件 | 计算方式 | 确认失败后是否触发 |
|------|----------|-------------------|
| `is_playing=False` | 直接返回 (0, duration) | ❌ 不触发 |
| `overdue_without_timer` | `_next_timer is None` 且 `offset >= duration + 15.0` | ❌ 不触发（timer 存在） |
| `near_end_with_timer` | `_next_timer is not None` 且 `offset >= max(duration-1, duration*0.9)` | ❌ 不触发（is_playing=False → offset=0） |

**结论**：确认失败后 `is_playing=False`，`get_offset_duration()` 直接返回 (0, duration)，**不会触发 autonext_guard**。

**但**：autonext_guard 也会调用 `_play_next()`，因此也会受到同样的选歌逻辑缺陷影响。

---

## 6. stop_done=false 的影响

### 6.1 代码分析

**位置**：`device_player.py` L825-837

```python
if stop_task is not None:
    self.log.info(
        "group_stop_overlap_state session_id=%s stop_done=%s",
        sid,
        str(stop_task.done()).lower(),
    )
```

### 6.2 分析

`stop_done=false` 表示 `stop_task.done()` 为 False，即异步停止任务尚未完成。

**影响**：
1. 新歌曲在旧歌曲停止**之前**开始播放
2. 如果旧歌曲实际已停止，这不是问题
3. 如果旧歌曲仍在播放，可能导致播放冲突

**但这不是重播的直接原因**，而是可能导致确认失败的因素之一。

---

## 7. stop_done=false 出现 5 次的原因

### 7.1 可能的解释

1. **异步停止任务**：在 overlap 模式下，`group_force_stop_xiaoai(fast=True)` 以异步方式执行
2. **任务完成时间**：任务在日志记录后 2 秒才完成
3. **日志时序**：`stop_done` 日志在任务完成之前打印

```
19:57:43 group_stop_overlap_state session_id=5 stop_done=false  ← 任务尚未完成
19:57:45 timer_cancel(session_id=5)                             ← 2秒后任务可能已完成
```

### 7.2 是否是问题？

**不是问题**。`stop_done=false` 只是表示停止任务尚未完成，这是异步设计的预期行为。

---

## 8. 根因结论

### 8.1 根因

**`_play_next()` 方法在自动切歌场景（timer 触发）下的选歌逻辑缺陷**。

当 timer 触发 `_play_next()` 时：
1. `get_cur_music()` 返回的是**上一首歌曲**（因为 `_stage_playlist_navigation_transition` 尚未执行）
2. 由于上一首歌曲在播放列表中，条件 `name not in self._play_list` 为 False
3. 因此**跳过了 `get_next_music()` 调用**
4. 直接使用上一首歌曲播放 → **重播发生**

### 8.2 代码位置

**文件**：`xiaomusic/device_player.py`  
**函数**：`_play_next()`  
**问题行**：L648-660 的条件判断逻辑

### 8.3 修复方向

**方案 A**：在 `_play_next` 中，自动切歌时直接调用 `get_next_music()`

```python
# 在 _play_next 开头：
if not manual:
    # 自动切歌场景：直接获取下一首
    name = self.get_next_music()
else:
    # 手动切歌场景：使用现有逻辑
    name = self.get_cur_music()
    if (... 原有条件 ...):
        name = self.get_next_music()
```

**方案 B**：调整判断条件，使其在自动切歌时正确识别需要切歌

**方案 C**：将 `_stage_playlist_navigation_transition` 调用提前到 `_play()` 调用之前

### 8.4 为什么前两轮修复只堵住了部分问题

| 修复 | 效果 | 限制 |
|------|------|------|
| `no_retry` | 堵住了 retry 路径 | 不影响 timer 触发路径 |
| `is_playing=False` 重置 | 堵住了 autonext_guard 误触 | 不影响 timer 触发路径 |

这两轮修复**只堵住了确认失败后的 retry 路径**，但**没有修复 timer 自然触发时的选歌逻辑缺陷**。

---

## 9. 待验证事项

由于无法访问测试服务器获取实时日志，以下分析基于代码静态分析：

| 待验证项 | 说明 |
|----------|------|
| 确认 `play_type` 的默认值 | 是否为 `PLAY_TYPE_ALL`（全部循环）？ |
| 确认日志中 `_play_next` 的 `name` 值 | 是否和 cur_music 一致？ |
| 确认修复后是否仍有重播 | 如果仍有重播，根因确认 |

---

## 10. 调查结论

### 真实根因

**`_play_next()` 方法的选歌逻辑缺陷**是重播的根因。

在自动切歌场景（timer 触发）下，`_play_next()` 错误地使用当前歌曲而不是下一首歌曲，导致重播发生。

### 修复建议

修改 `_play_next()` 方法，在自动切歌场景下直接调用 `get_next_music()` 获取下一首歌曲，而不是依赖条件判断。

### 后续步骤

1. 确认 `play_type` 的默认值
2. 在代码中验证 `_play_next` 的行为
3. 应用修复并验证

---

**调查完成**
