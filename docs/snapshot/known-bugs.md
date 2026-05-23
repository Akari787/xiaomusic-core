# 已知 Bug

| ID | 描述 | 复现路径 | 怀疑原因 | 首次发现时间 | 状态 |
|----|------|----------|----------|--------------|------|
| BUG-001 | pending invalidation 依赖前端本地判据组合，若后端存在投影延迟，可能出现边界误差 | WebUI 切换歌单后快速操作 | 状态投影延迟 + 前端乐观更新 | v1.1.1 | 待处理 |
| BUG-002 | 前端 HomePage 状态逻辑偏集中 | WebUI 多设备切换场景 | 状态管理未分离 | v1.1.1 | 待处理 |
| BUG-003 | `disabled_plugins` 当前仍为内存态，不持久化 | 重启后插件恢复启用 | 状态未持久化 | v1.1.0 | 待处理 |
| BUG-004 | 浏览器自动化不稳定 | 自动化测试执行 | 环境依赖 | v1.1.0 | 待处理 |
| BUG-005 | 部分旧 HomePage vitest 漂移 | 运行 vitest | 测试不稳定 | v1.1.0 | 待处理 |
| BUG-006 | M2 P2（Music Service Phase 2）尚未完成 | 播放复杂源 | 功能未实现 | v1.1.0 | 待处理 |
| BUG-007 | auto runtime reload 未完全覆盖 | 认证降级场景 | 实现不完整 | v1.0.10 | ✅ 已修复：`_maybe_scheduled_refresh()` 恢复，接入 keepalive 循环 |
| BUG-008 | singleflight/fallback 未完全覆盖 | 并发认证恢复 | 实现不完整 | v1.0.10 | ✅ 已修复：asyncio.Lock + Event + backoff 完整实现 |
| BUG-009 | playback restart 反复调研 | 播放中断后恢复 | 根因未确定 | 早期版本 | 待处理 |
| BUG-010 | `_persist_auth_data()` 无条件覆盖 `saveTime` 导致24小时掉线 | 长时间运行 | 逻辑缺陷 | v1.0.8 | ✅ 已修复：仅在 token 实际变更时更新 saveTime |
| BUG-011 | stop 后 next/prev 长期显示 switching | 停止后切歌 | 状态机转换问题 | v1.0.9 | ✅ 已修复：facade 层在 is_playing=False 时返回 "stopped" |
| BUG-012 | async EventBus 订阅者未正确调度执行 | 异步事件处理 | 调度逻辑缺陷 | v1.0.9 | ✅ 已修复：`_schedule_async_callback` 使用 `asyncio.ensure_future` |
| BUG-013 | current_index 与 track.title 不一致 | 切歌后状态回写 | 索引同步问题 | v1.0.9 | ✅ 已修复：facade 层在 is_playing=False 时跳过 switching 判定 |

> 数据来源：CHANGELOG.md, TEST_REPORT.md, docs/archive/investigation/*.md
> 更新：2026-05-23 技术债消化批次（BUG-007/008/010/011/012/013 已修复）