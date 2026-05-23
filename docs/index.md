---
layout: home

hero:
  name: "xiaomusic-core"
  text: "Auth + Jellyfin"
  tagline: 独立维护核心分支，专注稳定播放、自托管与认证恢复体验
  actions:
    - theme: brand
      text: API
      link: /api/api_v1_spec
    - theme: alt
      text: Architecture
      link: /architecture/
    - theme: alt
      text: Spec
      link: /spec/
    - theme: alt
      text: GitHub
      link: https://github.com/Akari787/xiaomusic-core

features:
  - title: MIT 开源
    details: 完全开源，自主可控
  - title: 认证恢复
    details: 使用米家扫码登录、token 持久化与运行时恢复机制
  - title: Jellyfin 联动
    details: 支持 Jellyfin 搜索与歌单同步
---

## 深度体检

[执行方案](/plan/xiaomusic-core_深度体检与长期演化执行方案_v2)

当前进度：阶段 5（已完成）

产出文档索引：
- [Day 0 现状快照](/snapshot/known-bugs) — 已知 bug、迷之能用、雷区
- [架构审计报告](/architecture/runtime-dependency) — 运行时依赖、状态流、生命周期
- [边界审计报告](/architecture/source-system) — Source 系统、Runtime 边界、API 契约
- [状态权威偏差表](/architecture/state-authority) — 状态权威、质量门禁
- [可观测性设计](/architecture/event-model) — 统一事件模型、Correlation ID、Snapshot 端点
- [ADR 目录](/adr/) — 5 个架构决策记录
- [系统宪法](/architecture/constraints) — 禁止清单、必须清单
- [AI Review Checklist](/ai-review-checklist) — 提交前检查

---

## AI 开工入口

| 文档 | 说明 |
|---|---|
| [ARCHITECTURE.md](https://github.com/Akari787/xiaomusic-core/blob/main/ARCHITECTURE.md) | AI 开工协议：5步开工流程、九大边界、约束清单 |
| [Architecture 文档地图](architecture/README) | 架构文档索引 |
| [Spec 文档地图](spec/README) | 规范文档索引（规范 > 架构） |
| [API v1 规范](api/api_v1_spec) | v1 接口契约、白名单、错误模型 |
| [AI Review Checklist](ai-review-checklist) | 提交前检查清单 |

## 专题入口

| 专题 | 入口文档 |
|---|---|
| auth 认证恢复 | [spec/auth/ 入口](spec/auth/README) |
| playback 播放编排 | [spec/playback/ 入口](spec/playback/README) |
| WebUI playlist 状态 | [架构：webui_playlist_state](architecture/webui_playlist_state) |
| 播放状态快照 | [spec：player_state_projection_spec](spec/player_state_projection_spec) |
| SSE 推送协议 | [spec：player_stream_sse_spec](spec/player_stream_sse_spec) |
