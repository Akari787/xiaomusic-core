# 认证运行时恢复路径规范

版本：v1.0
状态：1.0.9 专项约束文档
最后更新：2026-03-29
适用范围：`xiaomusic/auth.py` 内 runtime reload / runtime rebind / verify 路径的行为边界

---

## 1. 文档目的

当前自动恢复失败的主要矛盾已从 short session rebuild 进一步转移到 runtime reload 闭环。

- primary / fallback 路径负责获取 token
- short session rebuild 负责"盘上没有 short session"的场景
- runtime reload 负责"盘上已有 short session，但运行时未恢复"的场景

当前新暴露的主问题：

- UI 显示"登录待恢复"而非"未登录"
- 页面主动作变为"刷新运行时"
- `persistent_auth_available=true`、`short_session_available=true`
- 但 `runtime_seed_has_serviceToken=false`、`mina_service_rebuilt=false`、`verify_result=failed`

本文档解决：
- runtime reload 的定位、入口条件、执行边界、结果分类、可观测性要求

本文档不解决：
- 为什么 Xiaomi 云端返回 401
- short session rebuild 的规范（已有 `auth_recovery_fallback_path.md`）
- QR 登录流程本身
- 整套 auth.py 模块拆分

---

## 2. 术语与范围

| 术语 | 定义 |
|------|------|
| runtime reload | 从磁盘已存在的 auth 数据重建运行时状态的完整流程 |
| runtime seed | 运行时初始化所需的认证种子数据（serviceToken、ssecurity 等） |
| runtime rebind | 重新初始化 mina_service / miio_service 实例 |
| verify | 调用 device_list 验证运行时可用性 |
| manual refresh runtime | 用户手动触发的运行时刷新（`POST /api/auth/refresh_runtime`） |
| 登录待恢复 / login pending recovery | long-lived auth 和 short session 均在，但运行时未 ready 的状态 |
| short session available | 盘上存在 serviceToken 或 yetAnotherServiceToken |
| persistent auth available | 盘上存在长期态字段（passToken、psecurity、ssecurity 等） |
| degraded | 认证状态异常，部分能力可能不可用 |
| locked | 认证锁定，需要人工干预（扫码登录） |

**本文档中的 runtime reload 专指**：`manual_reload_runtime` 及其调用链。

**不包括**：
- short session rebuild（`_rebuild_short_session_from_persistent_auth`）
- primary path（`miaccount_persistent_auth_login`）
- fallback path（`mijia_persistent_auth_login`）
- manual QR login 本身

---

## 3. 当前已知链路事实

以下为本文档的现实前提，不是推测：

1. short session 已在盘上
   - `disk_has_serviceToken=true`
   - `disk_has_yetAnotherServiceToken=true`

2. 但 runtime seed 不完整
   - `runtime_seed_has_serviceToken=false`

3. service 实例未重建成功
   - `mina_service_rebuilt=false`

4. verify 失败
   - `verify_result=failed`

5. 系统状态表现为
   - `auth_mode=degraded`
   - UI 显示"登录待恢复"

6. 当前问题不是 token writeback，而是运行时恢复闭环未完成

---

## 4. runtime reload path 的定位

- runtime reload 不是 short session rebuild
- runtime reload 不是 primary / fallback
- runtime reload 的职责是：
  - 从盘上已存在的 auth 数据重建 runtime
  - 建立 runtime seed
  - 重建 mina_service / miio_service
  - 执行 verify
- runtime reload 不是 manual login 本身
- runtime reload 是"登录后待恢复态"的恢复闭环核心路径

---

## 5. runtime reload 的入口条件

### 5.1 允许进入的条件

| 条件 | 说明 |
|------|------|
| `persistent_auth_available=true` | 长期态仍在 |
| `short_session_available=true` | short session 已在盘上 |
| auth 数据已在盘上 | token_store 可加载 |
| 当前运行时未 ready | `runtime_auth_ready=false` |

