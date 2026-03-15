# 认证运行时恢复规范

> 最后更新：2026-03-12

## 1. 背景

`xiaomusic-core` 早期曾把两类不同动作混在一条链路里：

- 向 Xiaomi 云端发起会话 refresh
- 从本地 `auth.json` 重载运行时认证状态

在生产环境中，自动调用 `mi_account.login("micoapi")` 经常触发 Xiaomi 风控（如 `70016`），无法稳定写回新的短期会话字段，导致自动恢复链不确定、故障被放大。

因此当前主线将认证恢复链收口为可预测、可观测、可审计的固定流程。

## 2. 三层认证模型

- 长期认证状态（持久化）：`passToken`、`psecurity`、`ssecurity`、`userId`、`cUserId`、`deviceId`
- 短期会话状态（持久化）：`serviceToken`、`yetAnotherServiceToken`
- 运行时状态（内存）：`MiAccount` seed、`mina_service`、`miio_service`、device map

系统始终以 `auth.json` / `TokenStore` 为事实来源。

## 3. 为什么自动登录 fallback 被禁用

- 服务端自动调用 `mi_account.login("micoapi")` 在恢复窗口中不稳定。
- 常见失败表现为 Xiaomi 返回 `70016`，且没有新的短期 token 被成功写回。
- 因此这条路径被策略性禁用，不再作为自动恢复主链。
- 如果命中该分支，日志必须显式记录 `disabled_by_policy=true`。

## 4. 标准恢复链

### 4.1 短期会话失效的自动恢复

唯一允许的顺序：

1. 清理短期会话（`serviceToken`、`yetAnotherServiceToken`）
2. 基于长期认证材料重建短期会话
3. 执行 runtime rebind（`mina_service`、`miio_service`）
4. 执行 verify（`device_list` / `runtime_auth_ready`）

不允许存在隐式替代分支。

### 4.2 扫码登录后的手动恢复

当用户完成扫码登录且新的短期 token 已持久化到磁盘时：

1. 调用 `POST /api/auth/refresh` 或 `POST /api/auth/refresh_runtime`
2. 从磁盘重载运行时认证状态
3. 执行 runtime rebind
4. 刷新 device map
5. 执行 verify

这一过程不需要重启容器。

## 5. Refresh 与 Reload Runtime 的区别

- Refresh：云端会话刷新动作
- Refresh runtime：从本地磁盘重载运行时认证状态，不依赖云端 refresh 成功

当前主线语义：

- `POST /api/auth/refresh` 表示刷新运行时
- `POST /api/auth/refresh_runtime` 是同语义的显式别名

## 6. Locked 策略

`auth locked` 是终态保护状态，只应在长期认证材料缺失或等价硬故障时触发。

以下情况不应直接进入 locked：

- 短期会话失效
- 被策略禁用的自动登录 fallback
- 单次 runtime verify 失败

## 7. 可观测性

运行时重载会输出结构化事件 `auth_runtime_reload`，至少包含：

- `stage=reload_runtime`
- `result`
- `token_store_reloaded`
- `disk_has_serviceToken`
- `disk_has_yetAnotherServiceToken`
- `runtime_seed_has_serviceToken`
- `mina_service_rebuilt`
- `miio_service_rebuilt`
- `device_map_refreshed`
- `verify_result`
- `error_code`
- `error_message`
- `refresh_token_path_invoked`

相关调试接口：

- `GET /api/v1/debug/auth_state`
- `GET /api/v1/debug/auth_recovery_state`
- `GET /api/v1/debug/miaccount_login_trace`
- `GET /api/v1/debug/auth_runtime_reload_state`

## 8. 运维建议

- 保持网络链路稳定、低抖动
- 避免对 Xiaomi 认证流量做不必要的代理或协议改写
- 将 `auth.json` 视为事实来源；扫码登录后应从磁盘重建 runtime，而不是依赖容器内旧内存态

## 9. 已知边界

- Xiaomi 服务端风控无法被彻底消除
- 系统目标不是“零失败”，而是“低频失败、可恢复、可预测”
- playlist / queue / library / object 等 API 扩展不在当前稳定版收口范围内
