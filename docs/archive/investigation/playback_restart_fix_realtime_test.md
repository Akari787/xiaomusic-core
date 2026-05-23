# 自动切歌重头开始问题修复 - 实机测试报告

**日期**: 2026-05-12  
**测试类型**: 测试服务器实机验收  
**待验证版本**: 最新 commit (f323899)

---

## 1. 测试目标

验证修复：自动切歌确认失败时，不再触发 retry 导致歌曲重头开始。

### 验证逻辑

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| `started=False`（非 Jellyfin 源） | 取消 timer + 调用 `_handle_play_failure`（触发 `_retry_next`） | 记录失败状态，**不触发 retry** |
| `started=False`（Jellyfin fallback 成功） | 正常播放（不变） | 正常播放（不变） |

---

## 2. 代码修复点

**文件**: `xiaomusic/device_player.py`  
**位置**: `_background_confirm_playback_started` 函数，L1214-1225

### 修复前 (L1214-1225)

```python
if started is False:
    # ... jellyfin fallback ...
    if proxy_url:
        await self._mark_play_started(...)
        return
    await self.cancel_next_timer()
    await self._handle_play_failure(
        name=name, sid=sid, reason="play_start_not_confirmed"
    )
    return
```

### 修复后

```python
if started is False:
    # ... jellyfin fallback ...
    if proxy_url:
        await self._mark_play_started(...)
        return

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
    return
```

### 关键变更

1. **删除了 `await self.cancel_next_timer()`** - 不再取消 timer
2. **删除了 `await self._handle_play_failure(...)`** - 不再触发 retry
3. **新增失败状态记录** - 记录失败日志和计数器
4. **日志关键字**: `play_start_not_confirmed (auto_next) no_retry`

---

## 3. 前置检查

### 3.1 确认测试服务器可访问

```bash
# SSH 登录测试服务器
ssh root@192.168.7.178

# 检查容器状态
cd /root/xiaomusic-core
docker compose -f docker-compose.hardened.yml ps
```

### 3.2 确认服务运行

```bash
# 检查服务是否正常运行
curl -fsS http://127.0.0.1:58090/getversion
```

期望返回类似：
```json
{"version": "1.x.x", ...}
```

### 3.3 确认代码版本

```bash
# 进入容器
docker exec -it xiaomusic-core bash

# 检查代码版本
cd /app
git log --oneline -3

# 检查修复是否已应用
grep -n "play_start_not_confirmed (auto_next) no_retry" xiaomusic/device_player.py
```

期望输出包含 `play_start_not_confirmed (auto_next) no_retry`

### 3.4 如果代码未更新

```bash
# 在服务器上执行
cd /root/xiaomusic-core

# 方式 A: git pull 拉取最新代码
git pull origin main

# 方式 B: 全量同步（如果 git pull 不可用）
# 从本机复制最新代码到服务器
scp -r ./xiaomusic root@192.168.7.178:/root/xiaomusic-core/

# 重建容器
docker compose -f docker-compose.hardened.yml down
docker compose -f docker-compose.hardened.yml up -d --build
```

---

## 4. 测试步骤

### 4.1 准备：打开实时日志

```bash
# 在服务器上打开实时日志
docker exec -it xiaomusic-core tail -f /app/conf/xiaomusic.log.txt
```

或使用 grep 过滤关键字：

```bash
docker exec -it xiaomusic-core tail -f /app/conf/xiaomusic.log.txt | grep -E "timer_start|play_start_confirmation|play_start_not_confirmed|timer_discard|session_id"
```

### 4.2 触发播放

通过 WebUI 或 API 触发播放：

**方式 A: WebUI**
- 打开 `http://192.168.7.178:58090`
- 选择设备
- 点击播放按钮

**方式 B: API**
```bash
# 获取设备列表
curl -s "http://127.0.0.1:58090/getsetting?need_device_list=true" | jq

# 获取播放列表
curl -s "http://127.0.0.1:58090/api/v1/playlist" | jq

# 触发播放（替换 <DID> 和歌曲ID）
curl -s "http://127.0.0.1:58090/api/v1/play?did=<DID>&media_id=<SONG_ID>"
```

### 4.3 观察日志（自动切歌触发）

等待当前歌曲播放接近结束时（可设置较小的 `delay_sec` 加速测试），观察以下日志顺序：

#### 预期日志序列（修复后）

