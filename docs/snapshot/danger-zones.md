# 雷区

| 文件/模块 | 原因 | 上次出问题的场景 |
|-----------|------|------------------|
| `xiaomusic/core/` | 多版本迭代，核心模块承载了状态管理、生命周期、设备控制等混合职责 | 每次发布都有核心模块重构 |
| `xiaomusic/auth.py` | 认证恢复链复杂，经历多次修复（v1.0.7/v1.0.8/v1.0.10/v1.1.0） | 70016 风控、重启后认证丢失、refresh 放大故障 |
| playback restart 逻辑 | 从 CHANGELOG 看，播放重启问题反复出现，进行了多轮调研（见 docs/archive/investigation/） | v1.0.9/v1.0.14/v1.0.19 多次修复延迟/卡顿/中断恢复 |
| WebUI playlist state | v1.1.1 刚完成 identity 体系整改，之前存在同实体异名、不同实体同名、聚合歌单重复 entry 问题 | v1.1.1 发布前刚修复 |
| player state 投影 | SSE 推送和 state 同步历史上有多个问题（v1.0.9 修复了 revision 语义、transport_state 推导、重复推送） | 修复后又出现新问题 |
| 旧版 API 兼容层 | 经历了多次清理（v1.0.8 清理旧 device wrapper、facade、legacy facade） | 清理不彻底可能导致回归 |

> 数据来源：CHANGELOG.md 各版本 "本版本仍保留的边界" 章节