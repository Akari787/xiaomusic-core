# 认证恢复状态机规范

版本：v1.0
状态：行为冻结文档
最后更新：2026-03-28
适用范围：`xiaomusic/auth.py` 内认证错误判定、恢复链路与状态转移

---

## 1. 文档目的

本文档冻结 `xiaomusic/auth.py` 当前认证恢复链的真实行为，用于：

- 为后续 singleflight 实机验收提供统一行为基线
- 校正旧文档中与当前实现不一致的表述
- 为未来是否拆分 AuthRecoveryCoordinator 提供行为约束前提

本文档是"现状约束文档"，不是未来重构设计稿。

---

## 2. 范围与非目标

### 2.1 范围

- auth.py 内认证错误判定逻辑
- suspect 状态与升级条件
- non-destructive recovery（Phase A / Phase B）
- strong evidence 定义与升级条件
- clear short session 与 rebuild 流程
- auth_mode 状态转移
- singleflight leader/follower 机制

### 2.2 非目标

- 不讨论未来插件化架构
- 不讨论完整重构方案
- 不替代 `docs/api/api_v1_spec.md`
- 不替代 `docs/spec/auth_runtime_recovery.md` 中的历史背景描述

---

## 3. 当前恢复链总览

当前自动恢复不是单一路径，而是分层恢复链：

```
auth_call 捕获 auth error
    ↓
判断 should_clear_short_session
    ├─ first_suspect → non-destructive recovery
    │       ├─ Phase A (runtime rebuild with existing short session)
    │       │       ├─ success → 重试原请求
    │       │       └─ verify_failed + auth_failure_detected → strong evidence → 升级到 clear+rebuild
    │       └─ Phase B (轻量 verify)
    │               ├─ success → 重试原请求
    │               └─ failed → 抛出错误，不 clear
    │
    ├─ consecutive_auth_error_same_ctx → clear+rebuild (leader)
    └─ consecutive_auth_error_multiple → clear+rebuild (leader)

clear+rebuild 路径:
    _clear_short_lived_session
        ↓
    ensure_logged_in(prefer_refresh=True)
        ↓
    _rebuild_short_session_from_persistent_auth
        ↓
    rebuild_services (runtime rebind)
        ↓
    verify
        ↓
    success → 重试原请求
```

关键区分：

- **首次 auth error**：不立即 clear short session，走 non-destructive recovery
- **suspect 状态**：记录首次错误，等待升级条件
- **strong evidence**：Phase A verify 失败且检测到认证失败，同一轮内升级到 clear+rebuild
- **consecutive error**：同一 ctx 连续错误或窗口内多次错误，直接进入 clear+rebuild

---

## 4. 状态定义

### 4.1 healthy

- 语义：认证状态正常，业务请求可正常执行
- 进入条件：`ensure_logged_in` 成功完成（rebuild + rebind + verify 全部成功）
- 退出条件：auth error 发生

### 4.2 degraded

- 语义：认证状态异常，部分能力可能不可用
- 进入条件：
  - `ensure_logged_in` 失败
  - keepalive 验证失败
- 退出条件：恢复成功后转为 healthy

### 4.3 locked

- 语义：认证锁定，需要人工干预（扫码登录）
- 进入条件：长期态必需字段缺失或不完整，导致 persistent auth 不可用时，恢复失败后可能进入 locked
- 判定依据：`_has_persistent_auth_fields` 返回 False（当前实现检查 `passToken`、`psecurity`、`ssecurity` 等关键字段是否存在）
- 退出条件：
  - 扫码登录后调用 `clear_auth_lock`
  - 锁定超时后自动转为 degraded

**重要**：locked 不是短期 token 失效后的默认结果。只有在长期态缺失或等价硬故障导致恢复失败时才应进入 locked。

---

## 5. auth error 检测与 suspect 机制

### 5.1 错误分类

`auth_call` 捕获异常后进行分流：

| 错误类型 | 处理方式 |
|----------|----------|
| network error (`is_network_error`) | 直接抛出，不进入恢复链 |
| 非严格 auth error (`!is_auth_error_strict`) | 直接抛出，不进入恢复链 |
| 严格 auth error (`is_auth_error_strict`) | 进入 suspect 判定与恢复链 |

### 5.2 `_should_clear_short_session_on_auth_error` 行为

该方法决定是否立即 clear short session：

| 条件 | 返回值 | 说明 |
|------|--------|------|
| 同一 ctx 短时间内连续错误，streak >= 1 | `(True, "consecutive_auth_error_same_ctx")` | 升级到 clear |
| 不同 ctx 但在窗口内连续错误，streak >= 2 | `(True, "consecutive_auth_error_multiple")` | 升级到 clear |
| 第一次或窗口外的错误 | `(False, "first_suspect")` | 走 non-destructive recovery |

