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

## 用户 / 贡献者入口

| 入口 | 说明 |
|---|---|
| [API v1 规范](api/api_v1_spec) | v1 接口契约、白名单、错误模型 |
| [Architecture 文档地图](architecture/README) | 系统结构、模块边界、约束定义 |
| [ADR 目录](adr/README) | 架构决策记录 |
| [Spec 文档地图](spec/README) | 运行时行为规范（状态语义、SSE、auth 恢复等） |

### AI 开工入口

| 文档 | 说明 |
|---|---|
| [ARCHITECTURE.md](https://github.com/Akari787/xiaomusic-core/blob/main/ARCHITECTURE.md) | AI 开工协议：5 步开工流程、九大边界、约束清单 |
| [Architecture 文档地图](architecture/README) | 架构文档索引 |
| [Spec 文档地图](spec/README) | 规范文档索引（规范 > 架构） |
| [API v1 规范](api/api_v1_spec) | v1 接口契约、白名单、错误模型 |

## 专题入口

| 专题 | 入口文档 |
|---|---|
| auth 认证恢复 | [spec/auth/ 入口](spec/auth/README) |
| playback 播放编排 | [spec/playback/ 入口](spec/playback/README) |
| WebUI playlist 状态 | [架构：webui_playlist_state](architecture/webui_playlist_state) |
| 播放状态快照 | [spec：player_state_projection_spec](spec/player_state_projection_spec) |
| SSE 推送协议 | [spec：player_stream_sse_spec](spec/player_stream_sse_spec) |
| 播放控制模型 | [架构：playback-control-model](architecture/playback-control-model) |
