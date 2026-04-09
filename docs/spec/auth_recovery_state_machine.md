# 认证恢复状态机规范

版本：v1.1  
状态：行为冻结文档  
最后更新：2026-04-09  
适用范围：`xiaomusic/auth.py` 内认证错误判定、恢复链路与状态转移

---

## 1. 文档目的

本文档冻结当前 `auth.py` 恢复状态机的真实行为边界，并同步当前已知验收结论。

本文档解决：

- probe failure / auth error / manual login 之间的语义边界
- suspect / non-destructive recovery / clear+rebuild / runtime reload 的关系
- locked / manual login required 的收紧条件
- 当前 fresh session 修补后的主路径结论属于哪一层

本文档不解决：

- spec rebuild 全量验收
- singleflight 全量实机闭环
- fallback 全量验收
- auth / playback 交叉边界

---

## 2. 范围与非目标

### 2.1 范围

- auth.py 内认证错误判定逻辑
- suspect 状态与升级条件
- non-destructive recovery（Phase A / Phase B）
- clear short session 与 rebuild 流程
- runtime reload / `_try_login()` 主路径与状态转移
- auth_mode：healthy / degraded / locked

### 2.2 非目标

- 不讨论未来大重构方案
- 不替代 `docs/spec/auth_runtime_recovery.md` 的主线路径说明
- 不把局部通过扩写成 spec rebuild 全量通过

---

## 3. 当前恢复链总览

当前自动恢复不是单一路径，而是分层恢复链：

```text
auth_call 捕获 auth error
    ↓
判断 should_clear_short_session
    ├─ first_suspect → non-destructive recovery
    │       ├─ Phase A（基于现有 short session 的 runtime rebuild / verify）
    │       │       ├─ success → 重试原请求
    │       │       └─ strong evidence → 升级到 clear+rebuild
    │       └─ Phase B（轻量 verify）
    │               ├─ success → 重试原请求
    │               └─ failed → 抛出错误，不 clear
    │
    ├─ consecutive_auth_error_* → clear+rebuild（受 singleflight 保护）
    └─ degraded + token available → runtime reload / `_try_login()` 主路径
```

关键区分：

- **probe failure 只是退化触发器，不等于 manual login**
- **进入 degraded 不等于进入 locked**
- **manual_login_required 只应在有强人工介入证据时成立**
- **当前 fresh session 修补已经消除了“复用旧 session 导致 `login(false)`”这一类主断点**
- **以上只覆盖当前主路径，不覆盖全部恢复分支**

---

## 4. 状态定义

### 4.1 healthy

- 语义：认证状态正常，业务请求可正常执行
- 进入条件：恢复链成功完成，runtime 可用
- 退出条件：auth error 或 probe failure 触发退化判定

### 4.2 degraded

- 语义：认证状态异常，部分能力可能不可用，但仍优先尝试自动恢复
- 进入条件：
  - `ensure_logged_in` 失败
  - keepalive / verify / probe 失败
  - runtime reload / `_try_login()` 主路径失败但未达到 locked 条件
- 退出条件：恢复成功后转为 healthy

**重要**：probe failure 只是 degraded 的触发器，不等于 manual login required。

### 4.3 locked

- 语义：认证锁定，需要人工干预（扫码登录）
- 进入条件：长期态必需字段缺失或等价硬故障，自动恢复前提已不成立
- 不应由以下情况直接触发：
  - 单次 probe failure
  - 单次 login-stage failure
  - 单次 verify-stage failure
  - 单次 network error

### 4.4 manual_login_required

`manual_login_required` 的语义已经收紧：

- 只在有**强人工介入证据**时成立
- 例如长期态缺失、locked 已成立、或高层策略已明确要求扫码

以下情况**不应**直接写成 `manual_login_required=true`：

- degraded
- 单次 runtime verify 失败
- `_try_login()` 主路径中的一次失败
- auto runtime reload 的一次失败

---

## 5. auth error 检测与 suspect 机制

### 5.1 错误分类

`auth_call` 捕获异常后的分流仍然是：

| 错误类型 | 处理方式 |
|----------|----------|
| network error | 直接抛出，不进入 auth 恢复主链 |
| 非严格 auth error | 直接抛出，不进入恢复链 |
| 严格 auth error | 进入 suspect 判定与恢复链 |

