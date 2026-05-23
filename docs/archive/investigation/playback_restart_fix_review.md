# 自动切歌后重头开始问题修复方案审查报告

**日期**: 2026-05-11  
**审查文件**: 
- `docs/playback_restart_issue_investigation.md`
- `docs/playback_restart_fix_design.md`
- `xiaomusic/device_player.py`

---

## 审查结论

**结论：需修改**

方案2存在一个关键风险：`cancel_next_timer()` 后依赖 autonext_guard 接管时，可能对正在正常播放的歌曲进行误判切歌。建议改用方案3（不取消 timer）来规避此风险。

---

## 1. 根因分析审查

### 1.1 时序图和代码路径分析

**审查结果：准确**

调查报告中的时序图正确描述了问题发生的完整路径：

```
T=0:     _play_next() → _playmusic(confirm_start_in_background=True)
T=0:     sid = _bump_play_session() → session=1
T=0-0.5: stop current song
T=0.5-1: play next song (send command)
T=1.0:   is_playing = True
         _mark_play_started() → set timer
         _schedule_playback_confirmation() → schedule background task
T=1.0-1.2: _confirm_playback_started() sleeps
T=1.2:   get_if_xiaoai_is_playing() → player_get_status
         ★ 小米API返回 status=0 (切换延迟)
T=1.2:   started=False
T=1.2:   _handle_play_failure() → _retry_next() scheduled
T=2.2:   _retry_next() executes
         ├─ sid == _play_session_id ✓
         ├─ is_playing == True ✓ (已在_playmusic中设置为True)
         └─ ★ 不满足保护条件，_play_next() 被调用
```

代码路径分析正确，`_background_confirm_playback_started` → `started=False` → `_handle_play_failure` → `_retry_next` 的调用链符合代码事实。

### 1.2 保护机制失效原因分析

**审查结果：准确**

报告正确识别了两个关键保护失效点：

1. **session 保护失效**：`sid == _play_session_id`（都是1），保护失效
2. **is_playing 保护失效**：`is_playing == True`（已在 `_playmusic` 中设置为True），保护失效

### 1.3 根因确认

**结论：根因分析准确**

根因是小米 API `player_get_status` 在歌曲切换时返回不准确的 `status`，导致：
1. `_confirm_playback_started` 在歌曲实际已开始播放后仍返回 `False`
2. `_handle_play_failure` 被错误触发
3. `_retry_next` 中的保护无法阻止重新播放

---

## 2. 方案设计审查

### 2.1 方案对比回顾

| 维度 | 方案1 | 方案2 | 方案3 |
|------|-------|-------|-------|
| **改动范围** | 中等（改签名 + 2个调用点） | 小（只改1个函数） | 小（只改1个函数） |
| **风险** | 中等（需要修改多处） | **存在关键风险** | 低（不干预播放） |
| **效果** | 好（明确区分场景） | 存在隐患 | 好（完全依赖 timer） |
| **代码复杂度** | 高（需要参数传递） | 低（直接改分支） | 最低（只删除代码） |

### 2.2 方案2的关键风险：autonext_guard 误判

**问题描述**：方案2取消 timer 后依赖 `autonext_guard` 接管，但 `autonext_guard` 的状态检查存在误判风险。

**autonext_guard 代码逻辑**（L282-308）：

```python
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
    await self._play_next()
```

**问题场景**：

| 步骤 | 场景描述 |
|------|----------|
| 1 | `_confirm_playback_started` 返回 `started=False`（误判，歌曲实际已在播放） |
| 2 | `cancel_next_timer()` 执行，timer 被取消 |
| 3 | `get_offset_duration()` 检测到 `overdue_without_timer = (self._next_timer is None and offset >= duration + 15.0)` |
| 4 | `autonext_guard` 被调度 |
| 5 | `still_playing = await self.get_if_xiaoai_is_playing()` 返回 `False`（小米 API 状态延迟） |
| 6 | `if still_playing: return` 不满足，**触发切歌** |

**结论**：取消 timer 后，如果小米 API 状态持续不可靠，autonext_guard 可能对正在播放的歌曲误判切歌。

### 2.3 方案3的优势

方案3不调用 `cancel_next_timer()`，保留 timer，让歌曲正常播放完成：

