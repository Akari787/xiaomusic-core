# xiaomusic-core spec rebuild 验收矩阵（基于当前仓库与本轮已知证据）

生成时间：2026-04-09 20:27 GMT+9  
入仓时间：2026-04-09  
生成方式：只基于当前仓库文档、测试文件名/内容、以及本轮已明确给出的 >24h 观察结论整理；**未改代码、未补功能、未新增运行验证**。

---

## 一、结论边界

这份矩阵只回答三件事：

1. **spec rebuild 实际被文档定义了哪些范围**
2. **当前哪些范围有证据、证据属于哪一层**
3. **哪些范围仍未被独立验收**

这份矩阵**不支持**以下结论：

- spec rebuild 全量通过
- auth 全分支恢复链全部通过
- playback 全量长期稳定
- auth 与 playback 交叉边界已完成验收

当前最硬的实机证据，仍然只收口到：

- **fresh session 修补后的 `_try_login()` 路径在 >24h 窗口内稳定**
- `/api/auth/status` 与 debug 业务结论一致，均指向 healthy
- `candidate_runtime_account_ready` / `runtime_swap_applied` 正常
- `recovery_failure_count` 未异常上涨

以上结论**只能落在对应子项上**，不能外推成 spec rebuild 全量通过。

---

## 二、spec rebuild 范围分解

### A. Auth runtime reload / runtime rebind / verify

直接对应的仓库文档：

- `docs/spec/auth_runtime_reload_recovery_path.md`
- `docs/spec/auth_runtime_recovery.md`
- `docs/authentication_architecture.md`
- `docs/spec/auth_auto_runtime_reload_acceptance.md`

实际覆盖的对象包括：

- runtime reload 的入口条件
- runtime reload 与 short session rebuild / primary / fallback 的边界
- runtime seed 建立
- runtime rebind
- verify（device_list / runtime_auth_ready）
- result / error_code 分类

### B. Auto runtime reload 触发与边界

直接对应的仓库文档：

- `docs/spec/auth_auto_runtime_reload_acceptance.md`
- `docs/spec/auth_runtime_reload_recovery_path.md`

实际覆盖的对象包括：

- 自动触发入口：`init_all_data()` / `keepalive_loop()`
- 自动触发条件：`degraded + persistent_auth_available=true + short_session_available=true`
- cooldown / backoff 边界
- verify auth failure handoff 与网络失败不误判边界

### C. Auth recovery singleflight / entrypoint / fallback 边界

直接对应的仓库文档：

- `docs/spec/auth_recovery_singleflight.md`
- `docs/spec/auth_recovery_entrypoint_unification.md`
- `docs/spec/auth_recovery_fallback_path.md`
- `docs/spec/auth_recovery_state_machine.md`

### D. Auth status / debug status / status mapping

直接对应的仓库文档与测试：

- `docs/spec/auth_runtime_recovery.md`
- `docs/authentication_architecture.md`
- `tests/test_api_v1_debug_auth_state.py`
- `xiaomusic/auth.py` 中可见 debug / status 相关字段与 trace 结构

### E. Auth 长窗稳定性

直接对应的仓库文档与测试：

- `docs/spec/auth_auto_runtime_reload_acceptance.md`
- `tests/test_auth_runtime_stability.py`
- 本轮 >24h 观察结论

### F. Playback start confirmation / player state 一致性

直接对应的仓库文档与测试：

- `tests/unit/test_playback_start_confirmation.py`
- `tests/test_api_v1_player_state_contract.py`
- `docs/spec/player_state_projection_spec.md`
- `docs/spec/playback_coordinator_interface.md`

### G. Auth 与 playback 交叉边界

当前范围存在，但缺少本轮独立验收结论。

---

## 三、正式验收矩阵（摘要）

