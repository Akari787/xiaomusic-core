# xiaomusic 实机重播问题实时调查报告

**调查时间**：2026-05-12 20:27 (JST)  
**调查对象**：测试服务器 `192.168.7.178`，容器路径 `/app/xiaomusic/device_player.py`  
**日志来源**：`/app/conf/xiaomusic.log.txt`

---

## 1. 修复代码确认

```bash
$ grep -n 'play_start_not_confirmed (auto_next) no_retry' device_player.py
1224:                "play_start_not_confirmed (auto_next) no_retry cnt=%d name=%s",
```

**确认结果**：修复代码已正确部署在 L1224，日志中也出现了 `play_start_not_confirmed (auto_next) no_retry` 字样。

---

## 2. 日志时序分析

### 2.1 案例 A：シクス 重播（L148, L166, L178-211）

```
19:16:25  [timer_fired] → _play_next → シクス-ｎａｍｅｌｅｓｓ，とあ
19:16:25  [playmusic_begin t=1778584585.527]
19:16:27  【シクス】已经开始播放了         ← _mark_play_started 设置定时器
19:16:29  timer_start(session_id=2, delay_sec=196.838)  ← 定时器已设置
19:16:29  play_start_confirmation_attempted (retries=0, delay_ms=800)
19:16:32  play_start_confirmation_result(started=false background=true)
19:16:32  play_start_not_confirmed (auto_next) no_retry cnt=1 name=シクス   ← 修复生效，no_retry
19:16:56  [timer_fired] → _play_next → オレンジ-96猫   ← 重播未发生，正常切歌
19:17:14  【オレンジ】已经开始播放了
```

**分析**：修复生效，no_retry 日志出现后没有触发重播，定时器正常触发切到下一首。

### 2.2 案例 B：OVER LIT 重播（L232-278）

```
19:21:17  [timer_fired] → _play_next → OVER LIT-書店太郎
19:21:17  [playmusic_begin t=1778584877.606]
19:21:20  【OVER LIT】已经开始播放了        ← _mark_play_started
19:21:21  timer_start(session_id=4, delay_sec=286.043)
19:21:21  play_start_confirmation_attempted (retries=0, delay_ms=800)
19:21:49  force_stop_xiaoai_fast player_stop   ← 33秒后才执行 stop
19:21:52  status=2 (停止)                      ← 设备已停止
19:21:53  play_start_confirmation_result(started=false background=true)
19:21:53  play_start_not_confirmed (auto_next) no_retry cnt=1 name=OVER LIT  ← 修复生效
```

**分析**：修复生效，但设备在 33 秒后已停止（status=2），歌曲已停止播放。

### 2.3 案例 C：ウミユリ 海底譚（L300-337）—— 成功案例

```
19:26:07  [timer_fired] → _play_next → ウミユリ海底譚-まじ娘
19:26:07  [playmusic_begin]
19:26:08  group_stop_overlap_state stop_done=true   ← overlap stop 成功完成
19:26:10  timer_start(session_id=5, delay_sec=231.932)
19:26:10  play_start_confirmation_attempted (retries=0, delay_ms=800)
19:26:11  status_playing_observed t=1778585171.143   ← 播放确认成功
19:26:13  play_start_confirmation_result(started=true background=true)  ← 成功
```

**分析**：对比案例 B，C 的 `stop_done=true`，说明 `group_stop_overlap_state` 已正确完成停止操作，B 中的 `stop_done=false` 导致设备状态异常。

---

## 3. 根因分析

### 核心发现：修复代码本身正确，但存在另一个未覆盖的重播路径

修复代码（`no_retry`）位于 `_background_confirm_playback_started` 中，当确认失败时：
- ✅ 不调用 `_handle_play_failure`（不再重试）
- ✅ 不取消 timer（让歌曲自然播放）
- ❌ **但没有处理"歌曲已经标记为播放但实际设备已停止"的矛盾状态**

### 重播的真实触发路径

```
1. timer_fired → _play_next → 新歌曲开始播放
2. _mark_play_started → 设置 is_playing=True → 设置 timer → 安排确认任务
3. 确认任务发现 started=false（设备已停止）
4. 修复代码：不重试，不取消 timer
5. ❗ 但设备实际已停止，歌曲没有在播放
6. 用户手动点击下一首，或 autonext_guard 在超时时触发 _play_next
7. → 歌曲从头开始播放（重播）
```

### 为什么会有重播？

根据日志分析，有以下两种情况：

**情况 1：`confirm_start_in_background=True` 路径（正常自动切歌）**

```
19:16:27 【シクス】已经开始播放了       ← 直接在 API 返回后调用
19:16:32 play_start_not_confirmed       ← 确认失败，修复生效
19:16:56 timer_fired → 重播下一首       ← 没有重播，正常切歌
```