- **优点**：不依赖 autonext_guard 的状态检查，消除误判风险
- **行为**：取消 timer 后歌曲继续播放直到自然结束或被 autonext_guard 接管
- **风险**：如果歌曲实际未播放成功，会一直播放（直到 autonext_guard 超时触发）

### 2.4 边界情况审查

#### 场景1：Jellyfin fallback 成功后又失败

**审查结果：处理正确**

```python
if proxy_url:  # fallback 成功
    await self._mark_play_started(...)  # 设置 timer
    return
```

fallback 成功时调用 `_mark_play_started` 设置新 timer，行为不变。

fallback 失败时走失败分支，方案2/3行为一致。

#### 场景2：手动触发场景

**审查结果：不受影响**

手动触发时 `confirm_start_in_background=False`，走 `_playmusic` 同步路径（L894-902），不在方案2修改范围内。

#### 场景3：player_play 失败场景

**审查结果：不受影响**

路径 C（L845-858）在 `_playmusic` 中处理，`group_player_play` 全部失败时调用 `_handle_play_failure`，不在方案2修改范围内。

---

## 3. 代码质量审查

### 3.1 失败状态记录逻辑

**审查结果：代码逻辑正确**

方案2的失败状态记录代码：

```python
self._play_failed_cnt += 1
self._play_fail_last_reason = "play_start_not_confirmed"
if self._play_fail_first_ts <= 0:
    self._play_fail_first_ts = time.time()
```

与 `_handle_play_failure` 的逻辑对比：

```python
now = time.time()
if self._play_fail_first_ts <= 0:
    self._play_fail_first_ts = now
self._play_fail_last_reason = reason
self._play_failed_cnt += 1
```

**逻辑一致**，三处状态变量（`_play_failed_cnt`、`_play_fail_last_reason`、`_play_fail_first_ts`）都得到正确更新。唯一细微差异是方案2没有预先保存 `now`，但不影响逻辑正确性。

### 3.2 代码语法

**审查结果：无语法问题**

方案2的代码片段是标准 Python 语法，无问题。

### 3.3 日志信息

**建议优化**：日志信息可以更明确地区分是「确认失败」还是「Jellyfin fallback 失败」：

```python
# 当前
"play_start_not_confirmed (auto_next) cnt=%d name=%s"

# 建议
"play_start_not_confirmed (auto_next) no_fallback cnt=%d name=%s"
```

这样可以更方便地通过日志定位问题类型。

---

## 4. 测试建议审查

### 4.1 现有测试覆盖情况

现有测试文件：
- `tests/test_play_retry_backoff.py` - 测试 retry 和 degrade 机制
- `tests/test_play_session_timer.py` - 测试 timer 和 autonext_guard

**关键测试**：`test_background_confirmation_failure_cancels_timer_and_retries`（L403-423）测试了失败时取消 timer 和触发 retry 的行为。方案2修改后，此测试预期行为会变化。

### 4.2 需要补充的测试用例

#### 测试1：自动切歌确认失败不触发 retry（方案2/3）

```python
@pytest.mark.asyncio
async def test_auto_next_confirm_failure_does_not_trigger_retry():
    """自动切歌确认失败时不应触发 _retry_next"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5

    called = {"cancel": 0, "retry": 0}
    original_is_playing = True

    async def _confirm_playback_started(name, sid, **kwargs):
        return False

    async def _cancel_next_timer():
        called["cancel"] += 1

    async def _play_next():
        called["retry"] += 1

    d._confirm_playback_started = _confirm_playback_started
    d.cancel_next_timer = _cancel_next_timer
    d._play_next = _play_next
    d._is_jellyfin_auto_candidate = lambda **kwargs: False

    await d._background_confirm_playback_started(...)

    assert called["cancel"] == 1
    assert called["retry"] == 0  # 不应触发 retry
```

#### 测试2：方案3保留 timer（方案3独有）

```python
@pytest.mark.asyncio
async def test_auto_next_confirm_failure_preserves_timer():
    """确认失败时应保留 timer（方案3）"""
    d = _build_device_for_timer_tests()
    d._play_session_id = 5
    d._next_timer = asyncio.create_task(asyncio.sleep(999))

    async def _confirm_playback_started(name, sid, **kwargs):
        return False

    d._confirm_playback_started = _confirm_playback_started
    d._is_jellyfin_auto_candidate = lambda **kwargs: False

    await d._background_confirm_playback_started(...)

    # timer 应保留
    assert d._next_timer is not None
```

