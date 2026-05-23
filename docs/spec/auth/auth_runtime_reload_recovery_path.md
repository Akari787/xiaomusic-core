# 认证运行时恢复路径规范

版本：v1.1  
状态：1.0.9 专项约束文档  
最后更新：2026-04-09  
适用范围：`xiaomusic/auth.py` 内 runtime reload / `_try_login()` / runtime rebind / verify 路径的行为边界

---

## 1. 文档目的

本文档描述当前 runtime 恢复主路径的阶段边界、失败分类与可观测性要求。

本文档解决：

- runtime reload / login / verify / runtime swap 的阶段定义
- login-stage failure 与 verify-stage failure 的区分
- 候选 runtime 进入 verify 的前置条件
- 本轮已确认通过的是哪一层，不是哪一层

本文档不解决：

- fallback path 的独立验收
- singleflight 的实机闭环
- auto runtime reload 全量验收
- auth / playback 交叉边界
- spec rebuild 全量结论

---

## 2. 术语与范围

| 术语 | 定义 |
|------|------|
| runtime reload | 从磁盘与当前认证材料出发，重建 runtime 的完整恢复流程 |
| `_try_login()` | 当前 auth runtime 恢复核心路径之一 |
| token load | 读取 token_store / auth.json / 登录输入快照 |
| fresh session login | 使用 fresh `ClientSession` 的 `MiAccount.login("micoapi")` |
| candidate runtime | 本轮登录成功后构建但尚未应用的候选 runtime |
| candidate runtime readiness | 候选 runtime 的 account / cookie ready 判定 |
| verify | 调用 `device_list` 等方式验证候选 runtime 是否可用 |
| runtime swap | 将候选 runtime 原子应用到当前运行态 |
| login-stage failure | 失败发生在 login 或进入 verify 前的准备阶段 |
| verify-stage failure | 登录成功且候选 runtime ready 后，失败发生在 verify 阶段 |

**本文档中的 runtime reload 包含 `_try_login()` 主路径。**

**不包括**：

- short session rebuild 的完整分支验收
- primary / fallback 的全链验收
- manual QR login 本身

---

## 3. 当前已知链路事实

以下为当前仓库与本轮验收口径已确认的现实前提：

1. `_try_login()` 已暴露出明确的阶段化观测字段，包括：
   - `login_result`
   - `token_changed_after_login`
   - `candidate_runtime_account_ready`
   - `candidate_runtime_cookie_ready`
   - `verify_attempted`
   - `verify_error_text`
   - `runtime_swap_attempted`
   - `runtime_swap_applied`

2. 当前实现已经可以区分：
   - login-stage failure
   - verify-stage failure

3. 当前主路径修补点已经收口到：
   - **恢复登录前复用旧 session 会导致 `login_result=false`**
   - 改为 **fresh session login** 后，live 观察已恢复 healthy

4. 本轮 >24h 观察确认的是：
   - fresh session 修补后的 `_try_login()` 主路径稳定

5. 以上结论只覆盖当前主路径，不覆盖 spec rebuild 全量

---

## 4. 当前主路径阶段定义

### 4.1 阶段顺序

当前 runtime 恢复主路径按以下阶段定义：

| 阶段 | 说明 | 关键字段 |
|------|------|----------|
| token load | 重新加载 token_store / auth.json，准备登录输入 | `token_store_reloaded`、磁盘 token 可见性 |
| fresh session login | 使用 fresh `ClientSession` 创建新 `MiAccount`，执行 `login("micoapi")` | `login_result`、`token_changed_after_login` |
| candidate runtime readiness | 检查候选 runtime 的 account / cookie 是否 ready | `candidate_runtime_account_ready`、`candidate_runtime_cookie_ready` |
| verify | 对候选 runtime 执行可用性验证 | `verify_attempted`、`verify_error_text` |
| runtime swap | 仅在 verify 成功后进行原子替换 | `runtime_swap_attempted`、`runtime_swap_applied` |

### 4.2 阶段流转约束

- 只有 `login_result=true`，才允许进入 candidate runtime readiness
- 只有 `candidate_runtime_account_ready=true` 且 `candidate_runtime_cookie_ready=true`，才允许进入 verify
- 只有 verify 成功，才允许 `runtime_swap_applied=true`