### 5.3 suspect 计数机制

- `_auth_error_suspect_streak`：连续 auth error 计数
- `_last_auth_error_suspect_ts`：上次 auth error 时间戳
- `_last_auth_error_suspect_ctx`：上次 auth error 上下文
- `_auth_error_suspect_window_sec`：suspect 窗口时间（默认 30 秒）

**核心语义**：suspect 计数是为了避免首次 auth error 立即 clear short session。只有在短时间内连续出现 auth error 时才升级为 clear。

### 5.4 suspect 重置时机

- clear short session 执行后，当前实现会显式调用 `_reset_auth_error_suspect()` 重置 suspect 状态
- non-destructive recovery 成功后，当前实现没有显式 reset suspect；这是当前实现现状，不代表必然行为规范

---

## 6. non-destructive recovery 规范

non-destructive recovery 是首次/保守阶段的优先恢复路径，不 clear short session，不 mark_session_invalid。

### 6.1 Phase A：runtime rebuild with existing short session

**目标**：使用 auth.json 中已有的 short session 重建 runtime 对象

**流程**：

1. 重新读取 auth.json / token_store
2. 检查是否存在 short session（`serviceToken` 或 `yetAnotherServiceToken`）
3. 检查是否存在 `userId`
4. 创建新的 MiAccount 实例
5. 注入已有 token（`token["micoapi"] = (ssecurity, serviceToken)`）
6. 创建新的 MiNAService
7. 调用 `device_list()` 验证
8. 成功则原子替换当前 runtime 引用
9. 失败则丢弃临时对象，不影响当前 runtime

**失败时不 clear short session，不写空 auth.json。**

**失败原因分类**：

| reason | 语义 | 是否 strong evidence |
|--------|------|---------------------|
| `no_existing_short_session` | auth.json 缺少 serviceToken | 否 |
| `missing_userId` | auth.json 缺少 userId | 否 |
| `verify_failed` + `auth_failure_detected=true` | 验证失败且检测到认证错误 | **是** |
| `verify_failed` + `auth_failure_detected=false` | 验证失败但非认证错误 | 否 |
| `exception` | 未知异常 | 否 |

### 6.2 Phase B：轻量 runtime verify

**目标**：在 Phase A 失败且非 strong evidence 时，尝试轻量验证当前 runtime

**流程**：

1. 重新加载 token_store
2. 检查 short session 是否存在
3. 调用 `_verify_runtime_auth_ready()` 验证当前 runtime
4. 成功则返回 `runtime_verified`
5. 失败则返回 `all_attempts_failed`

**Phase B 不修改任何状态，只是验证当前 runtime 是否仍然可用。**

**Phase B 不等于 clear+rebuild。**

---

## 7. strong evidence 定义与升级条件

### 7.1 strong evidence 判定条件

`_is_strong_short_session_invalidation_evidence` 返回 True 的条件：

1. `reason` 包含 `verify_failed`
2. `auth_failure_detected` 为 True（或 detail 文本匹配认证失败关键词）

### 7.2 属于 strong evidence 的场景

- Phase A verify 失败且检测到 Mina auth failure（`login failed`、`401`、`70016`）

### 7.3 不属于 strong evidence 的场景

| 场景 | 说明 |
|------|------|
| `no_existing_short_session` | 缺少 short session，不是"short session 失效" |
| `missing_userId` | 缺少 userId，不是认证失败 |
| `exception` | 未知异常，无法判断是否为认证错误 |
| `verify_failed` 但 `auth_failure_detected=false` | 验证失败但非认证错误（如网络错误） |

### 7.4 升级行为

当检测到 strong evidence 时：

1. 返回 `(False, detail, True)` 给 `_attempt_non_destructive_auth_recovery`
2. `_attempt_non_destructive_auth_recovery` 返回 `escalate_to_clear=True`
3. `auth_call` 进入 clear+rebuild 路径（同一轮内）

---

## 8. clear short session 与 rebuild 规范

### 8.1 clear 的对象

**runtime 注入态**：

- 清除 `mina_service.account.token["micoapi"]`
- 清除 `miio_service.account.token["micoapi"]`

**auth.json / token_store 中的 short session 字段**：

- `serviceToken`
- `yetAnotherServiceToken`

**保留的长期态字段**：

- `passToken`
- `psecurity`
- `ssecurity`
- `userId`
- `cUserId`
- `deviceId`

### 8.2 clear 后的 rebuild 主链