这种路径下，即使确认失败，歌曲已在播放，不会有重播。

**情况 2：`started=false` 确认失败后设备实际停止**

```
19:21:20 【OVER LIT】已经开始播放了      ← 标记为播放
19:21:49 force_stop_xiaoai_fast player_stop  ← 33秒后停止设备
19:21:53 play_start_not_confirmed       ← 确认失败，设备已停止
→ 此时 is_playing=True，但设备已停止
```

这种状态下：
- `is_playing=True` 保持（没有重置）
- timer 在 286 秒后触发 → `_play_next`
- **但此时用户可能手动操作、或 autonext_guard 检测到超时**

### autonext_guard 的潜在影响

根据代码 L282-308：

```python
should_check_autonext = overdue_without_timer or near_end_with_timer
# overdue_without_timer: _next_timer is None and offset >= duration + 15.0
# near_end_with_timer: _next_timer is not None and offset >= max(duration - 1.0, duration * 0.9)
```

**关键问题**：`autonext_guard` 检查的是 `get_if_xiaoai_is_playing()`，但这个状态可能和实际不一致。

### Jellyfin proxy fallback 的触发条件

从日志看，两次 `play_start_not_confirmed` 都没有触发 proxy fallback：
- `jellyfin_proxy_mode='off'`（配置关闭）
- proxy fallback 需要 jellyfin_auto_candidate=True 且 proxy_url 非空

---

## 4. 触发条件总结

| 条件 | 说明 |
|------|------|
| 歌曲开始播放 | `is_playing=True`，timer 设置 |
| 确认失败 | `started=false background=true` |
| 修复生效 | 不调用 `_handle_play_failure`，不取消 timer |
| 设备实际停止 | `status=2`，确认失败后 33 秒才 stop |
| 重播发生 | 需要额外的触发条件（用户手动/下一定时器/autonext_guard） |

---

## 5. 与修复代码的关系

### 修复代码确实生效

- `play_start_not_confirmed (auto_next) no_retry` 日志出现在两次确认失败中
- 不再触发 `_handle_play_failure` → `_retry_next`
- timer 没有被取消

### 但修复范围不完整

修复只处理了"不重试"的逻辑，但**没有处理以下情况**：

1. **`is_playing=True` 但设备实际停止**：没有重置 `is_playing` 状态
2. **没有通知上层歌曲实际未播放**：上层以为歌曲在播放
3. **没有清理或标记错误状态**：`_play_failed_cnt` 增加但没有后续处理

---

## 6. 具体改动建议

### 建议 1：在确认失败时重置 `is_playing`

```python
# 在 _background_confirm_playback_started 的 "no_retry" 块中（L1218-1227）
# 当前：
self.log.info("play_start_not_confirmed (auto_next) no_retry cnt=%d name=%s", ...)
return

# 建议改为：
self.log.info("play_start_not_confirmed (auto_next) no_retry cnt=%d name=%s", ...)
# 如果 proxy fallback 也失败，重置状态
self.is_playing = False
self._start_time = 0
return
```

### 建议 2：增加诊断日志

```python
# 在确认失败时，记录设备的实际状态
status = await self.get_if_xiaoai_is_playing()
self.log.warning(
    "play_start_not_confirmed device_actually_playing=%s is_playing=%s",
    status,
    self.is_playing,
)
```

### 建议 3：处理 `is_playing=True` 但设备停止的矛盾

在 `_background_confirm_playback_started` 中：

```python
if started is False:
    actual_playing = await self.get_if_xiaoai_is_playing()
    if actual_playing:
        # 设备在播放，可能是状态检测延迟，重试
        pass
    else:
        # 设备确实停止
        self.is_playing = False
        self._start_time = 0
        # 不取消 timer，让定时器触发正常的下一首切换
```

---

## 7. 待进一步确认

1. **autonext_guard 是否实际触发了重播**：需要添加日志确认
2. **用户操作是否触发了重播**：需要了解用户的操作时序
3. **`stop_done=false` 的原因**：为什么 overlap stop 没有成功完成

---

## 8. 结论

- 修复代码（`no_retry`）**已正确部署并生效**
- 但修复范围**不完整**，只处理了"不重试"，没有处理"歌曲标记为播放但实际停止"的矛盾状态
- 真实重播原因：`is_playing=True` + 设备停止 + 额外的播放触发（timer/autonext_guard/用户操作）
- 建议：在确认失败时重置 `is_playing` 状态，并在日志中记录设备的实际播放状态

---

**调查结论**：修复方向正确，但需要扩展处理范围，增加 `is_playing` 状态的重置逻辑。
