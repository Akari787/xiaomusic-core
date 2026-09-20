# 认证运行时恢复规范

> 本规范对应 HEAD `66cc131`。它定义 persistent auth、short session、runtime 的边界、
> atomic 恢复行为、公共状态和验收门禁。

## 1. 术语与事实来源

### 1.1 三层状态

| 层 | 内容 | 事实/用途 |
|---|---|---|
| persistent auth | `passToken`、`psecurity`、`ssecurity`、`userId`、`cUserId`、`deviceId` | TokenStore/`conf/auth.json` 的持久事实；用于重建 short session |
| short session | `serviceToken`、`yetAnotherServiceToken` | 短期业务会话；旧 token 存在不代表仍有效 |
| runtime | account、Mina/MiIO service、session、cookie、signature、device_id | 进程内、必须经过 verify 才能成为当前运行态 |

`AUTH_ACCESS_TOKEN` / `AUTH_REFRESH_TOKEN` 仅是运行时覆盖，不是持久事实：

- 不得写入 TokenStore 或磁盘。
- scheduled refresh 检测到 env override 必须 skip。
- manual reload 可使用覆盖凭据重绑/verify，但不得换票或写盘。

## 2. 统一 atomic runtime transition

所有生产 runtime 切换必须遵循：

```text
candidate auth/runtime
  -> verify candidate
  -> TokenStore.commit（需要持久化且无 env override）
  -> 一次性同步 swap 全部 runtime 引用
  -> generation += 1
  -> 关闭旧 session
```

全部引用包括：`device_id`、`login_account`、`mina_service`、`miio_service`、
`mi_session`、`cookie_jar`、`login_signature`。

约束：

1. candidate 构造/verify 失败：旧 runtime、旧 token、`saveTime` 不变。
2. `TokenStore.commit()` 失败：旧内存镜像和旧 runtime 不变。
3. 新引用必须在任何 `await old_session.close()` 前全部可见。
4. close 被取消或挂起，不得留下新 session+旧 service 混合状态。
5. 事务受 `_auth_transition_lock` 保护。
6. `runtime generation` 只在 verified runtime 提交后递增。

`rebuild_short_session_from_persistent_auth()` 默认 `atomic=True`。`atomic=False` 仅为
明确 legacy 兼容路径；生产调用点不得使用默认 destructive fallback。

## 3. 恢复入口规范

### 3.1 Probe 与 `_try_login`

runtime probe 失败时：

1. 进入 transition lock。
2. env override 存在：只调用 atomic runtime rebind/verify。
3. 完整 persistent auth 存在：fast rebind 未成功后只调用 atomic persistent-auth rebuild。
4. 无论旧 `serviceToken` 是否存在，都不能跳过 atomic rebuild。
5. atomic rebuild 失败不得进入无口令 `MiAccount.login`。
6. full login 只允许在明确具备该能力且不属于上述 atomic recovery 的场景。

### 3.2 Scheduled refresh

scheduled refresh 的触发条件：

- 当前 state 为 `HEALTHY`。
- TTL 低于阈值。
- refresh attempt cooldown 已到期。

TTL 基准只允许使用持久 token `saveTime`。runtime probe 成功不得更新 TTL 锚点。

执行规则：

- env override：记录 `scheduled_env_override_skip`，不网络换票、不 commit。
- 无 persistent capability：记录 capability skip。
- 正常路径：调用 `rebuild_short_session_from_persistent_auth(atomic=True)`。
- 失败不得把仍健康 runtime 降级。
- 失败或 skip 都占用独立 attempt cooldown。

### 3.3 Manual reload

manual reload 必须：

1. TokenStore 存在时先真实调用 `reload_from_disk()`。
2. env override 模式：读取磁盘后应用 env，构造 candidate、verify、swap；不调用
   `_serviceLogin`、不调用 `MiAccount.login`、不写盘。
3. 非 env 模式：调用 atomic persistent-auth rebuild。
4. `token_store_reloaded=true` 只在真实 reload 已执行时允许返回。
5. verified success 清除 manual gate。
6. `runtime_swap_attempted` 只有结果明确为尝试时才为 true；`None`、缺省、`skipped`
   均为 false。

## 4. 错误分类与公共状态

### 4.1 Fatal 长期认证失败

以下证据才允许设置：

```text
long_term_expired = true
need_qr_scan = true
user_action_required = true
```

- serviceLogin code `70016`
- serviceLogin code `87001`
- 明确的 refresh/passport/service token expired
- 明确的 need/scan QR、account locked、login required 证据

`service_login_failed`、`service_login_code_*` 可以作为 auth 域错误标签，
但未知非零 code（例如 `10001`、`500`）不得自动变成长期失效或扫码要求。

### 4.2 Fatal gate

首次收到 fatal 分类即：