当前触发上下文：
- `manual_refresh_runtime`（用户手动点击"刷新运行时"）
- 未来可能自动触发（扫码登录成功后等）

### 5.2 禁止进入的条件

| 条件 | 说明 |
|------|------|
| 缺少 short session | 应走 short session rebuild 路径 |
| 缺少长期态字段 | 应走 primary / fallback 路径或进入 locked |
| 已进入 locked | 需要人工干预 |
| 当前运行时已经 ready | 无需重复 reload |
| short session rebuild 尚未完成 | 不应误入 runtime reload |

---

## 6. runtime reload 的执行边界

### 6.1 可以做的事

| 行为 | 说明 |
|------|------|
| 从磁盘重新加载 auth 数据 | `token_store.reload_from_disk()` |
| 检查 disk token presence | `disk_has_serviceToken` / `disk_has_yetAnotherServiceToken` |
| 建立 runtime seed | 基于盘上 token 初始化运行时认证种子 |
| 重建 mina_service / miio_service | `rebuild_services()` |
| 执行 verify | 调用 device_list 验证 |
| 在成功时进入 healthy | `auth_mode=healthy` |
| 在失败时保留 degraded | 并暴露明确原因 |

### 6.2 不能做的事

| 行为 | 说明 |
|------|------|
| 不得隐式重建 short session | runtime reload 不负责 token 获取 |
| 不得把 short session 缺失伪装成 runtime rebind 失败 | 应用 `short_session_missing_for_runtime_reload` |
| 不得在 runtime seed 不完整时伪装成 runtime ready | 应暴露 `runtime_seed_incomplete` |
| 不得隐式切换成 manual login | runtime reload 失败不等于立即 manual login |
| 不得绕过统一认证状态语义 | 必须通过 `rebuild_services` |
| 不得因为 runtime reload 失败就清掉长期态 | 失败应停在 degraded |

### 6.3 明确的阶段边界

runtime reload 必须拆分为以下阶段，不得混写为一个模糊失败：

| 阶段 | 说明 |
|------|------|
| 磁盘态检查 | `token_store_reloaded`、`disk_has_serviceToken` |
| runtime seed 建立 | 基于盘上 token 初始化认证种子 |
| service 实例重建 | `mina_service_rebuilt`、`miio_service_rebuilt` |
| verify | 调用 device_list 验证运行时可用性 |
| device map refresh | 更新设备信息 |

---

## 7. "登录待恢复"状态定义

### 7.1 状态语义

"登录待恢复"不是"未登录"。

它表示：
- 至少长期态已在
- 可能 short session 已在盘上
- 但运行时尚未 ready

### 7.2 对应后端状态

| 字段 | 典型值 |
|------|--------|
| `auth_mode` | `degraded` |
| `persistent_auth_available` | `true` |
| `short_session_available` | `true` |
| `runtime_rebind_result` | `failed` 或未执行 |
| `verify_result` | `failed` 或未执行 |

### 7.3 UI 显示约束

- UI 显示"刷新运行时"时，后端应处于上述状态
- 以下状态不应显示为"登录待恢复"：
  - `locked`（应显示"需要重新登录"）
  - `persistent_auth_available=false`（应显示"需要重新登录"）
  - `short_session_available=false`（应显示"登录中"或类似）

---

## 8. runtime reload 的结果分类

### 8.1 结果分类表

| error_code | 含义 | 发生阶段 | 允许重试 | 应保持 degraded | 应建议 manual login |
|------------|------|----------|----------|-----------------|---------------------|
| `""` (ok) | 成功恢复运行时 | - | - | 否 | 否 |
| `missing_long_lived_auth_fields` | 长期态字段缺失 | 磁盘检查 | 否 | 是 | 是 |
| `short_session_missing_for_runtime_reload` | short session 缺失 | 磁盘检查 | 否 | 是 | 视情况 |
| `runtime_seed_incomplete` | runtime seed 不完整 | seed 建立 | 短期可重试 | 是 | 否 |
| `runtime_rebind_failed` | service 实例重建失败 | rebind | 短期可重试 | 是 | 否 |
| `runtime_verify_failed` | device_list 验证失败 | verify | 短期可重试 | 是 | 视情况 |
| `RuntimeError` 等 | 未知异常 | 任意阶段 | 短期可重试 | 是 | 否 |

