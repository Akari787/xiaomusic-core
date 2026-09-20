# Auth 运行时恢复实现与观测参考

> 本文对应 HEAD `66cc131` 的实际实现。它是实现/观测参考，不替代
> [`docs/spec/auth/auth_runtime_recovery.md`](../spec/auth/auth_runtime_recovery.md) 的验收规范。

## 1. 实现边界

认证运行时分三层：

1. **persistent auth**：`passToken`、`psecurity`、`ssecurity`、`userId`、`cUserId`、`deviceId`。
2. **short session**：`serviceToken`、`yetAnotherServiceToken`。
3. **runtime**：`login_account`、`mina_service`、`miio_service`、session、cookie 和签名。

`TokenStore`/`conf/auth.json` 是持久认证事实来源。env credential 是运行时覆盖，
不属于持久事实，禁止写回磁盘。

## 2. 认证入口

### 2.1 `ensure_auth()` / `_try_login()`

`ensure_auth()` 先做 runtime probe。probe 失败后，`_try_login()` 在 transition lock
内按以下顺序工作：

```text
读取 auth_data
  -> env override？runtime rebind/verify（不换票）
  -> mina_service 缺失？尝试 persisted short-session fast rebind/verify
  -> 完整 persistent auth？atomic short-session rebuild
  -> 没有明确 persistent capability 时，才允许进入显式 full-login 场景
```

**自然过期边界**：即使磁盘仍保留旧 `serviceToken`，只要完整 persistent auth 存在，
fast rebind 未成功后仍必须走 atomic rebuild；atomic 失败绝不能落到无口令
`MiAccount.login`。

完整无口令 login 不是 scheduled、manual、persistent-auth recovery 的 fallback。

### 2.2 Scheduled refresh

`_maybe_scheduled_refresh()`：

- 若持久 token 有明确、有效、正数的 `expires_in`，以 `saveTime + expires_in`
  计算 TTL，使用 `auth_refresh_threshold`（安全夹在 `0.01..0.99`）按剩余比例触发，日志模式为 `ttl_ratio`。
- 若 `expires_in` 缺失或无效，不伪造 TTL；以 `auth_refresh_interval_hours`
  （安全正数下限 `0.01` 小时，默认 `12` 小时）作为固定刷新间隔，按持久
  `saveTime` 计算 elapsed，日志模式为 `interval_fallback`，此路径不再乘阈值。
- runtime probe 成功不会更新 TTL 锚点或 `saveTime`。
- env override 直接记录 `scheduled_env_override_skip` 并返回。
- 无 persistent capability 记录 capability skip。
- 其他情况调用：

```python
await rebuild_short_session_from_persistent_auth(
    reason="_maybe_scheduled_refresh",
    atomic=True,
)
```

scheduled 失败不修改仍健康 runtime；attempt timestamp 独立负责 cooldown。

### 2.3 Manual reload

`manual_reload_runtime()`：

1. 有 TokenStore 时先调用 `reload_from_disk()`。
2. env 模式调用当前 auth data 的 atomic runtime rebind/verify；不调用
   `_serviceLogin`、不调用 `MiAccount.login`、不写 token。
3. 非 env 模式调用 atomic persistent refresh。
4. verified success 清理 manual login gate；fatal 失败映射到
   `manual_login_required`。

返回中的 `token_store_reloaded` 只有真实执行 `reload_from_disk()` 后才能为 true。
`runtime_swap_attempted` 只在结果明确表示尝试时为 true，缺省/`None`/`skipped` 均为 false。

## 3. Atomic rebuild 实现

入口默认值为：

```python
rebuild_short_session_from_persistent_auth(reason="", atomic=True)
```

生产入口均显式使用 `atomic=True`。`atomic=False` 仅为 legacy 兼容路径。

### 3.1 Candidate pipeline

```text
persistent auth snapshot
  -> _try_miaccount_persistent_auth_relogin(writeback=False)
  -> candidate auth data
  -> _build_verified_runtime_candidate(candidate_auth_data)
  -> candidate device_list verify
  -> TokenStore.commit(candidate_auth_data)（env 时跳过）
  -> 一次性同步 runtime 引用
  -> generation += 1
  -> await close old session
```

candidate verify 失败时：

- 不调用 TokenStore.commit
- 不更新 `saveTime`
- 不替换旧 runtime
- 不关闭仍在使用的旧 session