### 5.2 suspect 语义

suspect 的目的仍然是：

- 避免首次 auth error 就立即 clear short session
- 把首次异常与连续异常区分开
- 为 non-destructive recovery 提供缓冲层

### 5.3 升级条件

- `first_suspect`：优先走 non-destructive recovery
- `consecutive_auth_error_*`：升级到 clear+rebuild
- strong evidence：在同一轮内从 Phase A 升级到 clear+rebuild

---

## 6. non-destructive recovery 规范

### 6.1 Phase A

目标：

- 使用 auth.json 中已有 short session 重建 runtime
- 优先验证“现有 short session 是否还能撑起 runtime”

当前语义：

- success：重试原请求
- verify 失败且 auth failure detected：可升级为 strong evidence
- 失败但证据不足：不立即 clear

### 6.2 Phase B

目标：

- 对当前 runtime 做轻量 verify
- 不修改状态，只做保守验证

当前语义：

- success：重试原请求
- failed：抛错，不 clear，不直接导向 manual login

---

## 7. clear + rebuild 与 runtime reload 的关系

### 7.1 clear + rebuild

clear + rebuild 仍用于：

- short session 已被判定需要清理
- 进入 persistent-auth rebuild 主链

### 7.2 runtime reload / `_try_login()`

runtime reload / `_try_login()` 当前需要按阶段理解：

1. token load
2. fresh session login
3. candidate runtime readiness
4. verify
5. runtime swap

关键约束：

- `login_result=false` 不得继续 verify
- `candidate_runtime_account_ready=false` / `candidate_runtime_cookie_ready=false` 不得继续 verify
- verify 失败不得污染旧 runtime

### 7.3 当前主修补点

当前主修补点已经从“verify 失败”进一步收口到：

- login-stage 与 verify-stage 可以区分
- 恢复场景复用旧 session 会导致 `login(false)`
- 改为 fresh session 后，该类主断点已被消除

但这只代表：

- **fresh session 修补后的 `_try_login()` 主路径已恢复稳定**

不代表：

- 所有 rebuild 分支都已通过
- auto runtime reload 全量通过
- singleflight / fallback / cross-boundary 全量通过

---

## 8. 状态转移口径

### 8.1 degraded → healthy

适用于：

- runtime reload / `_try_login()` 成功
- clear+rebuild 成功
- non-destructive recovery 成功

### 8.2 degraded → locked

只在：

- 长期态缺失
- 或等价硬故障导致自动恢复前提已经不存在

### 8.3 degraded ≠ manual login required

当前口径必须保持：

- degraded 只是“自动恢复仍有机会”的状态
- manual login required 是更高阈值结论

---

## 9. 可观测性与验收用途

后续验收至少应关注：

- `auth_short_session_clear_decision`
- `auth_non_destructive_recovery`
- `auth_recovery_singleflight`
- `auth_runtime_reload`
- `auth_mode_transition`
- `_try_login()` trace 中的：
  - `login_result`
  - `candidate_runtime_account_ready`
  - `candidate_runtime_cookie_ready`
  - `verify_attempted`
  - `verify_error_text`
  - `runtime_swap_attempted`
  - `runtime_swap_applied`

---

## 10. 当前已知边界

1. Xiaomi 云端风控仍不可消除
2. 系统目标仍是可恢复、可观测、可预测，而不是零失败
3. 当前 fresh session 修补只覆盖 `_try_login()` 主路径
4. singleflight 是否已在线上充分闭环，仍需独立验收
5. fallback path 仍需独立验收，不应借主路径结论代替
6. auth / playback 交叉边界仍未纳入本轮通过范围

---

## 11. 与现有文档关系

- 当前 auth runtime 主线路径，以 `docs/spec/auth_runtime_recovery.md` 为主
- 当前 runtime reload / `_try_login()` 阶段边界，以 `docs/spec/auth_runtime_reload_recovery_path.md` 为主
- 当前 auto runtime reload 验收边界，以 `docs/spec/auth_auto_runtime_reload_acceptance.md` 为主
- 本文档负责状态机语义收口，不提供 spec rebuild 总通过结论
