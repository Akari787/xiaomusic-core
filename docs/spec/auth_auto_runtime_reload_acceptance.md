# 自动 runtime reload 验收基线

版本：v1.1  
状态：收口文档  
最后更新：2026-04-09  
适用范围：`xiaomusic/auth.py`、`xiaomusic/api/routers/system.py` 的自动触发 runtime reload 验收边界

---

## 1. 文档定位

本文档只定义 **auto runtime reload 相关验收边界**，并同步当前已知通过范围与未覆盖范围。

本文档必须和以下事实同时成立：

1. 当前已经确认通过的，不是 auto runtime reload 全量
2. 当前最硬的通过证据，是 **fresh session 修补后的 `_try_login()` 主路径稳定**
3. auto runtime reload 仍需独立验收，不能借用 `_try_login()` 主路径结论替代

---

## 2. 当前已确认通过的范围

### 2.1 已确认通过

本轮已确认通过的是：

- **fresh session 修补后的 `_try_login()` 主路径稳定性**

已知证据包括：

- >24h 观察窗口内未复发 `login_result=false`
- `candidate_runtime_account_ready` / `runtime_swap_applied` 正常
- `recovery_failure_count` 未异常上涨
- `/api/auth/status` 与 debug 业务结论一致，均指向 healthy

### 2.2 适用范围

以上结论仅适用于：

- `_try_login()` 主路径
- fresh session 修补点
- 当前观察窗口内的主路径稳定性

### 2.3 不可外推范围

以上结论**不等于**：

- auto runtime reload 全量通过
- auto trigger 全边界已通过
- singleflight 已完成实机闭环
- fallback path 已独立通过
- auth / playback 交叉边界已通过
- spec rebuild 全量通过

---

## 3. auto runtime reload 当前验收对象

本文档后续 checklist 只应用于以下对象：

- `init_all_data()` / `keepalive_loop()` 的自动触发
- `degraded + persistent_auth_available=true + short_session_available=true` 时的自动排队
- verify auth failure handoff 的自动分流
- 网络/连接错误时的 cooldown / backoff 行为

---

## 4. 当前未覆盖范围

以下范围当前必须显式视为 **未覆盖**：

1. **auto trigger 全边界**
   - 包括误触发、漏触发、跳过条件、cooldown/backoff 边界

2. **singleflight 实机闭环**
   - 包括 leader / follower / blocked 在自动触发场景下的完整并发样本

3. **fallback 独立验收**
   - 包括 primary / fallback 的边界与结果分类是否在真实服务器上完成闭环

4. **auth / playback 交叉边界**
   - 包括恢复期间发起播放、播放中发生 auth reload / rebind

5. **极端网络扰动**
   - 包括高延迟、抖动、短时断连、外部服务波动

6. **spec rebuild 全量结论**
   - 本文档不是 spec rebuild 总验收文档

---

## 5. 当前行为基线（待独立验收）

### 5.1 登录待恢复

后端语义：

- `auth_mode=degraded`
- `persistent_auth_available=true`
- `short_session_available=true`
- runtime 尚未 ready

这时系统**可能**自动尝试一次 runtime reload。

> 注意：这里描述的是当前设计/实现目标与验收对象，**不是“已全量通过”的结论**。

### 5.2 自动 runtime reload 的触发入口

- `init_all_data()`
- `keepalive_loop()`

### 5.3 自动 runtime reload 的触发条件

- `auth_mode=degraded`
- `persistent_auth_available=true`
- `short_session_available=true`
- 当前没有进行中的同类恢复
- 不在 cooldown / backoff 窗口内

### 5.4 结果分流

- `runtime_auth_ready=true`：进入 `healthy`
- `verify_auth_failure_detected=true`：handoff 到既有 short-session 恢复链
- 网络/连接失败：保留 `degraded`，进入 cooldown / backoff

---

## 6. 真实服务器验收 checklist

### 6.1 自动触发是否发生

- [ ] 扫码登录成功后，`/api/v1/debug/auth_state` 显示 `persistent_auth_available=true` 且 `short_session_available=true`
- [ ] 未手动点击刷新时，`/api/v1/debug/auth_runtime_reload_state` 出现 `auto_runtime_reload_triggered=true`
- [ ] 自动触发来源可见为 `init_all_data` 或 `keepalive`
- [ ] 自动触发结果字段可见且可解释

### 6.2 触发边界是否正确

- [ ] 满足触发条件时会自动排队一次 runtime reload
- [ ] 不满足条件时不会误触发
- [ ] cooldown / backoff 活跃时不会重复触发
- [ ] 跳过原因有明确字段

### 6.3 verify auth failure handoff

- [ ] `verify_auth_failure_detected=true`
- [ ] `short_session_invalidated_after_verify=true`
- [ ] `recovery_chain_handoff=true`
- [ ] `recovery_chain_result` 明确落点可见

### 6.4 网络失败不误触发 auth handoff

- [ ] `verify_error_text` 是网络/连接错误时，不出现 `verify_auth_failure_detected=true`
- [ ] 不出现 `recovery_chain_handoff=true`
- [ ] 自动触发结果进入 cooldown/backoff

### 6.5 长稳验收（后续）

- [ ] 观察到从 `degraded` 自动回到 `healthy` 的真实样本
- [ ] 24h 左右掉线后，尽可能无需人工点击“刷新运行时”即可恢复
- [ ] 只有长期态缺失或硬故障时才需要人工重新登录

---

## 7. 记录要求

每次服务器验收至少保留：

- 刷新前 `auth_state`
- 刷新前 `auth_runtime_reload_state`
- 刷新后 `auth_state`
- 刷新后 `auth_runtime_reload_state`
- 关键日志里 `auto_runtime_reload_*`、`verify_auth_failure_detected`、`recovery_chain_handoff` 相关字段

---

## 8. 结论书写规则

后续任何结论都必须分层书写，至少写清：

- 本次通过的是哪一层
- 本次没有覆盖的不是哪一层
- 使用了哪些字段/日志作为证据
- 观察窗口有多长

固定写法建议：

> 本次通过的是：______  
> 本次未覆盖的不是：______  
> 证据依据：______  
> 观察窗口：______  
> 不可外推到：______