TokenStore commit 失败时同样保留旧 token 镜像和旧 runtime。

### 3.2 Runtime 一次性提交

下列引用在任何 close await 前同步完成：

```text
device_id
login_account
mina_service
miio_service
mi_session
cookie_jar
login_signature
_runtime_generation
```

旧 session 只在新 runtime 完整可见后关闭。取消 close await 不会造成新 session+旧
service 的半提交状态。

### 3.3 TokenStore commit

`TokenStore.commit()` 持锁工作：

- `persist_token=true`：原子文件替换成功后才更新 `_token/_dirty/_loaded`。
- `persist_token=false`：明确为仅内存提交，不写磁盘且不保留 dirty。
- 异常时内存镜像三字段保持原值。

历史 `update()+flush()` 仍可被兼容代码使用，但不是 atomic 主路径。

## 4. Persistent relogin 与 session

`_try_miaccount_persistent_auth_relogin()` 只返回候选 token 数据和分类结果，不把
创建的 ClientSession 交给 runtime。TypeError legacy constructor 路径也会关闭创建的
session。

serviceLogin 非零 code 分类：

- `70016`、`87001`：`long_term_expired/need_qr_scan/user_action_required=true`。
- 未知非零 code，例如 `10001`、`500`：可属于 auth error，但不自动推断需要扫码。
- `service_login_failed` 和 `service_login_code_*` 是 auth 域错误标签，不等于长期失效。

## 5. Fatal manual gate

fatal 分类首次失败即进入持久 manual gate：

```text
state = LOCKED
_last_manual_login_required_reason != ""
locked_until 不作为人工 gate 的过期条件
```

非 force `ensure_auth()` 在 probe 前短路，不重复访问小米服务。

verified runtime recovery 成功统一调用内部 success helper：

- state=`HEALTHY`
- `locked_until=0`
- 清空 `_last_manual_login_required_reason`
- 清空 lock transition reason

失败恢复不调用该 helper。`clear_auth_lock()` 仍是显式人工/二维码成功入口。

## 6. Transition lock 与 generation

`_auth_transition_lock` 覆盖候选快照、verify、commit、runtime swap 的事务边界。

调用者等待 lock 前保存 generation；获得 lock 后：

```text
若 generation 已变化
且当前 state=HEALTHY
且 runtime ready
=> 复用前一个成功事务，不重复 login/rebuild
```

generation 只在 verified runtime 成功提交后递增。reason 字符串不参与判断。

## 7. 时间与观测字段

| 字段 | 语义 | 不得承担的语义 |
|---|---|---|
| `_last_session_success_ts` | session 建立/刷新成功 | 不作 health probe 或 cooldown |
| `_last_runtime_verify_ts` | runtime 验证成功 | 不延长 token TTL |
| `_last_refresh_attempt_ts` | scheduled 尝试/skip | 不代表 token 成功 |
| `TokenStore.saveTime` | 持久 token 发行/刷新事实 | 不被 probe 时间覆盖 |

相关 debug flow：

- `started_at` / `finished_at`
- `primary_attempt`
- `fallback_attempt`（scheduled atomic 中明确 skipped）
- `rebind`
- `verify`
- `result`

primary token exchange 成功但 candidate verify 失败时，必须显示：

```text
primary_attempt.result = ok
verify.result = failed
```

## 8. 公共状态映射

`map_auth_public_status()` 的重点结果：

- persistent auth 缺失：`persistent_auth_missing`
- short-session rebuild 失败：`short_session_rebuild_failed`
- runtime 未准备：`runtime_not_ready`
- fatal auth + manual gate：`manual_login_required`
- 健康：`healthy` / `status=ok`

70016/87001 第一次失败即为 `manual_login_required`；未知 serviceLogin 非零 code
不得误报扫码。

## 9. 代码定位

- `xiaomusic/auth.py`
  - `ensure_auth()` / `_try_login()`
  - `_maybe_scheduled_refresh()`
  - `rebuild_short_session_from_persistent_auth()`
  - `_atomic_persistent_auth_refresh()`
  - `_atomic_runtime_rebind_current_auth()`
  - `_try_miaccount_persistent_auth_relogin()`
  - `manual_reload_runtime()`
- `xiaomusic/security/token_store.py`
  - `TokenStore.commit()`
  - `TokenStore.reload_from_disk()`
- 观测端点：`/api/v1/auth/status` 与 `/api/v1/debug/auth_*`
