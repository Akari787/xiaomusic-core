# 认证运行时恢复规范

> 最后更新：2026-04-09

## 1. 文档定位

本文档描述 **当前 auth runtime 恢复主线** 的实现语义、阶段边界与验收口径。

本文档解决：

- 当前 auth runtime 恢复主路径是什么
- `_try_login()` 在恢复链中的职责与阶段边界
- 哪些信号可以判断失败发生在 login-stage，哪些发生在 verify-stage
- 本轮已确认通过的是哪一层，不是哪一层

本文档不解决：

- spec rebuild 全量验收
- singleflight 的并发闭环是否已被线上充分证明
- fallback path 的独立验收
- auth / playback 交叉边界
- 极端网络扰动下的全部恢复行为

---

## 2. 当前 auth 主线

当前真实主线已经不是“自动登录 fallback 被禁用，因此不再走服务端登录恢复”这一旧表述。

当前实现中，**`_try_login()` 是 auth runtime 恢复的核心路径之一**。其主线语义为：

1. 从当前可用认证材料出发准备登录输入
2. **恢复登录前使用 fresh `ClientSession`**，避免复用旧 session 导致的 `login_result=false`
3. 调用 `MiAccount.login("micoapi")`
4. **只有 login 成功后，才允许构建候选 runtime**
5. **只有候选 runtime 的 account / cookie ready 后，才允许进入 verify**
6. verify 成功后，才允许进行 runtime swap
7. verify 失败时，候选 runtime 必须被丢弃，**不得污染旧 runtime**

换句话说，当前主路径不再把失败笼统写成“runtime verify failed”或“自动登录 fallback 被禁用”，而是明确分成：

- **login-stage failure**
- **verify-stage failure**

---

## 3. 三层认证模型

- 长期认证状态（持久化）：`passToken`、`psecurity`、`ssecurity`、`userId`、`cUserId`、`deviceId`
- 短期会话状态（持久化）：`serviceToken`、`yetAnotherServiceToken`
- 运行时状态（内存）：`MiAccount` seed、`mina_service`、`miio_service`、device map

系统仍以 `auth.json` / `TokenStore` 为事实来源。

---

## 4. `_try_login()` 主路径阶段定义

### 4.1 阶段顺序

当前 `_try_login()` 主路径按以下阶段理解：

1. **token load**
   - 读取当前可用认证材料，准备登录输入

2. **fresh session login**
   - 创建 fresh `ClientSession`
   - 创建新的 `MiAccount`
   - 调用 `MiAccount.login("micoapi")`

3. **candidate runtime readiness**
   - 仅在 login 成功后继续
   - 检查候选 runtime 的 account/cookie 是否 ready

4. **verify**
   - 仅在候选 runtime ready 后继续
   - 调用 `device_list` 等方式验证 runtime 是否可用

5. **runtime swap**
   - 仅在 verify 成功后允许
   - 原子替换当前 runtime 引用

### 4.2 禁止跨阶段跳跃

- `login_result=false` 时，不得继续进入 verify
- `candidate_runtime_account_ready=false` 时，不得继续进入 verify
- `candidate_runtime_cookie_ready=false` 时，不得继续进入 verify
- verify 失败时，不得把候选 runtime swap 到当前运行态

---

## 5. 当前关键观测字段

当前主路径的关键观测字段至少包括：

- `login_result`
- `token_changed_after_login`
- `candidate_runtime_account_ready`
- `candidate_runtime_cookie_ready`
- `verify_attempted`
- `verify_error_text`
- `runtime_swap_attempted`
- `runtime_swap_applied`
- `recovery_failure_count`

这些字段的基本语义：

