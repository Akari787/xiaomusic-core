# 认证恢复 fallback path 规范

版本：v1.0
状态：1.0.9 专项约束文档
最后更新：2026-03-29
适用范围：`xiaomusic/auth.py` 内 fallback 恢复路径的行为边界

---

## 1. 文档目的

当前自动恢复失败的主要矛盾已从 primary path 转移到 fallback path。

- primary path 已被最小保护性修补收口，稳定命中 `redirect_missing_nonce`
- fallback path（`mijia_persistent_auth_login`）当前稳定失败于 `redirect_http_401`
- 需要单独规范 fallback 路径的行为边界，而不是继续混在 primary / general rebuild 逻辑里讨论

本文档解决：
- fallback path 的定位、入口条件、允许行为、禁止行为、结果分类、可观测性要求

本文档不解决：
- 为什么 Xiaomi 云端返回 401
- miservice 依赖内部实现
- 二维码登录交互设计
- 整套 auth.py 模块拆分

---

## 2. 术语与范围

| 术语 | 定义 |
|------|------|
| primary path | `_try_miaccount_persistent_auth_relogin`，通过 serviceLogin + redirect 重建 short session |
| fallback path | `_try_mijia_persistent_auth_relogin`，通过 MiJiaAPI 另一条长期态恢复链重建 short session |
| persistent auth | 长期态字段：passToken、psecurity、ssecurity、userId、cUserId、deviceId |
| short session | serviceToken、yetAnotherServiceToken |
| rebuild | 从 persistent auth 重建 short session 并写回 auth.json |
| runtime rebind | 重新初始化 mina_service / miio_service |
| verify | 调用 device_list 验证 runtime 可用性 |
| degraded | 认证状态异常，部分能力可能不可用 |
| locked | 认证锁定，需要人工干预（扫码登录） |
| manual login | 用户扫码登录，不属于自动恢复路径 |
| `mijia_persistent_auth_login` | fallback path 的 `used_path` 标识 |

**本文档中的 fallback 专指**：`_try_mijia_persistent_auth_relogin` 及其调用链。

**不包括**：
- manual runtime reload（`POST /api/auth/refresh`）
- manual QR login
- primary path（`miaccount_persistent_auth_login`）

---

## 3. 当前已知链路事实

以下为本文档的现实前提，不是推测：

1. primary path 已被本地短路命中 `redirect_missing_nonce`
   - `serviceLogin` 返回 `code=0`
   - `location` 存在
   - `nonce` 缺失
   - 本地短路为：`error_code="redirect_missing_nonce"`, `failed_reason="service_login_response_missing_nonce"`
   - 不再调用 `_securityTokenService(...)`

2. fallback path 当前仍会继续尝试
   - 在 `_rebuild_short_session_tokens_from_persistent_auth` 中，primary 失败后会调用 fallback

3. fallback path 当前稳定失败于 `redirect_http_401`
   - `used_path="mijia_persistent_auth_login"`
   - `error_code="redirect_http_401"`

4. 系统状态表现为：
   - `auth_mode="degraded"`
   - `persistent_auth_available=true`
   - `short_session_available=false`

5. 当前问题不再是 primary 黑盒异常，而是 fallback 成功率为 0

---

## 4. fallback path 的定位

- fallback path 不是默认主路径
- fallback path 只在 primary 明确失败后才进入
- fallback path 的职责是"尝试以另一条长期态恢复链重建 short session"
- fallback path 不是 runtime reload
- fallback path 不是 manual login
- fallback path 不是最终兜底的人机登录流程

**它是"受控二级尝试"，不是"无限兜底"。**

---

## 5. fallback path 的入口条件

### 5.1 允许进入 fallback 的条件

| 条件 | 说明 |
|------|------|
| primary path 已执行并失败 | `_try_miaccount_persistent_auth_relogin` 返回 `ok=false` |
| primary 失败不是 `missing_persistent_auth_fields` | 长期态字段仍存在 |
| 当前仍有 `persistent_auth_available=true` | 长期态仍可用于恢复 |
| 当前 `short_session_available=false` | short session 仍缺失 |
| 当前调用仍在统一恢复执行入口内 | 受 singleflight 保护 |

### 5.2 禁止进入 fallback 的条件

| 条件 | 说明 |
|------|------|
| `missing_persistent_auth_fields` | 长期态字段缺失，fallback 也无法工作 |
| `locked` 状态 | 已进入锁定，需要人工干预 |
| 已进入 manual login 流程 | 不应重复 fallback |
| primary 已成功 | 不需要 fallback |
| 不在统一恢复执行入口内 | 可能绕过 singleflight |

### 5.3 触发上下文

当前调用上下文可能来自：
- `init_all_data`
- `init_all_data_verify_failed`
- `keepalive_auto_recover`
- `keepalive_proactive_recovery`
- `auth_call` clear+rebuild 分支

---

## 6. fallback path 的执行边界

### 6.1 可以做的事

| 行为 | 说明 |
|------|------|
| 尝试 `mijia_persistent_auth_login` | 通过 MiJiaAPI 重建 short session |
| 尝试长期态到 short session 的受控恢复 | 使用 persistent auth 重建 service cookies |
| 记录 `used_path` | 标识为 `mijia_persistent_auth_login` |
| 记录 `error_code` / `failed_reason` | 记录失败原因分类 |
| 在成功时写回 short session | 写入 auth.json |
| 在成功后允许进入 runtime rebind / verify | 继续后续恢复步骤 |

