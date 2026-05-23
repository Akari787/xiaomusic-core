# 自动切歌后重头开始问题 - 修复方案设计（方向B）

**日期**: 2026-05-11  
**文件**: `docs/playback_restart_fix_design.md`  
**主要文件**: `xiaomusic/device_player.py`  
**修复方向**: 方向B - 禁用确认失败后的 retry，依赖 timer 自然触发切歌

---

## 1. 问题根因回顾

根据调查报告，确认的根因是：

1. `_play_next` 调用 `_playmusic(confirm_start_in_background=True)`
2. `_playmusic` 立即设置 `is_playing=True` 和 timer
3. `_background_confirm_playback_started` 等待 1.2s 后探测 `started=False`（API 状态延迟）
4. `started=False` → `_handle_play_failure` → 调度 `_retry_next()`
5. `_retry_next()` 检查 `is_playing=True`（保护失效），在 backoff delay 后再次调用 `_play_next()`
6. 歌曲被重新从头播放

---

## 2. `_retry_next` 的调用来源分析

### 2.1 调用路径总览

```
_handle_play_failure(name, sid, reason)
  └── 创建 _retry_next() 协程任务（asyncio.create_task）

触发 _handle_play_failure 的位置：
├── _background_confirm_playback_started [L1190]
│     └── started=False 时调用
├── _confirm_playback_started 同步路径 [_playmusic 中]
│     └── started=False 且非 confirm_start_in_background 时调用 [L895]
├── _playmusic 中 player_play 全部失败 [L853-856]
└── 其他 Jellyfin fallback 失败场景
```

### 2.2 关键代码位置

#### 路径A: `_background_confirm_playback_started` 中的调用（L1190）

```python
# device_player.py L1185-1194
if started is False:
    proxy_url = ""
    if jellyfin_auto_candidate:
        proxy_url = await self._try_proxy_fallback(...)
    if proxy_url:
        await self._mark_play_started(...)
        return
    await self.cancel_next_timer()
    await self._handle_play_failure(
        name=name, sid=sid, reason="play_start_not_confirmed"
    )
    return
```

**这是本次问题的直接触发点**。当 `started=False` 且 Jellyfin fallback 失败或不是 Jellyfin 源时，调用 `_handle_play_failure`。

#### 路径B: `_playmusic` 同步确认失败（L895-900）

```python
# device_player.py L895-902
started = await self._confirm_playback_started(name, sid)
if started is False:
    if jellyfin_auto_candidate:
        proxy_url = await self._try_proxy_fallback(...)
        ...
    await self._handle_play_failure(
        name=name, sid=sid, reason="play_start_not_confirmed"
    )
```

**这是手动触发场景**（`confirm_start_in_background=False`）。此时 `_playmusic` 等待确认，如果失败则重试。

#### 路径C: `_playmusic` 中 `group_player_play` 全部失败（L853-858）

```python
# device_player.py L853-858
if all(ele is None for ele in results):
    ...
    await self._handle_play_failure(
        name=name, sid=sid, reason="player_play_failed"
    )
    return False
```

**这是播放命令本身失败**，不是状态确认问题。此场景的 retry 是合理的。

### 2.3 `_retry_next` 的保护机制（当前代码）

```python
# device_player.py L2030-2046
async def _retry_next():
    await asyncio.sleep(delay)
    # 保护1：session mismatch 丢弃
    if sid != self._play_session_id:
        return
    # 保护2：播放状态检查
    if not self.is_playing or self._last_cmd == "stop":
        return
    if self._degraded:
        return
    await self._play_next()
```

**问题**：保护2在本次场景中失效，因为 `is_playing` 在 `_playmusic` 中已被设置为 `True`，歌曲虽然已成功播放，但 API 状态探测返回 `False`，导致错误地触发重试。

---

## 3. 方案对比

### 方案1：直接在 `_handle_play_failure` 中区分场景禁用 retry

**核心思路**：在 `_handle_play_failure` 中增加判断，如果 reason 是 `"play_start_not_confirmed"` 且是自动切歌场景（`_background_confirm_playback_started` 触发），不触发 `_retry_next`，只取消 timer 并记录失败。

**实现方式**：传递额外参数或用不同的 reason 字符串区分场景。

**优点**：
- 改动集中在一个函数
- 可以区分不同失败原因

**缺点**：
- 需要在多个调用点传递额外参数
- `_handle_play_failure` 的签名需要修改
- 调用点需要区分"自动"和"手动"场景

**改动范围**：
- 修改 `_handle_play_failure` 签名，增加 `auto_next` 参数
- 修改 `_background_confirm_playback_started` 调用点
- 修改 `_playmusic` 同步确认失败调用点

---

### 方案2：在 `started=False` 时区分自动/手动场景，不 retry 自动切歌

**核心思路**：在 `started=False` 的处理分支中，如果来自自动切歌场景（`confirm_start_in_background=True` 路径），只取消 timer，不调用 `_handle_play_failure` 的 retry 机制。

