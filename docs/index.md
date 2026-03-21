---
# https://vitepress.dev/reference/default-theme-home-page
layout: home

hero:
  name: "xiaomusic-core"
  text: "Auth + Jellyfin"
  tagline: 独立维护核心分支，专注稳定播放、自托管与认证恢复体验
  actions:
    - theme: brand
      text: 使用指南
      link: /issues/index
    - theme: alt
      text: API v1 规范
      link: /api/api_v1_spec
    - theme: alt
      text: 认证架构
      link: /authentication_architecture
    - theme: alt
      text: 开发文档
      link: /dev/index
    - theme: alt
      text: 规范文档
      link: /spec/relay_terminology
    - theme: alt
      text: FAQ
      link: /issues/99
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
