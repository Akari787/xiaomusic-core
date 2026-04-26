# Auth 运行时恢复链说明

> 适用范围：V1.1.1（auth 恢复链从零实现）当前代码实现。
> 目标：说明正式状态接口、统一状态映射、short-session 重建主链、调试观测面与失败分类。

## 1. 背景与边界

当前认证状态分为两层：

- **长期态（persistent auth）**
  - `userId`
  - `passToken`
  - `psecurity`
  - `ssecurity`
  - `cUserId`
  - `deviceId`
- **短期态（short session）**
  - `serviceToken`
  - `yetAnotherServiceToken`

长期态持久化在 `conf/auth.json`，用于在短期态缺失时重新建立运行时认证能力。

本轮 M4 的边界是：

1. 建立统一的对外认证状态映射
2. 新增正式 v1 端点 `/api/v1/auth/status`
3. 为 short-session 缺失场景建立自动重建主链
4. 用调试端点暴露 rebuild / rebind / verify 阶段结果

本轮**不**把 `/api/v1/debug/*` 升格为正式 API；它们仍是调试级接口。

---

## 2. 内部认证状态枚举

`auth.py` 内部运行时状态枚举为：

- `healthy`
- `degraded`
- `locked`

代码位置：`xiaomusic/auth.py`

```python
STATE_HEALTHY = "healthy"
STATE_DEGRADED = "degraded"
STATE_LOCKED = "locked"
```

这三个状态是内部恢复状态机的基础输入；对外正式返回的 `status` 则进一步映射为：

- `ok`
- `degraded`
- `failed`
- `unknown`

---

## 3. 正式接口：`/api/v1/auth/status`

路由位置：`xiaomusic/api/routers/v1.py`

该接口返回 v1 envelope，`data` 部分来自：

- `AuthManager.map_auth_public_status(runtime_auth_ready=...)`
- 并由路由附加 `generated_at_ms`

### 3.1 正式字段定义

当前实现中的正式字段如下：

| 字段 | 类型 | 含义 |
|---|---|---|
| `status` | `ok \| degraded \| failed \| unknown` | 对外稳定状态 |
| `auth_mode` | `healthy \| degraded \| locked \| unknown` | 内部认证模式 |
| `status_reason` | `string` | 对外稳定原因码 |
| `status_reason_detail` | `string` | 原因补充说明 |
| `status_mapping_source` | `string` | 当前状态由哪条映射规则得出 |
| `recovery_failure_count` | `int` | 恢复失败累计计数 |
| `persistent_auth_available` | `bool` | 长期态是否完整可用 |
| `short_session_available` | `bool` | `serviceToken / yetAnotherServiceToken` 是否存在 |
| `runtime_auth_ready` | `bool` | 运行时 auth 对象是否已准备好 |
| `auth_locked` | `bool` | 是否处于 locked |
| `auth_lock_until` | `int` | lock 截止时间戳（ms） |
| `auth_lock_reason` | `string` | 当前 lock 原因 |
| `auth_lock_transition_reason` | `string` | 最近进入 locked 的转换原因 |
| `auth_lock_counter` | `int` | lock 计数 |
| `auth_lock_counter_threshold` | `int` | 进入 locked 的阈值 |
| `manual_login_required_reason` | `string` | 需要人工重新登录时的原因 |
| `runtime_not_ready_reason` | `string` | 短期态存在但 runtime 未就绪时的说明 |
| `last_error` | `string` | 最近认证错误 |
| `rebuild_failed` | `bool` | 最近一次 short-session rebuild 是否失败 |
| `rebuild_error_code` | `string` | 最近 rebuild 的失败码 |
| `rebuild_failed_reason` | `string` | 最近 rebuild 的失败说明 |
| `generated_at_ms` | `int` | 路由生成时间 |

---

## 4. 统一状态映射

### 4.1 入口

统一映射由两层函数组成：

1. `auth_public_status_snapshot(runtime_auth_ready=None)`
   - 聚合内部状态、debug 状态和 rebuild 状态
   - 输出最小公共快照
2. `map_auth_public_status(runtime_auth_ready=None)`
   - 将内部状态快照映射为正式对外口径

### 4.2 映射规则

当前映射顺序如下：

#### 规则 1：`auth_locked == true`

- 若同时满足人工登录相关条件：
  - `need_qr_scan == true`，或
  - `long_term_expired == true`，或
  - `user_action_required == true`
- 则映射为：
  - `status_reason = "manual_login_required"`
  - `status_mapping_source = "locked_manual"`

否则映射为：
- `status_reason = "temporarily_locked"`
- `status_mapping_source = "locked_temporary"`

#### 规则 2：长期态缺失