**实现方式**：在 `_background_confirm_playback_started` 中，`started=False` 时直接记录失败状态，不走 `_handle_play_failure`，也不触发 `_retry_next`。歌曲继续播放，依赖 timer 自然触发切歌。

**优点**：
- 改动范围小，只改一个函数（L1185-1194）
- 逻辑清晰，自动切歌确认失败时不做任何干预
- 不改变 `_handle_play_failure` 的行为，保留其他场景的 retry

**缺点**：
- 如果 Jellyfin fallback 成功又失败，可能导致行为不一致
- 需要仔细处理 timer 取消逻辑

**改动范围**：
- 修改 `_background_confirm_playback_started` 中 `started=False` 的处理分支
- 移除该分支中的 `await self._handle_play_failure(...)` 调用

---

### 方案3：更彻底的改动 - 在 `confirm_start_in_background=True` 路径中完全移除 retry

**核心思路**：在 `confirm_start_in_background=True` 路径中，确认失败时只记录状态，不改变播放行为。不取消 timer，不调用 retry，让歌曲自然播放结束或被 autonext_guard 接管。

**实现方式**：在 `_background_confirm_playback_started` 中，`started=False` 时不做任何干预（不取消 timer，不调用 `_handle_play_failure`）。

**优点**：
- 最彻底的修复，完全不干预自动切歌流程
- 依赖现有的 timer 和 autonext_guard 机制
- 不会影响其他场景的 retry 逻辑

**缺点**：
- 如果 timer 已设置但歌曲实际播放失败，会一直播放到 timer 触发
- 需要确保 autonext_guard 能正确接管

**改动范围**：
- 修改 `_background_confirm_playback_started` 中 `started=False` 的处理分支
- 不调用 `cancel_next_timer()` 和 `_handle_play_failure()`
- 让歌曲自然播放

---

### 方案对比总结

| 维度 | 方案1 | 方案2 | 方案3 |
|------|-------|-------|-------|
| **改动范围** | 中等（改签名 + 2个调用点） | 小（只改1个函数） | 小（只改1个函数） |
| **风险** | 中等（需要修改多处） | 低（只改一个分支） | 低（不干预播放） |
| **效果** | 好（明确区分场景） | 好（直接不 retry） | 好（完全依赖 timer） |
| **代码复杂度** | 高（需要参数传递） | 低（直接改分支） | 最低（只删除代码） |

---

## 4. 推荐方案：方案2（最小改动，风险最低）

### 4.1 选择理由

1. **改动范围最小**：只修改 `_background_confirm_playback_started` 中 `started=False` 的处理分支
2. **风险最低**：不改变 `_handle_play_failure` 的行为，保留其他场景（手动触发、player_play 失败）的 retry
3. **效果明确**：自动切歌确认失败时不干预，让歌曲继续播放，依赖 timer 自然触发切歌
4. **符合方向B**：禁用自动切歌场景的 retry，依赖 timer 自然触发切歌

### 4.2 具体改动

**文件**: `xiaomusic/device_player.py`

**位置**: `_background_confirm_playback_started` 函数内，`started=False` 的处理分支

#### 改动前（L1185-1194）：

```python
if started is False:
    proxy_url = ""
    if jellyfin_auto_candidate:
        proxy_url = await self._try_proxy_fallback(
            name=name,
            sid=sid,
            origin_url=origin_url,
            fast_stop=fast_stop,
            reason="play_start_not_confirmed",
            verify_started=True,
        )
    if proxy_url:
        await self._mark_play_started(
            name=name,
            sid=sid,
            cur_playlist=cur_playlist,
            measure_status=fast_stop,
        )
        return
    await self.cancel_next_timer()
    await self._handle_play_failure(
        name=name, sid=sid, reason="play_start_not_confirmed"
    )
    return
```

#### 改动后：

```python
if started is False:
    proxy_url = ""
    if jellyfin_auto_candidate:
        proxy_url = await self._try_proxy_fallback(
            name=name,
            sid=sid,
            origin_url=origin_url,
            fast_stop=fast_stop,
            reason="play_start_not_confirmed",
            verify_started=True,
        )
    if proxy_url:
        await self._mark_play_started(
            name=name,
            sid=sid,
            cur_playlist=cur_playlist,
            measure_status=fast_stop,
        )
        return

    # 【方案2改动】自动切歌场景，确认失败时不触发 retry
    # 取消 timer，让歌曲自然播放或被 autonext_guard 接管
    # 注意：此处不调用 _handle_play_failure，避免 _retry_next 重新播放
    await self.cancel_next_timer()

    # 仅记录失败状态，不触发重试
    self._play_failed_cnt += 1
    self._play_fail_last_reason = "play_start_not_confirmed"
    if self._play_fail_first_ts <= 0:
        self._play_fail_first_ts = time.time()
    self.log.info(
        "play_start_not_confirmed (auto_next) cnt=%d name=%s",
        self._play_failed_cnt,
        name,
    )
    return
```

