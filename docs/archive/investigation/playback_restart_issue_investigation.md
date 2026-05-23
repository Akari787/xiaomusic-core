# xiaomusic-core 自动切歌后重头开始问题调查报告

**日期**: 2026-05-11  
**文件**: `docs/playback_restart_issue_investigation.md`  
**主要文件**: `xiaomusic/device_player.py`

---

## 问题描述

**现象**: 自动播放下一首后，有概率播放几秒后重头重新开始播放。

**初步推测的逻辑路径**:
1. `_play_next()` 调用 `_playmusic(confirm_start_in_background=True, fast_stop=True)`
2. `_playmusic` 立即调用 `_mark_play_started` 设置 timer，不等待确认
3. `_schedule_playback_confirmation` 调度后台确认任务 `_background_confirm_playback_started`
4. `_confirm_playback_started` 等待 1.2 秒后发现 `started=False`
5. `started=False` → `_handle_play_failure` → 在后台调用 `_retry_next()`
6. `_retry_next()` 在 backoff delay 后再次调用 `_play_next()`，但此时设备上歌曲才刚开始播放几秒

---

## 验证过程

### 1. 代码层面验证

#### 1.1 `_play_next` 调用 `_playmusic` 的流程

**代码路径** (device_player.py):
```
_play_next() [L643]
  → _stage_playlist_navigation_transition() [L629]  # 设置 is_playing=False
  → _play(..., confirm_start_in_background=not manual, fast_stop=not manual) [L666]
    → _play_internal() [L546]
      → _playmusic() [L782]
```

**关键代码片段**:

```python
# _play_next (L643-670)
async def _play_next(self, manual: bool = False):
    self._stage_playlist_navigation_transition(name, reason="play_next")  # is_playing = False
    return await self._play(
        name,
        preserve_playlist=manual,
        confirm_start_in_background=not manual,  # 自动模式=True
        fast_stop=not manual,  # 自动模式=True
    )
```

#### 1.2 `_playmusic` 中的 session 和确认逻辑

**关键代码片段** (L782-880):

```python
async def _playmusic(self, name, *, confirm_start_in_background=False, fast_stop=False):
    # 新session：使旧session的延迟任务失效
    sid = self._bump_play_session(reason="start_new_play")  # ★ session bump

    self.is_playing = True  # ★ 立即设置为True

    # ... 停止旧歌曲、播放新歌曲 ...

    if confirm_start_in_background:
        await self._mark_play_started(...)  # ★ 立即设置timer和开始时间
        self._schedule_playback_confirmation(...)  # ★ 调度后台确认
        return True
```

**发现**: `sid` 在 `_playmusic` 开头通过 `_bump_play_session` 生成，并立即被用于后续所有延迟任务。

#### 1.3 `_background_confirm_playback_started` 的处理逻辑

**关键代码片段** (L1161-1230):

```python
async def _background_confirm_playback_started(self, *, name, sid, ...):
    started = await self._confirm_playback_started(
        name, sid,
        delay_sec=...,  # 默认 800ms
        retries=...,    # 默认 0
        interval_sec=...,  # 默认 300ms
    )

    # ★ 关键检查：session mismatch 则丢弃
    if sid != self._play_session_id:
        log "timer_discard_due_to_sid_mismatch"
        return

    if started is False:
        # Jellyfin fallback 尝试...
        if proxy_url:
            await self._mark_play_started(...)
            return
        await self.cancel_next_timer()
        await self._handle_play_failure(name=name, sid=sid, reason="play_start_not_confirmed")
        return
```

**发现**: 在 `started` 判断之前，有 `sid != self._play_session_id` 的检查。如果在这期间有新的播放启动，这个后台任务会被丢弃。

#### 1.4 `_confirm_playback_started` 的检测逻辑

**关键代码片段** (L1246-1305):

