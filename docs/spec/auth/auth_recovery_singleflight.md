# 认证后台恢复 singleflight 约束

> 状态：现行专项约束
> 最后核对：2026-09-28
> 实现：`xiaomusic/auth.py` 的后台 recovery 与 singleflight 方法

## 1. 范围

本文只约束后台认证恢复任务的并发隔离，不定义认证状态机、token 交换或 runtime commit 语义；后者以 `auth_runtime_recovery.md` 为准。

## 2. 角色

| 角色 | 条件 | 行为 |
|---|---|---|
| leader | 在锁内确认无 inflight recovery 且不在 backoff | 独占执行 `_schedule_background_recovery()` 创建的恢复主链 |
| follower | 已有 leader | 等待 `_recovery_complete_event`，不再启动第二条恢复链 |
| blocked | recovery backoff 仍有效 | 直接跳过本轮恢复 |

## 3. 实现约束

1. `_try_acquire_recovery_leader()` 只做启动前快速判断，不设置 inflight。
2. `_acquire_recovery_leader_lock()` 才能在 `_recovery_lock` 内设置 `_recovery_inflight`、leader ctx 并清除完成事件。
3. leader 必须在 `finally` 中调用 `_release_recovery_leader()`。
4. 非 `ok` 结果进入 backoff；已经判定需要人工登录时不再叠加自动 backoff。
5. `_wait_for_recovery_complete()` 必须有超时，不能无限等待。
6. `self._recovery_task` 由 AuthManager 持有；已有未完成任务时不得重复创建。

## 4. 不再适用的旧描述

旧版本把 singleflight 描述为 `auth_call` 中的 “first suspect → non-destructive recovery → clear short session → rebuild” 链路，并引用 `_clear_short_lived_session()`、`_attempt_non_destructive_auth_recovery()` 等方法。当前代码已经移除这些方法，这套阶段模型不再是现行约束。

## 5. 验证依据

- `xiaomusic/auth.py`：`_schedule_background_recovery()`、`_try_acquire_recovery_leader()`、`_acquire_recovery_leader_lock()`、`_release_recovery_leader()`、`_wait_for_recovery_complete()`
- `tests/test_auth_runtime_stability.py`
- `tests/unit/test_auth_short_session_rebuild.py`
- `tests/unit/test_auth_classification_regression.py`
