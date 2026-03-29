# 认证恢复入口统一约束规范

版本：v1.0
状态：1.0.9 修复约束文档
最后更新：2026-03-29
适用范围：`xiaomusic/auth.py` 内所有会触发 clear+rebuild / relogin / redirect 的路径

---

## 1. 文档目的

本文档用于解决"恢复入口分裂导致 singleflight 覆盖不完整"的问题。

- 服务于 1.0.9 的最小侵入修复
- 是对 `docs/spec/auth_recovery_state_machine.md` 和 `docs/spec/auth_recovery_singleflight.md` 的补充
- 只新增"恢复入口统一与执行权收口"的行为约束层

---

## 2. 问题背景

当前 `auth_call` 内部已经具备 leader/follower/backoff 机制。

但系统中仍存在多个绕过 `auth_call` 的恢复路径，这些路径会直接触发：

- `_clear_short_lived_session()`
- `_rebuild_short_session_from_persistent_auth()`
- `rebuild_services()`
- `ensure_logged_in(prefer_refresh=True)`

结果是系统级别仍可能出现多个并发 rebuild / redirect。

---

## 3. 术语定义

| 术语 | 定义 |
|------|------|
| 触发入口（trigger entrypoint） | 检测到认证异常并发出恢复请求的路径 |
| 恢复执行入口（recovery execution entrypoint） | 实际执行 clear / rebuild / relogin 的代码路径 |
| 恢复执行权（recovery execution authority） | 独占执行恢复动作的权利，同一时间只应有一个持有者 |
| clear+rebuild | clear short session + rebuild short session + runtime rebind 的组合 |
| relogin / redirect | 通过 persistent-auth 或 refresh 重建会话，可能触发云端请求 |
| singleflight 保护层 | leader/follower/backoff 机制，确保恢复执行权互斥 |
| 最小侵入修复 | 不做大重构，只统一收口恢复执行入口 |

---

## 4. 当前实现中的恢复入口分类

### 4.1 触发侧入口（trigger side）

这些入口可以检测认证异常并发出恢复需求，但不应直接拥有恢复执行权：

| 入口 | 触发方式 |
|------|----------|
| `auth_call` | 检测到 auth error 后进入 suspect / clear+rebuild 流程 |
| `keepalive_loop` | keepalive 失败后调用 `ensure_logged_in(prefer_refresh=True)` |
| `init_all_data` | 检测 short session 缺失后调用 `_rebuild_short_session_from_persistent_auth` |

### 4.2 恢复执行动作（recovery execution actions）

这些动作真正会改变认证状态或触发云端重建：

| 动作 | 说明 |
|------|------|
| `_clear_short_lived_session()` | 清除 short session（runtime 注入态 + auth.json） |
| `_rebuild_short_session_from_persistent_auth()` | 从长期态重建短期态 |
| `_rebuild_short_session_tokens_from_persistent_auth()` | 重建 short session tokens |
| `_rebuild_service_cookies_from_persistent_auth()` | 重建 service cookies |
| `rebuild_services()` | 重新初始化 mina_service / miio_service |
| `ensure_logged_in(prefer_refresh=True)` | 完整的 clear + rebuild + rebind + verify 链 |

**关键区分**："触发恢复"与"执行恢复"不是同一个层次。

---

## 5. 当前问题的根因归纳

- singleflight 当前挂在 `auth_call` 路径，而不是挂在"恢复执行权"这一层
- 因此 `auth_call` 外的恢复路径仍可直接进入 rebuild / relogin
- 这会导致系统级别仍可能存在多个 redirect / rebuild
- **当前问题的根因是"恢复入口统一失败"，不是"singleflight 机制无效"**

---

## 6. 1.0.9 的最小修复目标

1.0.9 不做完整协调器拆分，不做大规模重构，不做已有状态机语义改变。

本次只做一件事：

**凡是会触发 clear+rebuild / relogin / redirect 的路径，都必须经过同一个受 singleflight 保护的执行入口。**

---

## 7. 本次修复的统一约束

### 7.1 触发路径约束

| 路径 | 允许做的事 | 不允许做的事 |
|------|------------|--------------|
| `keepalive` | 触发恢复请求 | 直接绕过统一入口执行 clear+rebuild |
| `init_all_data` | 识别 short session 缺失 | 直接独占执行 rebuild 主链 |
| `getalldevices` / 初始化路径 | 发出恢复需求 | 直接并发触发 persistent-auth relogin |

### 7.2 恢复执行权约束

- singleflight 应保护的是"恢复执行权"，而不只是 `auth_call` 某个调用点
- 任何会触发 redirect / relogin / clear short session 的路径，都必须统一进入 singleflight 保护层
- leader 独占恢复执行权，follower 等待 leader 完成，backoff 阻断失败后立即重入

### 7.3 绝对约束

- follower 不 clear
- follower 不 rebuild
- leader 独占恢复主链
- 失败后有 backoff

---

## 8. 建议的最小实现方向

### 8.1 方向说明

- 后续应抽出一个统一的内部恢复执行入口
- 各触发路径改为调用这个统一入口，而不是直接执行 rebuild
- `auth_call` 保留现有状态机语义，但恢复执行最终应落到统一入口
- keepalive / init_all_data 的最小改动目标是"接入统一入口"，不是各自复制 singleflight 逻辑

### 8.2 不展开的内容

- 不设计完整协调器类
- 不设计大规模模块拆分
- 不改变 Phase A / strong evidence / suspect 的语义

---

## 9. 明确不在本次范围内的事项

本次不做：

- 不拆分完整 `AuthRecoveryCoordinator`
- 不重写 `auth_call`
- 不重写 `ensure_logged_in` 全部职责
- 不改变 Phase A / strong evidence / suspect 状态机定义
- 不引入新的恢复策略分支
- 不做大规模模块重构

---

## 10. 与现有文档的关系

- 本文档补充 `docs/spec/auth_recovery_state_machine.md`
- 本文档补充 `docs/spec/auth_recovery_singleflight.md`
- 状态机语义仍以前两者为准
- 本文档只新增"恢复入口统一与执行权收口"的行为约束层

---

## 11. 验收标准

文档完成后，应能支持维护者准确回答：

1. **为什么 `auth_call` 已有 leader，但系统仍然出现多次 redirect？**
   因为 singleflight 只挂在 `auth_call` 路径，keepalive / init_all_data 等路径绕过了它。

2. **哪些路径只是触发恢复，不应直接执行恢复？**
   keepalive、init_all_data、getalldevices 等触发侧入口。

3. **为什么 singleflight 应保护"恢复执行权"而不是只保护 `auth_call`？**
   因为恢复执行动作（clear / rebuild / relogin）可能从多个入口触发，singleflight 必须保护这些动作的互斥执行。

4. **1.0.9 本次修复为什么是"统一入口"而不是"大重构"？**
   因为当前问题根因是入口分裂，不是架构缺陷；统一入口是最小侵入的有效修复。

5. **后续最小改动时，哪些行为绝不能被破坏？**
   - follower 不 clear、不 rebuild
   - leader 独占恢复主链
   - 失败后有 backoff
   - suspect / Phase A / strong evidence 语义不变
