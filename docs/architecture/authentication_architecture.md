# 认证系统架构

> 本文描述当前认证状态机主线的认证架构，不绑定短期 commit。认证恢复的实现细节见
> [`auth_runtime_recovery.md`](auth_runtime_recovery.md)，行为验收见
> [`docs/spec/auth/auth_runtime_recovery.md`](../spec/auth/auth_runtime_recovery.md)。

## 1. 三层认证模型

认证状态不是一个 token，而是三个有明确边界的层次：

### 1.1 Persistent auth：长期认证材料

最小换票能力：

```text
userId, passToken, deviceId
```

`psecurity`、`ssecurity`、`cUserId` 可作为完整诊断字段，但不是 serviceLogin 的硬前提。

- 由 `TokenStore` 管理，磁盘事实来源通常是 `conf/auth.json`。
- 用于重建 short session 和构造候选 runtime。
- 完整长期字段存在时，恢复优先走 persistent-auth short-session rebuild。
- 缺失或被明确判定为长期失效时，公共状态可进入 `manual_login_required`。

### 1.2 Short session：短期业务会话

典型字段：

```text
serviceToken, yetAnotherServiceToken
```

- 参与 Mina/MiIO 业务调用，生命周期短。
- 旧 `serviceToken` 即使仍存在，也不能证明 session 未过期。
- short session 缺失或自然过期时，不能因此回退到无口令 `MiAccount.login`。

### 1.3 Runtime：进程内运行时对象

包括：

```text
login_account, mina_service, miio_service,
mi_session, cookie_jar, login_signature, device_id
```

- 只代表当前进程是否拥有已验证、可用的服务对象。
- runtime 健康验证时间不等于 token 发行时间，也不延长 token TTL。
- runtime 切换必须经过 candidate verify，失败不得污染旧 runtime。

## 2. TokenStore 与环境覆盖

`TokenStore` 是认证数据的持久化事实来源。atomic 主路径使用：

```text
candidate auth data
  -> verify candidate runtime
  -> TokenStore.commit()（需要持久化时）
  -> runtime swap
```

`TokenStore.commit()` 在持锁状态下工作：持久化成功后才更新内存镜像；失败时
`_token`、`_dirty`、`_loaded` 保持原状。

`AUTH_ACCESS_TOKEN` / `AUTH_REFRESH_TOKEN` 是运行时覆盖：

- 只影响当前进程读取和 runtime candidate。
- 永不写入 TokenStore 或磁盘。
- scheduled refresh 检测到 env override 后直接记录
  `scheduled_env_override_skip`，不换票、不写盘。
- manual reload 会先 reload 磁盘镜像，再用 env 覆盖构造 candidate、verify、swap，
  同样不换票、不写盘。

## 3. 统一 runtime 切换事务

所有 atomic runtime 切换遵守同一顺序：

```text
构造 candidate
  -> candidate verify
  -> TokenStore.commit（需要持久化时）
  -> 一次性同步替换全部 runtime 引用
  -> runtime generation += 1
  -> await 关闭旧 session
```

必须一次性同步的引用包括：

- `device_id`
- `login_account`
- `mina_service`
- `miio_service`
- `mi_session`
- `cookie_jar`
- `login_signature`

verify、commit 或 candidate 构造失败时，旧 token、旧 runtime 和旧 generation 保持不变。
关闭旧 session 的 await 发生在新 runtime 已完整可见之后；取消或关闭挂起不能留下
新 session 与旧 service 的混合状态。

## 4. 恢复入口与优先级

### 4.1 Probe / ensure

`ensure_auth()` 先验证当前 runtime。真实 probe auth failure 后：

1. 取得 transition lock。
2. 若有完整 persistent auth，优先 atomic short-session rebuild。
3. 即使旧 `serviceToken` 仍存在，也不得跳过 rebuild。
4. atomic rebuild 失败不得进入无口令 `MiAccount.login`；当前 Config 只有 HTTP Basic 账号字段，
   不提供 Xiaomi 账号/密码，因此不存在 legacy full-login 能力。