### 6.2 不能做的事

| 行为 | 说明 |
|------|------|
| 不得自行 clear long-lived auth | 只处理 short session，不动长期态 |
| 不得绕过 singleflight | 必须在统一恢复执行入口内 |
| 不得直接把失败静默吞掉 | 必须明确记录失败结果 |
| 不得在失败后伪造 runtime 已恢复 | 不得误标记 `short_session_available=true` |
| 不得隐式切换成 manual login | fallback 失败不等于立即 manual login |
| 不得修改 primary 的失败语义 | primary 的 `path_attempts[0]` 应保留 |

### 6.3 明确的前后边界

| 阶段 | 边界 |
|------|------|
| fallback 前 | primary 已失败 |
| fallback 中 | 只能处理 short session 重建 |
| fallback 后（成功） | 才能进入 runtime rebind / verify |
| fallback 后（失败） | 应明确结束为 degraded 或进入更高层决策，不得假装成功 |

---

## 7. fallback 的结果分类

### 7.1 结果分类表

| error_code | 含义 | 是否上游拒绝 | 允许重试 | 应进入 degraded | 应建议 manual login |
|------------|------|--------------|----------|-----------------|---------------------|
| `""` (ok=true) | 成功重建 short session | 否 | - | 否 | 否 |
| `redirect_http_401` | redirect 请求返回 401 | 是 | 短期不应立即重试 | 是 | 视情况 |
| `refresh_failed` | refresh token 失败 | 是 | 短期不应立即重试 | 是 | 视情况 |
| `missing_persistent_auth_fields` | 长期态字段缺失 | 否（本地判断） | 否 | 是 | 是 |
| `manual_login_required` | 需要人工扫码登录 | 否（本地判断） | 否 | 是 | 是 |
| `service_token_not_written` | 重建成功但未写回 | 否（本地异常） | 短期可重试 | 是 | 否 |
| `runtime_rebind_failed` | runtime 重新绑定失败 | 否（本地异常） | 短期可重试 | 是 | 否 |
| `verify_failed` | device_list 验证失败 | 视具体错误 | 视具体错误 | 是 | 视情况 |

### 7.2 特别区分

- **前段重建失败**：`redirect_http_401`、`refresh_failed`、`missing_persistent_auth_fields`
- **写回后 runtime 失败**：`runtime_rebind_failed`、`verify_failed`
- **manual login 才能继续**：`manual_login_required`、`missing_persistent_auth_fields`

---

## 8. fallback 与其他路径的边界关系

### 8.1 与 primary 的边界

- primary 失败后才进入 fallback
- primary 的本地短路错误不应再被 fallback 覆盖掉语义
- primary 的 `path_attempts[0]` 应保留，作为诊断信息

### 8.2 与 runtime reload 的边界

- runtime reload 依赖现有 short session
- fallback 用于"short session 已缺失但 persistent auth 仍在"的场景
- 两者不是同一路径

### 8.3 与 manual login 的边界

- fallback 失败不等于立刻 manual login
- 但某些失败分类（如 `missing_persistent_auth_fields`）可指向 manual login required
- manual login 是更高层兜底，不属于 fallback 内部动作

### 8.4 与 degraded / locked 的边界

- fallback 失败后通常停在 degraded
- 缺少 persistent auth 时才可能进入 locked
- 不能混淆这两者

---

## 9. fallback path 的可观测性要求

### 9.1 必须暴露的字段

| 字段 | 稳定性要求 | 说明 |
|------|------------|------|
| `used_path` | 必须稳定 | 标识为 `mijia_persistent_auth_login` |
| `error_code` | 必须稳定 | 失败分类码 |
| `failed_reason` | 必须稳定 | 人类可读失败原因 |
| `writeback_target` | 必须稳定 | 写回目标（`auth_json` 或 `none`） |
| `runtime_rebind_result` | 必须稳定 | runtime 重新绑定结果 |
| `verify_result` | 必须稳定 | verify 验证结果 |
| `path_attempts` | 必须稳定 | 尝试过的路径列表（包含 primary 和 fallback） |
| `diagnostic` | best effort | 扩展诊断信息 |

### 9.2 关键约束

- 不能让 fallback 再回到"黑盒失败"
- `path_attempts` 必须包含 primary 和 fallback 的完整尝试记录
- `error_code` 必须能区分"云端拒绝"与"本地异常"

---

## 10. 最小侵入修补约束

后续修补应遵守：

| 约束 | 说明 |
|------|------|
| 优先增强 fallback 的可观测性与分类稳定性 | 细化 `error_code` / `failed_reason` |
| 不应大改 auth state machine | 保持 `degraded` / `locked` 语义不变 |
| 不应重写 singleflight | singleflight 约束由 `auth_recovery_singleflight.md` 定义 |
| 不应把 fallback 和 manual login 混在一起 | manual login 是独立的高层兜底 |
| 不应因为修 fallback 而破坏 primary 已收口的行为 | primary 的 `redirect_missing_nonce` 短路应保留 |

---

## 11. 非目标

本文档不解决：

- 不解决 Xiaomi 云端为什么返回 401
- 不解决 miservice 依赖内部实现
- 不解决二维码登录交互设计
- 不解决整套 auth.py 模块拆分
- 不解决大规模重构方案