当 `persistent_auth_available == false`：

- `status_reason = "persistent_auth_missing"`
- `status_reason_detail = "all long-lived auth fields missing from token"`
- `status_mapping_source = "persistent_auth_missing"`

#### 规则 3：长期态存在，但短期态缺失

当：
- `persistent_auth_available == true`
- `short_session_available == false`

分两种情况：

1. 最近 rebuild 已失败：
   - `status_reason = "short_session_rebuild_failed"`
   - `status_reason_detail = "rebuild failed: <rebuild_error_code>"`
   - `status_mapping_source = "short_session_rebuild_failed"`

2. 最近 rebuild 尚未失败记录：
   - `status_reason = "short_session_missing"`
   - `status_reason_detail = "short-lived session tokens missing"`
   - `status_mapping_source = "short_session_missing"`

#### 规则 4：长期态与短期态都存在，但 runtime 未就绪

当：
- `persistent_auth_available == true`
- `short_session_available == true`
- `runtime_auth_ready == false`

映射为：
- `status_reason = "runtime_not_ready"`
- `status_mapping_source = "runtime_not_ready"`

#### 规则 5：其他情况

默认：
- `status_reason = "healthy"`
- `status_mapping_source = "healthy"`

### 4.3 `status` 对外枚举映射

在 `status_reason` 选定后，再计算正式 `status`：

- `status_reason == "healthy"` → `status = "ok"`
- `auth_mode == "locked"` → `status = "failed"`
- `auth_mode in {"healthy", "degraded"}` 且 `status_reason != "healthy"` → `status = "degraded"`
- 其他情况 → `status = "unknown"`

---

## 5. short-session rebuild 双路径流程

入口函数：`rebuild_short_session_from_persistent_auth(reason="")`

### 5.1 文字流程图

```text
读取 auth.json / token_store
  -> 检查长期态是否完整
    -> 否：直接失败，error_code=missing_persistent_auth_fields
    -> 是：继续
  -> primary: _try_miaccount_persistent_auth_relogin(before, reason, sid="micoapi")
    -> 成功：进入 rebind
    -> 失败：进入 fallback
  -> fallback: _try_mijia_persistent_auth_relogin(auth_dir, sid="micoapi")
    -> 成功：进入 rebind
    -> 失败：整个 rebuild 失败
  -> 检查 serviceToken / yetAnotherServiceToken 是否已写回
    -> 否：失败，error_code=service_token_not_written
    -> 是：继续
  -> runtime rebind: _rebind_runtime_from_auth_data(merged_auth_data)
    -> 失败：error_code=runtime_rebind_failed
    -> 成功：继续
  -> verify: await self.mina_service.device_list()
    -> 失败：error_code=verify_failed
    -> 成功：rebuild 完成
```

### 5.2 primary 路径

函数：`_try_miaccount_persistent_auth_relogin()`

当前真实主路径：

1. 使用 `MiAccount._serviceLogin("serviceLogin?sid=micoapi&_json=true")`
2. 从返回结果提取：
   - `location`
   - `nonce`
   - `ssecurity`
3. 再调用：
   - `MiAccount._securityTokenService(location, nonce, ssecurity)`
4. 成功后写回：
   - `serviceToken`
   - `yetAnotherServiceToken`
   - `ssecurity`
5. 若存在 `token_store`，通过 `token_store.update(...); flush()` 持久化

### 5.3 fallback 路径

函数：`_try_mijia_persistent_auth_relogin()`

当前 fallback 路径：

1. 实例化 `MiJiaAPI`
2. 调用：
   - `MiJiaAPI.rebuild_service_cookies_from_persistent_auth("micoapi")`
3. 从最新 auth 数据中回填：
   - `serviceToken`
   - `yetAnotherServiceToken`
   - `ssecurity`
4. 作为 fallback 的 relogin 结果返回给 rebuild 主函数

### 5.4 rebind 阶段

函数：`_rebind_runtime_from_auth_data(auth_data)`

作用：

- 用新的 auth 数据重新创建：
  - `MiAccount`
  - `MiNAService`
  - `MiIOService`
- 更新：
  - `self.login_account`
  - `self.mina_service`
  - `self.miio_service`
  - `self.login_signature`

成功返回：

```json
{"ok": true, "result": "ok"}
```

失败返回：

```json
{
  "ok": false,
  "result": "failed",
  "error_code": "runtime_rebind_failed",
  "failed_reason": "..."
}
```

### 5.5 verify 阶段

verify 当前最小标准为：

- `self.mina_service` 可用
- `await self.mina_service.device_list()` 成功

成功后：
- `self._last_ok_ts` 更新
- rebuild 结果记为成功

