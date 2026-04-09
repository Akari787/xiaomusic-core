# fresh session `_try_login()` 主路径验收收口

最后更新：2026-04-09  
适用范围：fresh session 修补后的 `_try_login()` 主路径

---

## 1. 本轮已确认通过范围

本轮已确认通过的是：

- **fresh session 修补后的 `_try_login()` 主路径稳定性**

当前证据：

- >24h 观察窗口内未复发 `login_result=false`
- `candidate_runtime_account_ready` / `runtime_swap_applied` 正常
- `recovery_failure_count` 未异常上涨
- `/api/auth/status` 与 debug 业务结论一致，均指向 healthy

---

## 2. 当前主路径结论

当前主路径结论是：

- `_try_login()` 是当前 auth runtime 恢复核心路径之一
- 恢复登录前改为使用 fresh `ClientSession`
- 这一修补已经消除了“复用旧 session 导致 `login_result=false`”这一类主断点
- 当前 live 观察内主路径已恢复 healthy

---

## 3. 本轮未覆盖范围

本轮未覆盖的包括：

- spec rebuild 全量
- auto runtime reload 全量
- singleflight 实机闭环
- fallback 独立验收
- auth / playback 交叉边界
- 极端网络扰动
- 其他未单独建立观察窗口的 auth / playback 边界

---

## 4. 不可扩写范围

本轮结论**不可扩写**为：

- spec rebuild 全量通过
- auth 全部分支恢复链通过
- auto runtime reload 已全部稳定
- playback / cross-boundary 已完成验收

---

## 5. 固定结论写法

推荐后续统一写成：

> 本次通过的是：fresh session 修补后的 `_try_login()` 主路径稳定性。  
> 本次未覆盖的不是：spec rebuild 全量、auto runtime reload 全量、singleflight/fallback/cross-boundary 全量。  
> 证据依据：`login_result`、`candidate_runtime_account_ready`、`runtime_swap_applied`、`recovery_failure_count`、`/api/auth/status` 与 debug 对账。  
> 观察窗口：>24h。  
> 不可外推到：所有未单独验收的 auth / playback 子项。