```python
async def _confirm_playback_started(self, name, sid, *, delay_sec=1.2, retries=2, interval_sec=0.6):
    await asyncio.sleep(delay_sec)  # 等待歌曲启动
    saw_true = False
    saw_false = False
    saw_drop_after_true = False

    for idx in range(retries + 1):
        started = await self.get_if_xiaoai_is_playing()
        if started:
            saw_true = True
        elif saw_true:
            saw_drop_after_true = True
            saw_false = True
        else:
            saw_false = True
        if idx < retries:
            await asyncio.sleep(interval_sec)
    else:
        if saw_drop_after_true:
            result = False  # ★ 播放后又停止了
        elif saw_true:
            result = True   # ★ 一直播放
        elif saw_false:
            result = False  # ★ 从未播放
        else:
            result = None
    return result
```

**发现**: 
- 默认 `delay_sec=1.2`，`retries=0`，意味着只探测一次
- 如果探测时 `started=False`，且配置未启用 retry，则直接返回 `False`

#### 1.5 `_handle_play_failure` 和 `_retry_next` 的逻辑

**关键代码片段** (L2004-2048):

```python
async def _handle_play_failure(self, *, name: str, sid: int, reason: str):
    self._play_failed_cnt += 1
    delay = min(1.0 * (2 ** max(self._play_failed_cnt - 1, 0)), 8.0)

    # 失败次数过多则 degrade
    if self._play_failed_cnt >= 5 or total_elapsed >= 60:
        self._degraded = True
        return

    async def _retry_next():
        await asyncio.sleep(delay)
        # ★ 关键保护：session mismatch 则丢弃
        if sid != self._play_session_id:
            log "timer_discard_due_to_sid_mismatch"
            return
        # ★ 关键保护：检查播放状态
        if not self.is_playing or self._last_cmd == "stop":
            return
        if self._degraded:
            return
        await self._play_next()  # ★ 重试播放下一首

    asyncio.create_task(_retry_next())
```

**发现**: `_retry_next` 中有两个关键保护：
1. `sid != self._play_session_id` - 旧 session 的任务被丢弃
2. `not self.is_playing or self._last_cmd == "stop"` - 如果没在播放或已停止则不重试

#### 1.6 session 管理机制 `_bump_play_session`

**关键代码片段** (L712-728):

```python
def _bump_play_session(self, reason: str = "") -> int:
    self._play_session_id += 1
    # 取消所有旧的后台任务
    if self._duration_probe_task and not self._duration_probe_task.done():
        self._duration_probe_task.cancel()
    if self._playback_confirm_task and not self._playback_confirm_task.done():
        self._playback_confirm_task.cancel()
    if self._playback_status_probe_task and not self._playback_status_probe_task.done():
        self._playback_status_probe_task.cancel()
    log "play_session_bump(session_id=%s, reason=%s)", self._play_session_id, reason
    return self._play_session_id
```

**发现**: `_bump_play_session` 会取消所有旧的后台确认任务，确保它们不会干扰新 session。

---

### 2. Session 保护机制分析

通过代码分析，session 保护机制如下：

| 时机 | 保护位置 | 保护逻辑 |
|------|----------|----------|
| `_playmusic` 开始 | L785 | `sid = self._bump_play_session()` - 新session生成，旧的confirm任务被取消 |
| `_background_confirm_playback_started` 开始 | L1185 | `if sid != self._play_session_id: return` |
| Jellyfin fallback 等待后 | L1224 | `if sid != self._play_session_id: return` |
| `_retry_next` 执行前 | L2034 | `if sid != self._play_session_id: return` |
| `_retry_next` 执行前 | L2042 | `if not self.is_playing or self._last_cmd == "stop": return` |
| `set_next_music_timeout` 定时器触发 | L1968 | `if sid != self._play_session_id: return` |
| `get_offset_duration` autonext_guard | L291 | `if sid != self._play_session_id: return` |

---

### 3. 根因分析

#### 3.1 推测路径验证