失败后：
- `error_code = "verify_failed"`

---

## 6. 阶段化调试结构

### 6.1 `last_auth_recovery_flow`

`/api/v1/debug/auth_short_session_rebuild_state` 中的 `last_auth_recovery_flow` 表示最近一次 short-session 恢复链的阶段化流转。

当前字段：

| 字段 | 含义 |
|---|---|
| `reason` | 触发 rebuild 的原因，例如 `init_all_data` |
| `started_at` | 本次恢复链开始时间（ms） |
| `primary_attempt` | primary 路径结果 |
| `fallback_attempt` | fallback 路径结果；未命中时为 `{"result": "skipped"}` |
| `rebind` | runtime rebind 结果 |
| `verify` | verify 结果 |
| `result` | 整个恢复链最终结果：`running / ok / failed` |
| `used_path` | 最终实际命中的路径，例如 `miaccount_persistent_auth_login` 或 `mijia_persistent_auth_login` |
| `finished_at` | 本次恢复链结束时间（ms） |

其中阶段节点统一使用以下字段风格：

| 字段 | 含义 |
|---|---|
| `attempt_at` | 阶段执行时间（ms） |
| `used_path` | 该阶段关联路径 |
| `error_code` | 阶段失败码；成功时为空字符串 |
| `result` | `ok / failed / skipped` |

### 6.2 `last_short_session_rebuild`

`last_short_session_rebuild` 是对最近一次 rebuild 结果的压缩摘要，字段如下：

| 字段 | 含义 |
|---|---|
| `ok` | 本次 rebuild 是否成功 |
| `result` | `ok / failed` |
| `used_path` | 最终实际命中的 rebuild 路径 |
| `error_code` | 最终失败码；成功时为空 |
| `failed_reason` | 失败原因说明；成功时为空 |
| `service_token_written` | 是否已经把短期 token 写回持久层/合并后的 auth 数据 |
| `runtime_rebind_result` | `ok / failed / skipped` |
| `verify_result` | `ok / failed / skipped` |
| `ts` | 该摘要写入时间（ms） |

### 6.3 `auth_short_session_rebuild_debug_state()` 返回结构

该调试接口当前返回：

| 字段 | 含义 |
|---|---|
| `state` | 当前内部 auth mode |
| `cooldown_until` | 冷却截止时间 |
| `last_short_session_rebuild` | 最近一次 rebuild 摘要 |
| `last_persistent_auth_relogin` | 最近一次真正使用的 relogin 阶段；若最终路径为 `mijia*`，则取 fallback，否则取 primary |
| `last_runtime_rebind` | 最近一次 rebind 结果 |
| `last_verify` | 最近一次 verify 结果 |
| `last_auth_recovery_flow` | 最近一次完整恢复链阶段流 |

---

## 7. Debug 端点说明

下列端点均为**调试级**接口，不承诺正式 v1 稳定兼容：

| 端点 | 作用 |
|---|---|
| `/api/v1/debug/auth_state` | 查看当前 auth mode、lock 信息、最近错误、状态映射相关基础数据 |
| `/api/v1/debug/auth_recovery_state` | 查看恢复任务、退避、计数器、终止阶段与终止错误码 |
| `/api/v1/debug/miaccount_login_trace` | 查看最近一次 MiAccount 登录/换票轨迹 |
| `/api/v1/debug/auth_rebuild_state` | 当前复用 `auth_debug_state()`，作为 rebuild 相关兼容调试面 |
| `/api/v1/debug/auth_runtime_reload_state` | 查看 runtime reload 相关状态 |
| `/api/v1/debug/auth_short_session_rebuild_state` | 查看 short-session rebuild 的 primary/fallback/rebind/verify 阶段流 |

M4 收口最关键的 debug 端点是：

- `/api/v1/debug/auth_short_session_rebuild_state`

因为它直接反映：
- rebuild 是否发生
- primary 是否命中
- fallback 是否命中
- rebind / verify 是否成功
- 最终失败码是什么

---

## 8. 失败分类与 `error_code` 清单

### 8.1 primary: `_try_miaccount_persistent_auth_relogin()`

| `error_code` | 含义 |
|---|---|
| `missing_persistent_auth_fields` | 长期态字段不完整 |
| `invalid_service_login_response` | `_serviceLogin()` 返回结构非法 |
| `service_login_failed` | `_serviceLogin()` 返回 code 非 0 |
| `redirect_missing_location` | 登录返回缺 `location` |
| `redirect_missing_nonce` | 重定向参数缺 `nonce` |
| `redirect_missing_ssecurity` | 重定向或 auth_data 缺 `ssecurity` |
| `security_token_service_failed` | `_securityTokenService()` 调用失败 |
| `empty_service_token` | `_securityTokenService()` 返回空 token |