---

## 5. 明确的禁止条件

### 5.1 login 失败后不得继续 verify

当：

- `login_result=false`

则：

- `verify_attempted` 必须为 `false`
- `runtime_swap_attempted` 不应进入真正应用阶段
- `runtime_swap_applied` 必须为 `false`

### 5.2 candidate runtime 未 ready 时不得继续 verify

当任一条件成立：

- `candidate_runtime_account_ready=false`
- `candidate_runtime_cookie_ready=false`

则：

- 不得继续进入 verify
- 不得把这类失败伪装成 verify-stage failure

### 5.3 verify 失败时不得污染旧 runtime

当：

- `verify_attempted=true`
- verify 失败

则：

- 候选 runtime 必须被丢弃
- `runtime_swap_applied=false`
- 旧 runtime 必须保持不被污染

---

## 6. 失败分类

### 6.1 login-stage failure

典型特征：

- `login_result=false`，或
- login 虽成功，但 `candidate_runtime_account_ready=false` / `candidate_runtime_cookie_ready=false`

应归类为：

- 登录阶段失败
- 候选 runtime 准备阶段失败

不应归类为：

- verify-stage failure

### 6.2 verify-stage failure

典型特征：

- `login_result=true`
- `candidate_runtime_account_ready=true`
- `candidate_runtime_cookie_ready=true`
- `verify_attempted=true`
- `runtime_swap_applied=false`

语义：

- 登录成功，候选 runtime 具备进入 verify 的条件
- 失败发生在 verify 阶段

### 6.3 当前修补点的现实归因

当前主修补点不是“所有 verify 失败都已解决”，而是：

- 旧行为中恢复登录复用旧 session，可能直接导致 `login_result=false`
- 现在改为 fresh session login 后，主路径已恢复健康

因此，本轮通过的是：

- fresh session 修补后的 `_try_login()` 主路径稳定

不是：

- runtime reload 所有失败都已消失
- verify 全量问题已清零

---

## 7. 可观测性要求

### 7.1 必须稳定暴露的字段

| 字段 | 说明 |
|------|------|
| `login_result` | 登录是否成功 |
| `token_changed_after_login` | 登录后 token 是否变化 |
| `candidate_runtime_account_ready` | 候选 runtime 的 account 是否 ready |
| `candidate_runtime_cookie_ready` | 候选 runtime 的 cookie 是否 ready |
| `verify_attempted` | 是否进入 verify |
| `verify_error_text` | verify 错误文本 |
| `runtime_swap_attempted` | 是否尝试 runtime swap |
| `runtime_swap_applied` | 是否应用 runtime swap |
| `recovery_failure_count` | 恢复失败计数 |

### 7.2 关键约束

- 必须能区分 login-stage failure 与 verify-stage failure
- 必须能判断 verify 是否真正发生
- 必须能判断 verify 失败后是否污染旧 runtime
- 不允许把 fresh session 主路径通过写成 general rebuild 全量通过

---

## 8. 与 auto runtime reload 的边界

- auto runtime reload 只是触发方式
- 不改变 `_try_login()` 主路径的阶段语义
- 当前本轮已确认的是 `_try_login()` 主路径稳定
- **不是 auto runtime reload 全量边界已完成验收**

未覆盖范围仍包括：

- auto trigger 全边界
- singleflight 实机闭环
- fallback 独立验收
- auth / playback 交叉边界
- 极端网络扰动

---

## 9. 当前验收结论边界

### 9.1 已确认通过

- fresh session 修补后的 `_try_login()` 主路径在 >24h 窗口内稳定

### 9.2 适用范围

- 当前主路径
- 当前阶段化观测字段
- login-stage / verify-stage 已可区分这一层

### 9.3 不可外推范围

- spec rebuild 全量通过
- auto runtime reload 全量通过
- singleflight / fallback / cross-boundary 全量通过

---

## 10. 非目标

本文档不解决：

- Xiaomi 云端为什么返回 401
- fallback path 的成功率问题
- manual QR login 交互
- 整个 auth.py 模块拆分
- 大规模重构方案
