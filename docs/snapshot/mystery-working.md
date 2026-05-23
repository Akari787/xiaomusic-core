# 迷之能用功能

| 功能 | 现象 | 为什么不确定 |
|------|------|--------------|
| auth restore 触发条件 | 认证降级时自动触发 runtime reload，但具体触发阈值和时机未完全明确 | 代码中有多个触发路径（`_try_login` 失败、runtime verify 失败、手动 refresh），且有 MiJia fallback 备选路径 |
| SSE 重连逻辑 | 断线后 WebUI 自动重连，但重试策略和最大重试次数未完全文档化 | 重连逻辑分布在 WebUI 和后端 SSE 端点中，断线原因多样（网络、服务器重启、设备切换） |
| playback 状态恢复 | 崩溃后重启时 playback 状态恢复，但恢复依据和完整性未完全确定 | 依赖 Device 状态 + 磁盘持久化，但 Device identity 化是 v1.1.1 刚完成的 |
| 多 source 共存优先级 | 多个 source 同时可用时，如何决定使用哪个 source | 没有明确的优先级策略文档，source plugin manager 基础骨架是 v1.1.0 新增的 |
| runtime auth 重建 | runtime reload 时的认证状态重建行为 | 有双路径（fresh session login + 磁盘重载），verify-stage failure 不得污染旧 runtime 规则 |

> 数据来源：CHANGELOG.md v1.0.7/v1.1.0/v1.1.1 章节