| 字段 | 语义 |
|------|------|
| `login_result` | `MiAccount.login("micoapi")` 是否成功 |
| `token_changed_after_login` | login 后 token 快照是否变化 |
| `candidate_runtime_account_ready` | 候选 runtime 的 account 种子是否 ready |
| `candidate_runtime_cookie_ready` | 候选 runtime 的 cookie / token 注入是否 ready |
| `verify_attempted` | 本轮是否真正进入 verify |
| `verify_error_text` | verify 阶段错误文本 |
| `runtime_swap_attempted` | 是否尝试 swap 候选 runtime |
| `runtime_swap_applied` | 是否最终把候选 runtime 应用到当前运行态 |
| `recovery_failure_count` | 恢复失败累计计数 |

---

## 6. login-stage failure 与 verify-stage failure 的区分

### 6.1 login-stage failure

典型特征：

- `login_result=false`
- `verify_attempted=false`
- `runtime_swap_attempted=false`
- `runtime_swap_applied=false`

语义：

- 失败发生在登录阶段
- 本轮不应继续进入 verify
- 不应把这类失败写成 verify 失败

### 6.2 verify-stage failure

典型特征：

- `login_result=true`
- `candidate_runtime_account_ready=true`
- `candidate_runtime_cookie_ready=true`
- `verify_attempted=true`
- `runtime_swap_applied=false`

语义：

- 登录已成功，候选 runtime 已具备进入 verify 的前提
- 失败发生在 verify 阶段
- 旧 runtime 必须保持不被污染

### 6.3 当前实现约束

- **login-stage failure 与 verify-stage failure 必须作为两种不同失败处理**
- 不允许再把二者混写成单一“自动恢复失败”

---

## 7. refresh / refresh_runtime / auto runtime reload 的关系

- `POST /api/auth/refresh`
- `POST /api/auth/refresh_runtime`

这两个入口仍表示手动触发 runtime 恢复。

自动 runtime reload 仍来自：

- `init_all_data()`
- `keepalive_loop()`

但无论手动还是自动触发，**都不应改变 `_try_login()` 主路径的阶段语义**。触发方式与执行阶段需要分开描述。

---

## 8. 本轮已确认通过的范围

本轮已有证据只能支持以下结论：

### 8.1 已确认通过

**fresh session 修补后的 `_try_login()` 主路径稳定性**

在本轮 **>24h 观察窗口** 内，已确认：

- 未复发 `login_result=false` 异常
- `candidate_runtime_account_ready` / `runtime_swap_applied` 正常
- `recovery_failure_count` 未异常上涨
- `/api/auth/status` 与 debug 业务结论一致，均指向 healthy

### 8.2 适用范围

以上结论只适用于：

- fresh session 修补后的 `_try_login()` 主路径
- 当前观察窗口内的主路径稳定性
- login-stage / verify-stage 已可区分这一层

### 8.3 不可外推范围

以上结论**不等于**：

- spec rebuild 全量通过
- auto runtime reload 全量通过
- singleflight 已完成实机闭环
- fallback path 已独立验收通过
- auth / playback 交叉边界已通过
- 极端网络扰动下恢复行为已通过

---

## 9. Locked 策略

`auth locked` 仍是终态保护状态，只应在长期认证材料缺失或等价硬故障时触发。

以下情况不应直接进入 locked：

- 短期会话失效
- 单次 login-stage failure
- 单次 verify-stage failure
- fresh session 主路径中的一次恢复失败

---

## 10. 可观测性

相关调试接口仍包括：

- `GET /api/v1/debug/auth_state`
- `GET /api/v1/debug/auth_recovery_state`
- `GET /api/v1/debug/miaccount_login_trace`
- `GET /api/v1/debug/auth_runtime_reload_state`

后续任何验收结论都应至少写清：

- 本次通过的是哪一层
- 本次没有覆盖的不是哪一层
- 使用了哪些字段作为证据
- 观察窗口有多长

---

## 11. 已知边界

- Xiaomi 云端风控仍不可消除
- 系统目标仍是“低频失败、可恢复、可观测、可预测”，不是零失败
- 当前主路径通过，不等于所有恢复分支通过
- 当前文档描述的是**已收口到的新主线**，不是 spec rebuild 全量结论