| ID | 子项名称 | 归属层 | 当前状态 | 当前证据 | 适用范围 | 不能外推到 | 下一步动作 |
|---|---|---|---|---|---|---|---|
| SR-01 | runtime reload 路径边界明确 | auth | 部分验证 | 文档已定义阶段边界 | 文档定义与验收基线 | runtime reload 全量通过 | 独立做实机检查表 |
| SR-02 | runtime rebind 关键信号有效 | auth | 部分验证 | `candidate_runtime_account_ready` / `runtime_swap_applied` 正常 | 主路径信号层 | spec rebuild 全量 | 扩到更多场景 |
| SR-03 | verify 阶段归因清晰 | auth | 部分验证 | login-stage / verify-stage 已拆开 | 分段归因能力 | verify 全场景成功 | 补真实样本 |
| SR-04 | auto runtime reload 触发条件 | auth | 未验证 | 仅有 spec baseline | 自动触发行为 | auto runtime reload 全量 | 单列验收 |
| SR-06 | recovery singleflight 并发收敛 | auth | 未验证 | 仅有 singleflight 规范 | 并发恢复互斥 | auth 自动恢复已长期稳定 | 做并发观察 |
| SR-08 | fallback path 边界与结果分类 | auth | 未验证 | 仅有 fallback 规范 | fallback 行为边界 | fallback 已恢复成功 | 单独验 fallback |
| SR-09 | `/api/auth/status` 业务状态正确 | auth | 部分验证 | 本轮 status/debug 一致 | 本轮主路径观察窗 | 所有 auth 状态都已映射 | 补异常态 |
| SR-12 | fresh session `_try_login()` 长窗稳定性 | auth | 已验证 | >24h 观察 + 单测 | fresh session 主路径 | spec rebuild 全量 | 保持独立结论 |
| SR-15 | local_library 首轮 start confirmation | playback | 部分验证 | 局部实机 + 单测约束 | local_library 首轮实机 | playback 长稳 | 补长窗 |
| SR-16 | direct_url 首轮 start confirmation | playback | 部分验证 | 局部实机 | direct_url 首轮实机 | relay/长期稳定 | 单列长时验收 |
| SR-17 | player state 契约与基本稳定性 | playback | 部分验证 | 契约测试 | 契约层与测试层 | 真实播放长窗一致性 | 做实机对账 |
| SR-19 | auth 恢复期间 playback start 行为 | cross-boundary | 未验证 | 当前无独立样本 | 交叉窗口行为 | 整体系统全稳定 | 设计交叉场景 |

---

## 四、本轮已覆盖清单

### 已覆盖

1. **fresh session 修补后的 `_try_login()` 长窗稳定性**
2. **`/api/auth/status` 与 debug 的局部一致性**
3. **`local_library` 首轮 start confirmation**
4. **`direct_url` 首轮 start confirmation**
5. **login-stage / verify-stage 的局部阶段归因能力**

### 适用范围

- 仅限各自子项，不可跨层合并

### 不可外推到

- spec rebuild 全量
- auto runtime reload 全量
- singleflight / fallback / cross-boundary 全量
- auth / playback 全量长期稳定

---

## 五、本轮未覆盖清单

以下内容当前不能写成已通过：

- spec rebuild 全量验收
- auto runtime reload 触发与边界的独立实机验收
- recovery singleflight 的线上并发闭环验证
- recovery entrypoint 统一是否真正收口到同一执行权入口
- fallback path 的独立实机验收
- 极端网络扰动下的恢复行为
- player state 的真实服务器长期一致性
- auth 与 playback 交叉边界
- 其他尚未单独建立观察窗口的 auth / playback 边界

---

## 六、下一阶段优先级

### P1

1. 旧 auth 文档分支路径映射
2. auto runtime reload 触发与边界
3. player state 实机状态面对账

### P2

1. recovery singleflight / entrypoint / fallback
2. auth 与 playback 交叉边界
3. auth recovery 指标长窗稳定性扩展

### P3

1. 多场景长窗稳定性全量
2. 极端网络扰动恢复专项
3. spec rebuild 全量汇总结论
