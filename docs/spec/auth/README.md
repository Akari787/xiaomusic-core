# Auth 相关规范（Auth Specifications）

> 目录用途：存放 auth 模块的协议、接口和行为规范文档。
> 何时读取：改认证相关逻辑前必须先读。
> 当前实现依据：先读架构与主规范；已被替代的阶段文档由 Git 历史保留，不留在现行文档树中。

## 当前阅读顺序

1. `docs/architecture/authentication_architecture.md`：persistent auth、short session、runtime 三层模型。
2. `auth_runtime_recovery.md`：当前 atomic recovery、状态映射与验收规范。
3. `docs/architecture/auth_runtime_recovery.md`：代码级实现与观测字段参考。
4. `auth_recovery_singleflight.md`：后台恢复任务仍适用的局部并发约束。

## 文档层级说明

| 文档 | 定位 | 权威级别 |
|---|---|---|
| `docs/architecture/authentication_architecture.md` | 认证三层状态模型与事务边界 | **核心权威** |
| `auth_runtime_recovery.md` | 当前认证恢复行为与验收规范 | **核心权威** |
| `docs/architecture/auth_runtime_recovery.md` | 当前实现与观测参考 | 当前参考 |
| `auth_recovery_singleflight.md` | 后台恢复任务并发互斥 | 专项参考 |

## 关键约束

- `TokenStore` / `auth.json` 是持久认证事实来源；env credential 只覆盖运行时，禁止持久化。
- 任何认证状态持久化必须通过 `TokenStore` 的原子提交边界。
- runtime candidate 必须 verify 成功后才允许 commit 与 swap。
- 不得绕过 `AuthManager` 直接修改认证状态。

## 相关文档

- 状态权威：`docs/architecture/state-authority.md`
- 系统宪法：`docs/architecture/constraints.md`