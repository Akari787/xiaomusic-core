# Auth 相关规范（Auth Specifications）

> 目录用途：存放 auth 模块的协议、接口和行为规范文档
> 何时读取：改认证相关逻辑前必须先读
> 是否可作为当前实现依据：是（经审计的规范文档）

## 文档层级说明

| 文档 | 定位 | 权威级别 |
|---|---|---|
| `authentication_architecture.md`（架构层） | 认证两层状态模型（长期态/短期态） | **核心权威** |
| `auth_runtime_recovery.md` | 认证恢复行为规范（恢复链路、状态映射） | **核心权威** |
| `auth_recovery_state_machine.md` | 恢复流程状态机详细说明 | 补充说明 |
| `auth_recovery_entrypoint_unification.md` | 恢复入口统一方案 | 专项验收 |
| `auth_recovery_singleflight.md` | 并发恢复互斥方案 | 专项验收 |
| `auth_recovery_fallback_path.md` | 降级路径方案 | 专项验收 |
| `auth_auto_runtime_reload_acceptance.md` | 自动重载验收标准 | 专项验收 |

## 关键约束

- `auth.json` 是认证状态的唯一事实来源
- 任何认证状态持久化必须通过 `TokenStore`
- 不得绕过 `AuthManager` 直接修改认证状态

## 相关文档

- 状态权威：`docs/architecture/state-authority.md`
- 系统宪法：`docs/architecture/constraints.md`