# 认证恢复 singleflight 规范

版本：v1.0
状态：行为冻结文档
最后更新：2026-03-28
适用范围：`xiaomusic/auth.py` 内 recovery leader/follower/backoff 并发控制机制

---

## 1. 文档目的

本文档约束认证恢复期间的并发行为，用于：

- 防止多个请求同时执行 clear / rebuild / redirect
- 为后续 24h 掉线的实机验收提供明确日志判定口径
- 约束后续修改不要重新引入并发 clear / rebuild / redirect

本文档描述的是"当前恢复窗口并发隔离规则"，不是未来重构提案。

---

## 2. 背景与问题定义

当前自动恢复失败的主怀疑点之一是恢复窗口并发打穿：

- 手动恢复路径通常是串行、单次、独占恢复窗口
- 自动恢复路径可能由 keepalive / mina_call / player 等多个请求同时触发
- 多个请求同时进入 `ensure_logged_in(prefer_refresh=True)` 会导致：
  - 多次 clear short session
  - 多次 rebuild_services
  - 多次 redirect / relogin
  - auth.json 写入竞争

当前 singleflight 的目标不是消灭所有失败，而是确保同一时间只有一条恢复主链执行。

---

## 3. 术语定义

| 术语 | 定义 |
|------|------|
| recovery window | 从 auth error 检测到恢复完成（或失败）的时间窗口 |
| leader | 成功获取恢复锁的请求，独占执行 clear+rebuild 主链 |
| follower | 在已有 leader 执行期间到达的请求，不得自行 clear/rebuild |
| backoff | leader 恢复失败后的保护窗口，阻断下一波立即并发重入 |
| clear+rebuild | clear short session + ensure_logged_in(prefer_refresh=True) 的组合 |
| recovery attempt | 一次完整的恢复尝试（从获取 leader 到释放 leader） |
| ctx | 触发恢复的上下文标识（如 `mina:device_list:keepalive`） |
| auth error trigger | 触发恢复链的认证错误事件 |

---

## 4. 当前实现目标

1. 同一时间只允许一个 leader 进入 clear+rebuild 或 strong-evidence 升级后的恢复主链
2. follower 不得自行 clear short session
3. follower 不得自行触发 `ensure_logged_in(prefer_refresh=True)`
4. 失败后进入短 backoff，避免立即重入
5. singleflight 只解决恢复窗口互斥，不保证 Xiaomi 云端一定成功

---

## 5. leader / follower 行为规范

### 5.1 `_try_acquire_recovery_leader` 语义

```
if backoff_active:
    → 返回 (False, "backoff_active"), 角色 = blocked

async with _recovery_lock:
    if _recovery_inflight:
        → 返回 (False, "follower"), 角色 = follower
    else:
        → 设置 _recovery_inflight = True
        → 返回 (True, "leader"), 角色 = leader
```

### 5.2 leader 可以做什么

| 行为 | 说明 |
|------|------|
| clear short session | 清除 runtime 注入态和 auth.json 中的 short session 字段 |
| 进入 `ensure_logged_in(prefer_refresh=True)` | 执行 clear 后的 rebuild 主链 |
| 执行恢复主链 | persistent-auth relogin → runtime rebind → verify |
| 释放 leader | 调用 `_release_recovery_leader`，无论成功失败 |

### 5.3 follower 不可以做什么

| 行为 | 说明 |
|------|------|
| 不 clear short session | follower 无权修改 auth.json |
| 不 rebuild | follower 不进入 `ensure_logged_in(prefer_refresh=True)` |
| 不发起自己的 redirect/relogin 主链 | follower 依赖 leader 的恢复结果 |

**边界提醒**：follower 不 clear、不 rebuild 是当前代码语义。但"是否所有并发现场都已被线上充分证明"仍需实机闭环验证。文档中的 PASS/FAIL 口径服务于后续验收，不等于当前线上结论。

### 5.4 follower 允许做什么

| 行为 | 说明 |
|------|------|
| join existing recovery | 记录日志 `auth_recovery_singleflight: follower join_existing_recovery` |
| 短暂等待后重试原请求 | `await asyncio.sleep(0.5)` 后重试原始业务调用 |
| 依赖 leader 结果 | 如果 leader 恢复成功，follower 重试可能成功 |

### 5.5 blocked 行为

| 行为 | 说明 |
|------|------|
| 不进入恢复链 | backoff 期间禁止任何恢复尝试 |
| 直接抛出错误 | 让调用者处理，不重试 |

---

## 6. backoff 规范

### 6.1 进入条件

leader 恢复失败后：

```python
if result != "ok":
    self._recovery_backoff_until_ts = now + self._recovery_backoff_sec
```

`_recovery_backoff_sec` 默认值：10 秒

### 6.2 目标

阻断下一波立即并发重入，避免：
- 恢复失败后立即重试
- 多个请求在失败后同时重试
- 对 Xiaomi 云端造成压力

### 6.3 backoff 激活期间的行为

| 角色 | 行为 |
|------|------|
| blocked | 返回 `(False, "backoff_active")`，不进入恢复链 |
| 日志 | `auth_recovery_singleflight: role=blocked action=backoff_skip` |