### 8.2 fallback: `_try_mijia_persistent_auth_relogin()`

| `error_code` | 含义 |
|---|---|
| `missing_persistent_auth_fields` | 长期态字段不完整 |
| `mijia_persistent_auth_login_failed` | MiJia fallback 调用异常 |
| `invalid_mijia_relogin_response` | MiJia fallback 返回结构非法 |

### 8.3 rebuild 主函数: `rebuild_short_session_from_persistent_auth()`

| `error_code` | 含义 |
|---|---|
| `missing_persistent_auth_fields` | 进入主函数时长期态不完整 |
| `persistent_auth_relogin_failed` | primary/fallback 最终都未返回可用 relogin 结果时的兜底码 |
| `service_token_not_written` | relogin 成功但最终没有拿到 `serviceToken / yetAnotherServiceToken` |
| `runtime_rebind_failed` | rebind 失败 |
| `verify_failed` | verify 失败 |

说明：
- 若 primary 失败且 fallback 也失败，最终 `error_code` 会优先透传 fallback/primary 的真实错误码；只有没有真实错误码时才退回 `persistent_auth_relogin_failed`。
- `map_auth_public_status()` 只把最近 rebuild 的终态压缩为：
  - `short_session_rebuild_failed`
  - 以及其对应的 `rebuild_error_code`

---

## 9. 与 `/api/auth/status` 的关系

`/api/auth/status` 是**内部 API**，主要供 WebUI / 内部认证流程使用，不承诺长期兼容。

`/api/v1/auth/status` 是**正式 v1 端点**。

二者的关系是：

1. `system.py::_build_auth_status_payload()` 内部会优先调用：
   - `am.map_auth_public_status(runtime_auth_ready=runtime_ready)`
2. 因此 `/api/auth/status` 与 `/api/v1/auth/status` 在以下字段上共享同一口径来源：
   - `status`
   - `auth_mode`
   - `status_reason`
   - `status_reason_detail`
   - `status_mapping_source`
   - `recovery_failure_count`
   - `rebuild_failed`
   - `rebuild_error_code`
   - `rebuild_failed_reason`
3. 区别只在于：
   - `/api/auth/status` 还包含内部场景需要的额外字段，如 `token_file`、`token_exists`、`token_valid`、`cloud_available`、`login_in_progress`
   - `/api/v1/auth/status` 返回的是正式 v1 envelope，字段更克制、更稳定

结论：
- **状态映射不再由 router 各自拼装**
- **system / v1 / debug 通过 auth manager 共享同一状态映射来源**

---

## 10. 当前实现口径与限制

1. `/api/v1/auth/status` 已是正式端点
2. `map_auth_public_status()` 是统一对外映射入口
3. short-session rebuild 已具备：
   - primary
   - fallback
   - rebind
   - verify
   - 阶段化 debug flow
4. `/api/v1/debug/*` 仍是调试级，不承诺正式兼容
5. 当前只保留最近一次：
   - `last_short_session_rebuild`
   - `last_auth_recovery_flow`
   不保留历史序列
6. fallback 路径已编码实现，但是否命中取决于真实运行环境；应以 debug flow 和日志为准判断

---

## 11. 代码定位

- `xiaomusic/auth.py`
  - `auth_public_status_snapshot()`
  - `map_auth_public_status()`
  - `_try_miaccount_persistent_auth_relogin()`
  - `_try_mijia_persistent_auth_relogin()`
  - `_rebind_runtime_from_auth_data()`
  - `rebuild_short_session_from_persistent_auth()`
  - `auth_short_session_rebuild_debug_state()`
- `xiaomusic/api/routers/v1.py`
  - `GET /api/v1/auth/status`
  - `GET /api/v1/debug/auth_*`
- `xiaomusic/api/routers/system.py`
  - `_build_auth_status_payload()`
  - `GET /api/auth/status`

---

## 12. 验收关注点

若后续继续验收或排查，应重点核对：

1. `/api/v1/auth/status` 是否返回正式字段且口径稳定
2. `status_reason` 是否与实际恢复阶段一致
3. `/api/v1/debug/auth_short_session_rebuild_state` 中：
   - `last_auth_recovery_flow.used_path`
   - `primary_attempt`
   - `fallback_attempt`
   - `rebind`
   - `verify`
   是否与日志一致
4. 恢复成功后：
   - `short_session_available == true`
   - `runtime_auth_ready == true`
   - `status == ok`
5. 恢复失败后：
   - `status_reason == short_session_rebuild_failed`
   - `rebuild_error_code` 与 debug flow 中最终失败码一致