### 8.2 特别区分

- **token 缺失导致不能开始**：`missing_long_lived_auth_fields`、`short_session_missing_for_runtime_reload`
- **token 存在但 seed 不完整**：`runtime_seed_incomplete`
- **service 重建失败**：`runtime_rebind_failed`
- **verify 失败**：`runtime_verify_failed`

---

## 9. runtime reload 与其他路径的边界关系

### 9.1 与 short session rebuild 的边界

- short session rebuild 解决"盘上没有 short session"
- runtime reload 解决"盘上有 short session，但 runtime 未恢复"
- 两者不是同一路径
- 不能混用错误码

### 9.2 与 primary / fallback 的边界

- primary / fallback 负责拿 token
- runtime reload 负责消费盘上已有 token 重建运行时
- primary/fallback 失败不应伪装成 runtime reload 失败
- runtime reload 失败不应反向覆盖 primary/fallback 分类

### 9.3 与 manual login 的边界

- manual login 可更新认证态
- 但 manual login 成功不等于 runtime ready
- runtime reload 是 manual login 后闭环的重要后续阶段
- manual login 本身不属于 runtime reload

### 9.4 与 degraded / locked 的边界

- runtime reload 失败通常应停在 degraded
- 只有长期态缺失或人工干预必要时才讨论 locked
- 不能因为一次 verify 失败就直接进入 locked

---

## 10. 可观测性要求

### 10.1 必须稳定暴露的字段

| 字段 | 说明 |
|------|------|
| `token_store_reloaded` | 是否成功重新加载 token_store |
| `disk_has_serviceToken` | 盘上是否存在 serviceToken |
| `disk_has_yetAnotherServiceToken` | 盘上是否存在 yetAnotherServiceToken |
| `runtime_seed_has_serviceToken` | runtime seed 中是否有 serviceToken |
| `mina_service_rebuilt` | mina_service 是否重建成功 |
| `miio_service_rebuilt` | miio_service 是否重建成功 |
| `device_map_refreshed` | device map 是否刷新成功 |
| `verify_result` | verify 结果（`ok` / `failed`） |
| `error_code` | 错误分类码 |
| `error_message` | 错误描述 |

### 10.2 关键约束

- 不能让"登录待恢复"再次回到黑盒状态
- 必须能区分是 token 缺、seed 缺、service 没建起来、还是 verify 失败
- `runtime_seed_has_serviceToken` 是判断 seed 是否完整的关键指标

---

## 11. 最小侵入修补约束

后续修补应遵守：

| 约束 | 说明 |
|------|------|
| 优先增强 runtime reload 闭环的稳定性与可观测性 | 细化错误分类 |
| 不应大改 auth state machine | 保持 `degraded` / `locked` 语义不变 |
| 不应重写 singleflight | singleflight 约束由 `auth_recovery_singleflight.md` 定义 |
| 不应把 runtime reload 和 short session rebuild 混在一起 | 两者是不同路径 |
| 不应因为修 runtime reload 而破坏 primary/fallback 已收口的行为 | 已收口的错误分类应保留 |
| 不应先改 UI，再倒推后端 | 应先明确后端状态定义 |

可写的后续方向类型（仅作为约束，不是实现）：
- 明确 runtime seed 不完整的稳定错误码
- 明确"登录待恢复"的后端判定
- 明确扫码登录成功后是否应该自动触发 runtime reload
- 明确 verify 失败后的终态

但不要写成具体代码方案。

---

## 12. 非目标

本文档不解决：

- 不解决 Xiaomi 云端为什么返回 401
- 不解决 miservice 依赖内部实现
- 不解决二维码 UI 交互设计
- 不解决整个 auth.py 模块拆分
- 不解决大规模重构方案