### 6.4 backoff 不是什么

- backoff 不是健康恢复机制
- backoff 只是失败后的保护窗口
- backoff 不解决云端风控问题

---

## 7. singleflight 与恢复链关系

### 7.1 singleflight 在恢复链中的位置

```
auth_call 捕获 auth error
    ↓
判断 should_clear_short_session
    ├─ first_suspect → non-destructive recovery（不争夺 leader）
    │       ├─ Phase A success → 重试原请求
    │       └─ strong evidence → 升级 → 争夺 leader → clear+rebuild
    │
    └─ consecutive error → 争夺 leader → clear+rebuild
            ├─ leader → 执行 clear+rebuild
            └─ follower → 等待 leader 完成后重试
```

### 7.2 关键关系

| 恢复阶段 | 是否争夺 leader | 说明 |
|----------|-----------------|------|
| non-destructive recovery (Phase A/B) | 否 | 不 clear，不 rebuild，不争夺 leader |
| strong evidence 升级后 | 是 | 需要 clear+rebuild，争夺 leader |
| consecutive error 直接 clear | 是 | 需要 clear+rebuild，争夺 leader |

### 7.3 必须写清的关系

1. **首次 auth error 下的 non-destructive recovery 不等于 singleflight leader**：Phase A/B 不修改状态，不需要互斥
2. **strong evidence 升级后，才可能争夺 leader 并进入 clear+rebuild**：这是互斥控制的入口点
3. **连续 auth error clear 路径也会争夺 leader**：consecutive error 也需要互斥控制

---

## 8. 关键日志事件与验收口径

### 8.1 必须关注的日志事件

| 事件 | 关键字段 | 用途 |
|------|----------|------|
| `auth_recovery_singleflight` | `role`, `action`, `ctx`, `reason` | 判断 leader/follower/backoff |
| `auth_short_session_clear_decision` | `clear_executed`, `decision_reason` | 判断是否执行了 clear |
| `auth_non_destructive_recovery` | `phase`, `strong_invalidation_evidence` | 判断 Phase A/B 结果 |
| `auth_cookie_rebuild` | `result`, `used_path` | 判断 persistent-auth 是否成功 |

### 8.2 leader/follower/backoff 典型日志链

**正常 leader 执行**：

```
auth_recovery_singleflight: role=leader action=start ctx=mina:device_list:keepalive
auth_short_session_clear_decision: clear_executed=true decision_reason=consecutive_auth_error_same_ctx
auth_recovery_singleflight: role=leader action=finish result=ok
```

**follower 等待 leader**：

```
auth_recovery_singleflight: role=follower action=join_existing_recovery leader_ctx=mina:device_list:keepalive
```

**backoff 阻断**：

```
auth_recovery_singleflight: role=blocked action=backoff_skip reason="backoff active, remaining=8.2s"
```

### 8.3 24h 掉线 singleflight PASS 的最小证据链

1. 出现一个 `role=leader action=start`
2. 同一时间窗口出现 `role=follower action=join_existing_recovery`
3. 同一恢复窗口内，日志显示只有一条 clear+rebuild / redirect 主链被执行
4. leader 出现 `action=finish result=ok` 或 `result=failed`

注意：这是当前实现目标和验收目标。当前代码已引入 leader/follower/backoff 机制来逼近这一目标。是否在线上真实收敛为单条主链，仍需靠下一次实机掉线验收确认。

### 8.4 PARTIAL PASS 判断口径

- leader 执行了 clear+rebuild，但最终仍失败
- follower 出现，但 leader 恢复失败导致 backoff

### 8.5 EVIDENCE INSUFFICIENT 判断口径

- 现场没有形成足够并发，无法验证 follower / backoff / 单 leader 是否真实生效
- 无 follower 出现的单请求恢复：可视为"恢复链跑通，但 singleflight 未被充分触发验证"
- 这不是失败，也不是通过，而是证据不足

### 8.6 FAIL 判断口径

- 出现多个 leader start（同一恢复窗口内）
- follower 自行执行了 clear 或 rebuild
- backoff 期间仍有请求进入恢复链
- 无 singleflight 日志，但出现多次并发 clear

---

## 9. 当前实现边界

1. **当前代码已经落地 leader/follower/backoff 状态，但尚未完成足够的实机闭环验证**
2. **不能把"已部署"写成"已在线上完全证明"**
3. **即使 singleflight 生效，也不能排除 redirect 401 仍来自云端风控或 relogin 路径脆弱性**
4. **singleflight 解决的是并发打穿问题，不是所有认证失败问题**

---

## 10. 对后续修改的约束

后续在 auth.py 或未来拆分时，不得破坏以下行为：

| 约束 | 说明 |
|------|------|
| follower 不 clear | follower 无权修改 auth.json |
| follower 不 rebuild | follower 不进入 `ensure_logged_in(prefer_refresh=True)` |
| leader 独占恢复主链 | 同一时间只有一个 leader 执行 clear+rebuild |
| 失败后有 backoff | leader 失败后必须有保护窗口 |

任何未来重构若改变上述语义，必须同步修改本文档与验收用例。