```
[Step 1] timer_fired
         ↓
[Step 2] group_force_stop_xiaoai fast:True
         ↓
[Step 3] after_group_force_stop_xiaoai dt=X.XXX
         ↓
[Step 4] after_group_player_play dt=X.XXX
         ↓
[Step 5] 【歌曲名】已经开始播放了
         ↓
[Step 6] play_start_confirmation_result(... started=False background=true)
         ↓
[Step 7] play_start_not_confirmed (auto_next) no_retry cnt=X name=歌曲名
         ↓
[Step 8] （新歌曲继续播放，不再触发 retry）
```

#### 关键验证点

| 验证点 | 预期日志 | 状态 |
|--------|----------|------|
| 确认失败记录 | `play_start_not_confirmed (auto_next) no_retry` | ⬜ |
| 不再触发 retry | 不出现 `timer_discard_due_to_sid_mismatch` | ⬜ |
| 歌曲继续播放 | 新歌曲从头正常播放，无中断 | ⬜ |
| timer 保留 | 不出现 `cancel_next_timer`（关键日志） | ⬜ |

### 4.4 重复测试 3-5 次

重复步骤 4.2-4.3，记录每次测试结果。

---

## 5. 测试结果记录表

| 测试次数 | 时间 | 歌曲 | 确认失败 | 日志关键字 | 歌曲重头? | 备注 |
|----------|------|------|----------|------------|-----------|------|
| 1 | 2026-05-12 | | ⬜ | | ⬜ | **代码已同步，容器已重建，服务运行中 v1.1.1** |
| 2 | | | ⬜/❌ | | ⬜/❌ | |
| 3 | | | ⬜/❌ | | ⬜/❌ | |
| 4 | | | ⬜/❌ | | ⬜/❌ | |
| 5 | | | ⬜/❌ | | ⬜/❌ | |

### 状态说明
- ⬜ = 符合预期（修复生效）
- ❌ = 不符合预期（问题仍存在）

---

## 6. 异常情况记录

如果测试中发现问题，请记录：

### 6.1 日志片段

```
[粘贴相关日志]
```

### 6.2 问题描述

[描述观察到的异常]

### 6.3 可能原因

[分析可能的原因]

---

## 7. 测试完成确认

| 检查项 | 状态 |
|--------|------|
| 代码版本已更新 | ✅ |
| 服务正常运行 | ✅ |
| 修复代码已部署 | ✅ |
| 完成 3-5 轮测试 | ⬜ |
| 日志符合预期 | ⬜ |
| 无重头开始问题 | ⬜ |

---

## 8. 测试人员签名

- **测试人**: _______________
- **测试日期**: _______________
- **结论**: ⬜ 通过 / ❌ 未通过

---

## 附录 A: 加速测试方法

如果不想等待歌曲自然播放完，可以：

### 方法 1: 设置极小的 delay_sec

```bash
# 通过 API 临时修改 delay_sec
curl -s "http://127.0.0.1:58090/api/v1/config" -X PUT \
  -H "Content-Type: application/json" \
  -d '{"delay_sec": -200}'

# 测试完成后恢复
curl -s "http://127.0.0.1:58090/api/v1/config" -X PUT \
  -H "Content-Type: application/json" \
  -d '{"delay_sec": 0}'
```

### 方法 2: 手动触发自动切歌

```bash
# 触发自动切歌（需要获取当前播放的 sid）
# 观察日志是否出现预期的 no_retry 日志
```

---

## 附录 B: 相关日志关键字参考

| 关键字 | 含义 |
|--------|------|
| `timer_fired` | 定时器触发，开始切歌 |
| `group_force_stop_xiaoai` | 停止当前歌曲 |
| `after_group_force_stop_xiaoai` | 停止完成 |
| `after_group_player_play` | 播放命令执行完成 |
| `已经开始播放了` | 新歌曲开始播放 |
| `play_start_confirmation_result` | 确认播放结果 |
| `started=False` | 确认失败 |
| `play_start_not_confirmed (auto_next) no_retry` | **修复后的关键日志** |
| `timer_discard_due_to_sid_mismatch` | session 不匹配丢弃（修复后不应出现） |
| `cancel_next_timer` | 取消定时器（修复后不应在确认失败时出现） |
| `_retry_next` | 重试切歌（修复后不应在确认失败时触发） |