#### 测试3：autonext_guard 状态检查不可靠时的行为

```python
@pytest.mark.asyncio
async def test_autonext_guard_respects_still_playing_check():
    """autonext_guard 应正确处理 still_playing 检查"""
    d = _build_device_for_timer_tests()
    d._duration = 10.0
    d._start_time = time.time() - 26.0  # offset = 26.0 > duration + 15.0
    d._paused_time = 0.0
    d._next_timer = None
    d._last_cmd = "play"
    d.is_playing = True

    still_playing_calls = []

    async def _get_if_xiaoai_is_playing():
        still_playing_calls.append("called")
        return True  # 歌曲实际在播放

    d.get_if_xiaoai_is_playing = _get_if_xiaoai_is_playing

    d.get_offset_duration()
    await asyncio.sleep(0)

    # still_playing=True 时不应切歌
    assert d._next_called == 0
```

#### 测试4：Jellyfin fallback 场景

```python
@pytest.mark.asyncio
async def test_jellyfin_fallback_success_sets_new_timer():
    """Jellyfin fallback 成功后应设置新 timer"""
    # 模拟 fallback 成功的场景
    ...
```

### 4.3 现有测试修改

`test_background_confirmation_failure_cancels_timer_and_retries` 需要根据方案2/3修改断言：
- 方案2：断言 `failure == 0`，`cancel == 1`
- 方案3：断言 `failure == 0`，`cancel == 0`（timer 保留）

---

## 5. 额外发现的潜在问题

### 5.1 autonext_guard 的状态检查可靠性

`autonext_guard` 中调用 `get_if_xiaoai_is_playing()` 检查 `still_playing`，如果返回 `False` 则触发切歌。但在问题场景中，小米 API 本身就不可靠，这个检查无法保证准确。

**建议**：在 `autonext_guard` 中增加连续探测机制，比如连续探测 2~3 次，每次间隔 0.5s，只有所有探测都返回 `False` 才触发切歌。

```python
async def _guard_autonext():
    # 连续探测确认真的没在播放
    all_false = True
    for _ in range(3):
        still_playing = await self.get_if_xiaoai_is_playing()
        if still_playing:
            all_false = False
            break
        await asyncio.sleep(0.5)
    
    if not all_false:
        return
    ...
```

**严重程度**：中等。这是一个独立的潜在问题，不在方案2修改范围内，但如果采用方案2，此问题会加剧误判风险。

### 5.2 修复方案对 autonext_guard 的影响

| 方案 | timer 状态 | autonext_guard 依赖度 | 状态检查风险 |
|------|-----------|----------------------|-------------|
| 方案1 | 不变 | 低 | 低 |
| 方案2 | 取消 | 高 | **高** |
| 方案3 | 保留 | 低 | 低 |

---

## 6. 总结与建议

### 6.1 审查总结

1. **根因分析**：准确
2. **方案2风险**：存在关键风险（autonext_guard 误判）
3. **方案3优势**：更可靠，不依赖 autonext_guard 状态检查
4. **代码质量**：良好
5. **测试覆盖**：需要补充

### 6.2 建议

1. **采用方案3**：不调用 `cancel_next_timer()`，让歌曲正常播放完成，规避 autonext_guard 误判风险

2. **增加 autonext_guard 的状态检查稳定性**：连续探测 2~3 次再决定是否切歌

3. **补充测试用例**：覆盖自动切歌确认失败、Jellyfin fallback、autonext_guard 状态检查等场景

4. **修改现有测试**：`test_background_confirmation_failure_cancels_timer_and_retries` 需要根据最终方案调整断言

### 6.3 后续工作

如果采用方案3，需要：
1. 修改 `_background_confirm_playback_started` 中的 `started=False` 分支
2. 不调用 `cancel_next_timer()`
3. 仅记录失败状态，不触发 retry
4. 补充测试用例
5. 考虑增强 autonext_guard 的状态检查可靠性

---

**审查人**：影子澪  
**审查日期**：2026-05-11