### 4.3 行为变化

改动后，自动切歌场景的行为变化：

| 场景 | 改动前 | 改动后 |
|------|--------|--------|
| `started=False`（非 Jellyfin 源） | 取消 timer + 调用 `_handle_play_failure`（触发 `_retry_next`） | 取消 timer + 记录失败状态（不触发 retry） |
| `started=False`（Jellyfin fallback 成功） | 正常播放（不变） | 正常播放（不变） |
| `started=False`（Jellyfin fallback 失败） | 取消 timer + 调用 `_handle_play_failure` | 取消 timer + 记录失败状态（不触发 retry） |
| 手动触发 `_play_next` 时 `started=False` | 通过 `_playmusic` 同步路径处理（不变） | 不受影响 |
| `player_play` 失败 | 调用 `_handle_play_failure`（不变） | 不受影响 |

---

## 5. 影响预估

### 5.1 确认失败时歌曲会继续播放吗？

**是**。自动切歌场景确认失败时，不再触发 `_retry_next`，歌曲会继续播放。

- 如果 API 状态延迟导致误判，歌曲实际上已经在播放，会继续正常播放直到 timer 触发切歌
- 如果歌曲确实没有播放成功，会一直播放（直到 autonext_guard 在超时后触发）

### 5.2 自动切歌的 timer 会被取消吗？

**会**。在 `started=False` 时，仍然调用 `await self.cancel_next_timer()`，timer 会被取消。

### 5.3 autonext_guard 机制会接管吗？

**可能**。如果：
1. timer 被取消
2. 歌曲实际没有正常播放
3. 播放时间超过 `duration + 15.0` 秒

则 `get_offset_duration()` 中的 `autonext_guard` 会触发切歌：

```python
overdue_without_timer = (
    self._next_timer is None and offset >= duration + 15.0
)
```

---

## 6. 备选方案：方案3（更彻底的改动）

如果方案2仍有疑虑，可以选择方案3：完全不干预 `started=False` 的场景。

### 6.1 方案3改动

**文件**: `xiaomusic/device_player.py`

**位置**: `_background_confirm_playback_started` 函数内，`started=False` 的处理分支

#### 改动前（L1185-1194）：

```python
if started is False:
    proxy_url = ""
    if jellyfin_auto_candidate:
        proxy_url = await self._try_proxy_fallback(...)
    if proxy_url:
        await self._mark_play_started(...)
        return
    await self.cancel_next_timer()
    await self._handle_play_failure(
        name=name, sid=sid, reason="play_start_not_confirmed"
    )
    return
```

#### 改动后：

```python
if started is False:
    proxy_url = ""
    if jellyfin_auto_candidate:
        proxy_url = await self._try_proxy_fallback(
            name=name,
            sid=sid,
            origin_url=origin_url,
            fast_stop=fast_stop,
            reason="play_start_not_confirmed",
            verify_started=True,
        )
    if proxy_url:
        await self._mark_play_started(
            name=name,
            sid=sid,
            cur_playlist=cur_playlist,
            measure_status=fast_stop,
        )
        return

    # 【方案3改动】自动切歌场景，确认失败时不做任何干预
    # 不取消 timer，不触发 retry，让歌曲自然播放或被 autonext_guard 接管
    self._play_failed_cnt += 1
    self._play_fail_last_reason = "play_start_not_confirmed"
    if self._play_fail_first_ts <= 0:
        self._play_fail_first_ts = time.time()
    self.log.info(
        "play_start_not_confirmed (auto_next) no_intervention cnt=%d name=%s",
        self._play_failed_cnt,
        name,
    )
    return
```

### 6.2 方案3与方案2的区别

| 维度 | 方案2 | 方案3 |
|------|-------|-------|
| `cancel_next_timer()` | 调用 | 不调用 |
| timer 状态 | 取消 | 保留 |
| 依赖 autonext_guard | 可能接管 | 更可能接管 |
| 改动范围 | 小 | 最小 |

---

## 7. 最终推荐

**推荐方案2**（取消 timer + 记录状态，不触发 retry）。

理由：
1. 改动范围小，风险低
2. 符合方向B的设计思路
3. 保留了其他场景的 retry 逻辑
4. 行为可预期：确认失败时取消 timer，让 autonext_guard 或手动触发接管

如果对 autonext_guard 有信心，可以选择方案3（完全不干预）。

---

## 8. 代码行数

**方案2**：
- 删除 1 行：`await self._handle_play_failure(...)`
- 添加约 12 行：记录失败状态的逻辑
- 净增约 11 行

**方案3**：
- 删除 2 行：`await self.cancel_next_timer()` + `await self._handle_play_failure(...)`
- 添加约 10 行：记录失败状态的逻辑
- 净增约 8 行