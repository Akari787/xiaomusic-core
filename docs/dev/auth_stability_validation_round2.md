# Auth Stability Validation Round 2

## 1. 验证背景

- 目标：验证 `miaccount.login("micoapi")` 五段观测链在运行期是否支持稳定恢复与明确归因。
- 五段观测节点：
  1. `login_input_snapshot`
  2. `login_http_exchange`
  3. `login_response_parse`
  4. `token_writeback`
  5. `post_login_runtime_seed`
- 验证重点：
  - 人工重登后短期 token 是否生成
  - 短期 token 在观察窗口内是否稳定
  - 再次掉线是否仍卡在 `login_response_parse`


## 2. 登录基线状态

- 基线检查时间（UTC）：`2026-03-11T22:41:47Z`
- `auth_state`：
  - `auth_mode=locked`
  - `login_at=2026-03-11T22:41:11.408000Z`
  - `expires_at=2026-03-12T22:41:11.408000Z`
  - `ttl_remaining_seconds=86363`
  - `last_auth_error=auth locked, manual relogin required`
- Probe 结果：`code=40004`, `message=device not found`
- Play 结果：`code=40004`, `message=device not found`

结论：本次基线不满足“healthy + probe ok + play success”。


## 3. auth.json token 状态

检查文件：`/root/xiaomusic_conf/auth.json`

- `serviceToken`: `exists=true`, `len=430`
- `yetAnotherServiceToken`: `exists=true`, `len=430`
- `passToken`: `exists=true`, `len=323`
- `ssecurity`: `exists=true`, `len=24`
- `deviceId`: `exists=true`, `len=39`

说明：磁盘侧短期 token 存在，但运行态仍可进入 locked/失败路径。


## 4. login_trace 五段观测

当前 `GET /api/v1/debug/miaccount_login_trace` 最近一次链路：

1. `login_input_snapshot`
   - `result=ok`
   - `token_dict_is_none=true`
   - `has_serviceToken=false`
   - `has_yetAnotherServiceToken=false`

2. `login_http_exchange`
   - `result=ok`
   - `has_set_cookie=false`
   - `has_service_token_cookie=false`
   - `has_yet_another_service_token_cookie=false`

3. `login_response_parse`
   - `result=failed`
   - `parsed_serviceToken=false`
   - `parsed_yetAnotherServiceToken=false`
   - `parse_error_type=refresh_failed`

4. `token_writeback`
   - `result=skipped`
   - `wrote_serviceToken=false`
   - `wrote_target=none`
   - `token_store_flush=no`

5. `post_login_runtime_seed`
   - `result=failed`
   - `runtime_seed_has_serviceToken=false`
   - `runtime_seed_source=unknown`


## 5. 运行期状态变化

日志窗口：`docker logs --since 24h`（可用数据从 `2026-03-11 22:32:08` 到 `2026-03-12 06:42:43`，不足完整 24h）

- `auth_state` 总事件：`31`
- 含 locked 相关事件：`11`
- healthy+ok 事件：`4`
- `70016` 命中次数：`14`

`miaccount_login_trace` 统计：

- `login_input_snapshot ok`: `3`
- `login_http_exchange ok`: `3`
- `login_response_parse failed`: `6`
- `token_writeback skipped`: `3`
- `post_login_runtime_seed failed`: `3`
- `token_writeback ok`: `0`
- `post_login_runtime_seed ok`: `0`

最近一次恢复尝试：`2026-03-12 06:41:13`
- `login_response_parse=failed`
- `token_writeback=skipped`
- `post_login_runtime_seed=failed`


## 6. 是否发生掉线

是。

证据：
- `auth_mode=locked`
- probe/play 均返回 `40004 device not found`
- 恢复链未形成 writeback 和 runtime seed 成功闭环


## 7. 若掉线：恢复链完整分析

从五段链路可还原失败顺序：

1. `login_input_snapshot`: login 前 runtime token 字典为空（无短期 token）
2. `login_http_exchange`: 调用了 login，但未观察到可用 cookie/token 信号
3. `login_response_parse`: 未解析出 `serviceToken/yetAnotherServiceToken`（持续 failed）
4. `token_writeback`: 因无可写回短期 token，写回被 skipped
5. `post_login_runtime_seed`: runtime 无短期 token seed，阶段失败

该链路与“磁盘 auth.json 存在短期 token”并存，说明存在“运行链路拿不到可用短期 token上下文”的场景。


## 8. 最终结论

- 本轮验证未达到“24h 稳定”目标（且实际日志窗口不足 24h）。
- 再次掉线时，观测证据仍指向：
  - **主要卡点：`login_response_parse`**
  - 后续连锁：`token_writeback skipped` -> `post_login_runtime_seed failed`

结论归位：

> 若再次掉线，问题仍然可被明确证明卡在 `login_response_parse`，而不是写回或 runtime 读取阶段先失败。
