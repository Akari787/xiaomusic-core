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
      link: /architecture/module_inventory
    - theme: alt
      text: Spec
      link: /spec/runtime_specification
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

## 当前 auth 主线阅读路径

建议按以下顺序阅读当前 auth 文档：

1. [Auth 运行时恢复链说明](/architecture/auth_runtime_recovery)
   - auth runtime 运行时恢复链——v1 auth status、双路径 rebuild、阶段化 debug 结构
2. [认证运行时恢复规范](/spec/auth_runtime_recovery)
3. [认证运行时恢复路径规范](/spec/auth_runtime_reload_recovery_path)
4. [fresh session 主路径验收收口](/implementation/auth_fresh_session_runtime_recovery_acceptance)
5. [spec rebuild 验收矩阵](/implementation/spec_rebuild_acceptance_matrix_2026-04-09)
6. [v1.1.1 发布说明](/release/v1.1.1)
7. [v1.1.0 发布说明](/release/v1.1.0)
8. [v1.0.10 发布说明](/release/v1.0.10)

当前阅读路径的目的：

- 先理解当前 auth 主线
- 再理解 `_try_login()` / runtime reload 的阶段边界
- 再看本轮已确认通过的是哪一层
- 最后看 spec rebuild 当前哪些仍未覆盖

## 当前 WebUI playlist 状态阅读路径

建议按以下顺序阅读当前 WebUI playlist 状态文档：

1. [WebUI 歌单选择状态架构](/architecture/webui_playlist_state)
   - pendingSelection / effectiveSelection / pending 失效判据 / pending-aware next/previous / `/api/v1/play` snapshot 回包
2. [WebUI 架构](/architecture/webui_architecture)
3. [播放器状态投影规范](/spec/player_state_projection_spec)
4. [播放器 SSE 规范](/spec/player_stream_sse_spec)
5. [v1 API 文档](/api/api_v1_spec)
6. [v1.1.0 发布说明](/release/v1.1.0)

当前阅读路径的目的：

- 先理解 playlist selector 的三层状态模型
- 再回到 WebUI 的通用接口依赖边界
- 最后确认播放状态字段、SSE 契约与 v1 API 对齐关系