- state=`LOCKED`
- 设置 `_last_manual_login_required_reason`
- 设置 lock transition reason
- `is_auth_locked()` 持续为 true，直到显式清除或 verified recovery 成功
- 公共映射：`status_reason=manual_login_required`

非 force `ensure_auth()` 在任何 probe 前短路，不重复访问小米接口。

### 4.3 Verified recovery clear

以下成功必须清除 manual gate：

- verified fast rebind
- atomic persistent refresh
- env runtime rebind
- manual reload success
- force ensure/_try_login success
- full login 成功且 runtime verify 已完成

清理内容：

- state=`HEALTHY`
- `locked_until=0`
- `_last_manual_login_required_reason=""`
- lock transition reason 清空

失败路径不得清理。

## 5. Transition lock 与 generation

请求在等待 `_auth_transition_lock` 前捕获 generation。获得 lock 后：

```text
如果 generation 已变化
且 state=HEALTHY
且 runtime ready
=> 复用先前成功事务并返回，不重复 recovery/login
```

不得用 reason 字符串或 `lock.locked()` 的瞬时状态替代 generation 判断。

## 6. 时间语义与观测

| 时间字段 | 语义 | 验收用途 |
|---|---|---|
| session success | 会话建立/刷新成功 | 证明认证成功，不作 TTL |
| runtime verify | runtime 探针成功 | 证明当前 runtime 健康 |
| refresh attempt | scheduled 尝试或 skip | 证明节流边界 |
| `saveTime` | 持久 token 发行/刷新事实 | 唯一 TTL 锚点 |

debug flow 必须保留：

- `started_at` / `finished_at`
- `primary_attempt`
- `fallback_attempt`
- `rebind`
- `verify`
- `result`

若 primary token exchange 成功但 candidate verify 失败：

```text
primary_attempt.result = ok
verify.result = failed
```

## 7. 验收规范

### 7.1 首次 scheduled 触发点

构造：

- state=`HEALTHY`
- 完整 persistent auth
- 持久 `saveTime` 进入 scheduled 阈值
- 可选旧 `serviceToken`

检查：

1. 只调用 atomic persistent-auth rebuild。
2. `MiAccount.login` 未调用。
3. env override 时无换票、无 TokenStore.commit，trigger 为
   `scheduled_env_override_skip`。
4. 成功时 `saveTime` 由 TokenStore.commit 更新，runtime healthy。
5. 失败时旧 `saveTime`、旧 runtime、state、`recovery_failure_count` 语义保持正确。
6. `runtime verify` 时间不被用作 TTL。

### 7.2 至少一个重复周期

在第一次 scheduled 成功、失败或 skip 后推进可控时间，至少再次执行一个周期：

- attempt cooldown 未到：不得再次换票/登录。
- cooldown 到期：最多一次新的 atomic candidate 尝试。
- 不得因每次 keepalive probe 成功而重置 token TTL。
- 记录并检查 `last_refresh_attempt_ts`、`last_session_success_ts`、
  `last_runtime_verify_ts` 的独立语义。

### 7.3 Natural expiry

即使旧 `serviceToken` 仍在磁盘：

- runtime probe auth failure 后必须进入 atomic rebuild。
- atomic failure 不得进入无口令 `MiAccount.login`。
- 70016/87001 第一次失败必须映射 `manual_login_required`，第二次非 force
  ensure 不得再次调用 `_serviceLogin`。
- 10001/500 等未知非零 code 不得误报扫码。

### 7.4 Manual reload

- 先断言真实调用 `TokenStore.reload_from_disk()`。
- env 模式只 rebind/verify，不换票、不写盘。
- fatal 失败检查：state、`auth_locked`、`need_qr_scan`、`manual_login_required_reason`、
  `runtime_auth_ready=false`。
- verified recovery 后检查 state=`HEALTHY`、`is_auth_locked=false`、reason 为空，
  随后普通 `ensure_auth()` 可继续执行。

### 7.5 Failure accounting

验收必须检查：

- `failure_count/recovery_failure_count`
- `error_code`
- `status_reason`
- `TokenStore.saveTime`
- `runtime_swap_attempted/runtime_swap_applied`
- `MiAccount.login` 和 `_serviceLogin` 的调用证据

## 8. 观测接口

正式状态：

- `/api/v1/auth/status`

调试状态：

- `/api/v1/debug/auth_state`
- `/api/v1/debug/auth_recovery_state`
- `/api/v1/debug/auth_runtime_reload_state`
- `/api/v1/debug/auth_short_session_rebuild_state`

调试接口必须能区分：

- `manual_login_required`
- `short_session_rebuild_failed`
- `runtime_not_ready`
- `scheduled_env_override_skip`
- primary exchange failure
- candidate verify failure
- token commit failure