用户推测的路径：
```
_play_next() → _playmusic(confirm_start_in_background=True)
→ _mark_play_started (立即设置timer)
→ _schedule_playback_confirmation (后台确认)
→ _confirm_playback_started 等待1.2s后发现 started=False
→ _handle_play_failure → _retry_next()
→ _retry_next() 在backoff delay后再次调用 _play_next()
```

**验证结果**: 路径基本正确，但 session 保护机制会阻止大多数情况下的冲突。

#### 3.2 可能的根因

根据代码分析，问题可能出在以下几个地方：

**根因A: `is_playing` 状态与实际播放不同步**

```python
# _playmusic 中立即设置
self.is_playing = True  # L798

# _stage_playlist_navigation_transition 中设置为False
self.is_playing = False  # L633 (在 _play_next 中调用)
```

问题：
- `_stage_playlist_navigation_transition` 在 `_play_next` 开头被调用，设置 `is_playing=False`
- 但 `_playmusic` 立即设置 `is_playing=True`
- 如果 `_retry_next` 被触发时，`is_playing` 已经因为某些原因被设置为 False（但歌曲还在播放），则保护失效

**根因B: 小米API返回不准确的播放状态**

```python
async def get_if_xiaoai_is_playing(self, device_id=None):
    playing_info = await self.auth_manager.mina_call("player_get_status", ...)
    is_playing = json.loads(playing_info.get("data", {}).get("info", "{}")).get("status", -1) == 1
    return is_playing
```

问题：
- 小米API的 `status` 字段可能不准确
- 在歌曲切换的瞬间，旧的播放实例可能还没完全结束，新的播放实例已经开始
- 如果 `player_get_status` 在这个时间窗口内返回 `status=0`，会被误判为"未开始播放"

**根因C: Jellyfin proxy fallback 场景**

```python
# _background_confirm_playback_started 中
if started is False:
    if jellyfin_auto_candidate:
        proxy_url = await self._try_proxy_fallback(...)
        if proxy_url:
            await self._mark_play_started(...)  # 成功，直接返回
            return
    # fallback失败才会调用 _handle_play_failure
    await self._handle_play_failure(...)
```

问题：
- 在 `started=False` 时，如果 Jellyfin proxy fallback 成功，会调用 `_mark_play_started`
- 但这个路径可能在某些边缘情况下导致状态不一致

#### 3.3 最可能的根因

**最可能的问题**: 小米API `player_get_status` 在歌曲切换时的状态不确定性

时序图:
```
T=0:     _play_next() 调用 _playmusic()
T=0:     _bump_play_session() 生成 sid=1，取消旧的后台任务
T=0-0.5: 停止旧歌曲
T=0.5-1: 播放新歌曲（发送播放命令）
T=1.0:   _mark_play_started() 设置timer，设置 is_playing=True
T=1.0:   _schedule_playback_confirmation() 调度后台确认任务
T=1.0-1.2: 后台确认任务等待
T=1.2:   get_if_xiaoai_is_playing() 调用 player_get_status
T=1.2:   ★ 关键时刻：小米API返回 status=0（歌曲刚切换，可能有延迟）
T=1.2:   started=False
T=1.2:   _handle_play_failure() 被调用，_retry_next() 被调度
T=2-3:   _retry_next() 在 backoff delay 后执行
T=2-3:   ★ 此时歌曲其实已经在播放，但因为 is_playing=True，所以 _retry_next() 会执行
T=2-3:   _play_next() 被再次调用，歌曲从头开始
```

**问题核心**: `_retry_next` 中的 `if not self.is_playing` 检查在这种情况下无效，因为：
- `is_playing` 在 `_playmusic` 中被设置为 `True`
- `is_playing` 是本地状态，不反映实际播放是否成功
- `_retry_next` 检查的是 `is_playing` 而不是实际播放状态

---

### 4. 配置参数影响

通过代码分析，以下配置参数会影响此问题：

| 配置项 | 默认值 | 影响 |
|--------|--------|------|
| `auto_next_confirm_delay_ms` | 800 | 确认延迟，影响误判概率 |
| `auto_next_confirm_retries` | 0 | retry次数，增加确认可靠性 |
| `auto_next_confirm_interval_ms` | 300 | retry间隔 |