5. Reactive/manual reload 的 `70016`（credential/session rejected）或 `87001`/`captchaUrl`
   （interactive captcha challenge）首次即进入持久 `manual_login_required`，但
   `long_term_expired=false`；后续非 force ensure 短路。Scheduled 保持 healthy 并挂起后续
   刷新，直到 verified recovery。

### 4.2 Scheduled refresh

scheduled refresh 只调用 atomic persistent-auth rebuild：

- env override：直接 skip。
- 无 persistent auth capability：记录 capability skip。
- 失败不降级仍健康的 runtime。
- 70016/87001 失败设置 `scheduled_refresh_suspended` 及原因，后续周期零网络；verified
  recovery 清除挂起。
- 网络/5xx/未知错误只推进独立 refresh attempt cooldown。
- 有效 `expires_in` 时，TTL 为 `saveTime + expires_in`，按
  `auth_refresh_threshold` 的 `ttl_ratio` 模式触发；阈值安全夹在 `0.01..0.99`。
- 无有效 `expires_in` 时，不伪造 TTL，按 `auth_refresh_interval_hours` 的
  `interval_fallback` 模式比较 `saveTime` elapsed；配置下限为 `0.01` 小时，默认 `12` 小时，
  不额外应用 ratio 阈值。
- 两种模式都只由持久 token 的 `saveTime` 驱动；runtime probe 成功不能续命或更新锚点。
- 成功后更新 session success、runtime verify 和 recovery debug 状态。

### 4.3 Manual reload

manual reload：

1. 若有 TokenStore，先执行 `reload_from_disk()`。
2. env override 模式只 rebind/verify，不换票、不持久化环境凭据。
3. 普通模式使用 atomic persistent refresh。
4. credential/session rejection、captcha 等 manual-intervention/challenge 类失败映射为
   `manual_login_required`；这不等同于 `long_term_expired`。
5. verified runtime recovery 成功后清除人工认证锁和 reason，恢复 `HEALTHY`。

### 4.4 Generation 与 transition lock

候选事务共享专用 transition lock。请求在等待 lock 前捕获 runtime generation；获得
lock 后若 generation 已变化且当前 runtime `HEALTHY`、对象就绪，则复用先前事务结果，
不重复 login/rebuild。generation 只在 verified runtime 成功提交后递增。

## 5. 时间语义

以下时间轴不得混用：

| 时间 | 语义 | 用途 |
|---|---|---|
| session success | 会话建立/刷新成功 | 记录认证成功 |
| runtime verify | 当前 runtime 探针成功 | 健康观测 |
| refresh attempt | scheduled 尝试或 skip | 刷新节流 |
| `saveTime` | 持久 token 发行/刷新事实 | TTL 计算 |

健康探针和 `last_ok_ts` 不能代替 `saveTime`，也不能代替 refresh attempt。

## 6. Legacy 边界

`rebuild_short_session_from_persistent_auth(atomic=False)` 只保留为显式兼容路径，
不属于生产主线。生产调用点均使用 `atomic=True`，主线不再描述
`MiAccount.login` 或 primary 失败后自动 destructive fallback 为默认恢复策略。

## 7. 公共状态与观测

重点观测：

- `/api/v1/auth/status`
- `/api/v1/debug/auth_state`
- `/api/v1/debug/auth_recovery_state`
- `/api/v1/debug/auth_runtime_reload_state`
- `/api/v1/debug/auth_short_session_rebuild_state`

应能区分：

- `short_session_rebuild_failed`
- `manual_login_required`
- `runtime_not_ready`
- `scheduled_env_override_skip`
- atomic verify/commit 失败

## 8. 已知边界

- 云端风控、网络和 DNS 仍可能导致认证失败。
- legacy atomic=False 仅为兼容性保留，后续可继续收敛删除。
- 认证数据写回统一使用 `TokenStore.commit()`；候选失败不得触发任何 auth.json 写回。