clear 后进入 `ensure_logged_in(prefer_refresh=True)`：

```
_clear_short_lived_session(clear_reason)
    ↓
_rebuild_short_session_from_persistent_auth(reason)
    ├─ persistent_auth_login (primary path: 使用长期态重建短期态)
    └─ refresh_token_fallback (如果 persistent 失败)
    ↓
rebuild_services(reason, allow_login_fallback=False)
    └─ runtime rebind (重新初始化 mina_service / miio_service)
    ↓
_verify_runtime_auth_ready()
    └─ 调用 device_list 验证
```

注意：`_rebuild_short_session_from_persistent_auth` 内部包含 primary path 与 refresh fallback，不是单一路径。

### 8.3 rebuild 成功/失败后的状态变化

| 结果 | auth_mode | 说明 |
|------|-----------|------|
| rebuild 成功 | healthy | 恢复完成 |
| rebuild 失败但有长期态 | degraded | 下次重试可能恢复 |
| rebuild 失败且无长期态 | locked | 需要人工扫码登录 |

rebuild 失败时最终可能进入 degraded 或 locked，而不是默认回到 healthy。恢复主链不是"唯一成功路径"，而是当前实现的主要恢复路径。

---

## 9. manual reload runtime 与自动恢复的边界

### 9.1 manual_reload_runtime / refresh_runtime

- 接口：`POST /api/auth/refresh`、`POST /api/auth/refresh_runtime`
- 语义：从磁盘重新加载 auth.json，重建 runtime
- 与自动恢复链不是同一个入口
- 适用于扫码登录后手动触发恢复

### 9.2 扫码登录后从磁盘重建 runtime

用户完成扫码登录后：

1. 新的 auth token 已持久化到 auth.json
2. 调用 `POST /api/auth/refresh` 或 `POST /api/auth/refresh_runtime`
3. 系统从磁盘重载认证状态
4. 执行 runtime rebind
5. 执行 verify

**这一过程不依赖自动恢复链，是独立的手动恢复入口。**

---

## 10. 可观测性与日志事件

### 10.1 关键事件

| 事件名称 | 用途 |
|----------|------|
| `auth_short_session_clear_decision` | 记录 clear 决策：是否执行、决策原因、suspect streak |
| `auth_non_destructive_recovery` | 记录非破坏性恢复：Phase A/B、成功/失败、是否 strong evidence |
| `auth_runtime_rebuild_with_existing_short_session` | 记录 Phase A 细节：创建对象、验证结果、swap 结果 |
| `auth_recovery_singleflight` | 记录 singleflight：leader/follower、backoff、开始/结束 |
| `auth_recovery_flow` | 记录完整恢复流程：初始状态、rebuild 策略、rebind 结果、verify 结果 |
| `auth_cookie_rebuild` / `auth_persistent_auth_relogin` | 记录 persistent-auth 重建过程 |
| `auth_runtime_reload` | 记录 runtime reload：token_store 状态、service 重建、verify 结果 |
| `auth_mode_transition` | 记录 auth_mode 状态转移 |

### 10.2 验收用途

对下一次 24h 掉线做实机验收时，应关注：

1. `auth_short_session_clear_decision` 中的 `decision_reason`：判断是 first_suspect 还是 consecutive error
2. `auth_non_destructive_recovery` 中的 `phase` 和 `strong_invalidation_evidence`：判断 Phase A 是否失败、是否升级
3. `auth_recovery_singleflight` 中的 `role`：判断是否有多路并发 recovery
4. `auth_recovery_flow` 中的 `result` 和 `used_rebuild_strategy`：判断 rebuild 是否成功

---

## 11. 当前已知边界

1. **Xiaomi 云端风控不可消除**：`70016` 等风控错误仍可能发生
2. **系统目标是可恢复、可观测、可预测，而不是零失败**
3. **本文档描述的是当前实现基线，不代表未来永久架构**
4. **singleflight 是否在线上真正完全生效，仍需后续实机验收确认**
5. **Phase A 成功依赖 auth.json 中已有有效的 short session**：如果 short session 已过期，Phase A 会失败
6. **本文档同时包含"当前代码已实现行为"与"当前验收应验证的行为约束"**：对尚未完成实机闭环验证的内容，不应视为线上已证明事实

---

## 12. 与现有文档关系

- 本文档用于补充并校正 `docs/spec/auth_runtime_recovery.md` 中与当前实现不一致的部分
- 旧文档中的历史背景保留，但**当前实现行为以本文档为准**
- 旧文档中"唯一允许顺序 = clear short session → persistent-auth login → runtime rebind → verify"的表述已不准确，当前实现存在 non-destructive recovery 作为前置路径
