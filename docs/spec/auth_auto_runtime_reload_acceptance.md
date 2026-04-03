# 自动 runtime reload 验收基线

版本：v1.0
状态：收口文档
最后更新：2026-04-03
适用范围：`xiaomusic/auth.py`、`xiaomusic/api/routers/system.py` 的自动恢复登录闭环验收

---

## 1. 目的

本文档固化当前自动恢复登录主线的验收口径：

- 扫码登录成功后，系统能否自动把 runtime 拉回 healthy
- `degraded + persistent_auth_available=true + short_session_available=true` 时，系统能否自动触发 runtime reload
- `verify_auth_failure_detected=true` 时，runtime reload 是否正确 handoff 到既有 short-session 恢复链
- 网络/连接错误时，是否只进入 cooldown/backoff，而不误入 auth handoff

本文档只做收口，不定义新逻辑。

---

## 2. 当前行为基线

### 2.1 登录待恢复

后端语义：

- `auth_mode=degraded`
- `persistent_auth_available=true`
- `short_session_available=true`
- runtime 尚未 ready

这时系统会自动尝试一次 runtime reload，不再只依赖人工点击按钮。

### 2.2 自动 runtime reload 的触发入口

- `init_all_data()`
- `keepalive_loop()`

### 2.3 自动 runtime reload 的触发条件

- `auth_mode=degraded`
- `persistent_auth_available=true`
- `short_session_available=true`
- 当前没有进行中的同类恢复
- 不在 cooldown / backoff 窗口内

### 2.4 结果分流

- `runtime_auth_ready=true`：进入 `healthy`
- `verify_auth_failure_detected=true`：short session 已被证伪，handoff 到既有 short-session 恢复链
- 网络/连接失败：保留 `degraded`，进入 cooldown / backoff，不误判为 auth failure

### 2.5 手动 refresh 语义

- `POST /api/auth/refresh`
- `POST /api/auth/refresh_runtime`

手动入口语义保持不变，和自动触发共享同一 runtime reload 核心链路。

---

## 3. 真实服务器验收 checklist

### 3.1 扫码登录后自动触发

- [ ] 扫码登录成功后，`/api/v1/debug/auth_state` 显示 `persistent_auth_available=true` 且 `short_session_available=true`
- [ ] 未手动点击刷新时，`/api/v1/debug/auth_runtime_reload_state` 出现 `auto_runtime_reload_triggered=true`
- [ ] 自动触发来源可见为 `init_all_data` 或 `keepalive`
- [ ] 最终状态回到 `healthy`

### 3.2 degraded 自动触发 runtime reload

- [ ] 服务器处于 `auth_mode=degraded`
- [ ] `persistent_auth_available=true`
- [ ] `short_session_available=true`
- [ ] 没有同类恢复进行中、没有 cooldown/backoff 时，会自动排队一次 runtime reload
- [ ] `auto_runtime_reload_result` 有明确结果

### 3.3 verify auth failure handoff

- [ ] `verify_auth_failure_detected=true`
- [ ] `short_session_invalidated_after_verify=true`
- [ ] `recovery_chain_handoff=true`
- [ ] `recovery_chain_result` 明确落点可见

### 3.4 网络失败不误触发 auth handoff

- [ ] `verify_error_text` 是网络/连接错误时，不出现 `verify_auth_failure_detected=true`
- [ ] 不出现 `recovery_chain_handoff=true`
- [ ] 自动触发结果进入 cooldown/backoff

### 3.5 长稳验收

- [ ] 观察到从 `degraded` 自动回到 `healthy` 的真实样本
- [ ] 24h 左右掉线后，尽可能无需人工点击“刷新运行时”即可恢复
- [ ] 只有长期态缺失或硬故障时才需要人工重新登录

---

## 4. 记录要求

每次服务器验收至少保留：

- 刷新前 `auth_state`
- 刷新前 `auth_runtime_reload_state`
- 刷新后 `auth_state`
- 刷新后 `auth_runtime_reload_state`
- 关键日志里 `auto_runtime_reload_*`、`verify_auth_failure_detected`、`recovery_chain_handoff` 相关字段