如果 `retries=0`，则只探测一次，容易误判。如果启用 retries（如 `retries=2`），会进行多次探测，减少误判。

---

## 结论

### 推测验证结果

**推测路径**: 大部分正确，但 session 保护机制会阻止大多数冲突。

**真实根因**: 小米API `player_get_status` 在歌曲切换时可能返回不准确的 `status`，导致：
1. `_confirm_playback_started` 在歌曲实际已开始播放后仍返回 `False`
2. `_handle_play_failure` 被错误触发
3. `_retry_next` 中的 `is_playing` 检查无法防止重新播放（因为 `is_playing` 已被设置为 `True`）
4. 歌曲从头开始播放

### 时序图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 自动切歌后重头开始时序图                                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  T=0      _play_next()                                                       │
│           ├─ _stage_playlist_navigation_transition() → is_playing=False      │
│           └─ _playmusic(confirm_start_in_background=True)                    │
│                 │                                                             │
│  T=0      sid = _bump_play_session() → session=1                            │
│                 │                                                             │
│  T=0-0.5  stop current song                                                  │
│                 │                                                             │
│  T=0.5-1  play next song (send command)                                       │
│                 │                                                             │
│  T=1.0     is_playing = True                                                 │
│           _mark_play_started() → set timer                                  │
│           _schedule_playback_confirmation() → schedule background task       │
│                 │                                                             │
│  T=1.0     return to caller                                                   │
│                 │                                                             │
│  T=1.0-1.2 (background) _confirm_playback_started() sleeps                   │
│                 │                                                             │
│  T=1.2      get_if_xiaoai_is_playing() → player_get_status                   │
│                 │                                                             │
│  T=1.2      ★ 小米API返回 status=0 (切换延迟)                                  │
│           started = False                                                    │
│                 │                                                             │
│  T=1.2      _handle_play_failure()                                           │
│           _retry_next() is scheduled (delay=1.0s)                           │
│                 │                                                             │
│  T=2.2      _retry_next() executes                                            │
│           ├─ sid == _play_session_id ✓ (都是1)                                │
│           ├─ is_playing == True ✓ (已在_playmusic中设置为True)                 │
│           └─ ★ 不满足保护条件，_play_next() 被调用                              │
│                 │                                                             │
│  T=2.2+     歌曲从头开始播放（因为 _play_next 再次触发播放）                    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 保护机制失效原因

1. **session 保护**: `sid == _play_session_id`（都是1），保护失效
2. **is_playing 保护**: `is_playing == True`（已在 `_playmusic` 中设置为True），保护失效
3. **只有 Jellyfin proxy fallback 成功时才会阻止重试**，但非 Jellyfin 源没有这个保护

### 修复建议（不在本次调查范围内）

1. 在 `_retry_next` 中增加实际播放状态检查，不只依赖 `is_playing`
2. 增加 `auto_next_confirm_retries` 配置，减少单次探测的误判
3. 在 `_confirm_playback_started` 中增加更长的等待时间和多次探测

---

## 附录：关键代码位置

| 功能 | 代码位置 |
|------|----------|
| `_play_next` | L643-670 |
| `_stage_playlist_navigation_transition` | L629-641 |
| `_playmusic` | L782-890 |
| `_bump_play_session` | L712-728 |
| `_background_confirm_playback_started` | L1161-1230 |
| `_confirm_playback_started` | L1246-1305 |
| `_handle_play_failure` | L2004-2048 |
| `get_if_xiaoai_is_playing` | L1344-1356 |

---

## 参考配置

可以通过以下配置调整确认逻辑（需在配置文件中设置）:

```yaml
auto_next_confirm_delay_ms: 800    # 增加此值可给歌曲更多启动时间
auto_next_confirm_retries: 2       # 增加此值可进行多次确认探测
auto_next_confirm_interval_ms: 300  # 探测间隔
```