> ⚠ 本文档基于旧播放模型，仅供历史参考（Historical Only）。

# xiaomusic-core 使用指南

`xiaomusic-core` 是基于 `xiaomusic` 的独立维护分支，默认使用米家扫码认证登录，支持 Jellyfin 联动。

原项目: <https://github.com/hanxi/xiaomusic>

当前仓库: <https://github.com/Akari787/xiaomusic-core>

文档目录: <https://github.com/Akari787/xiaomusic-core/tree/main/docs>

FAQ: [常见问题集合](/issues/99)

## 主要特性

- 认证登录：聚焦米家扫码 + token 持久化 + 运行时恢复
- Jellyfin 搜索与歌单同步
- 优化 `.m4a` 兼容与切歌稳定性

## Docker 使用说明

本仓库当前发布 Docker Hub 镜像：`akari787/xiaomusic-core`。

部署要点：

- 容器内音乐目录：`/app/music`
- 容器内配置目录：`/app/conf`
- 默认服务端口：`8090`
- 认证 Token：`conf/auth.json`

## 安全建议

- 公网访问时请开启控制台认证并使用强密码
- 妥善保管 `conf/auth.json`，避免泄露
- 提交日志前请清理敏感信息

## 贡献

- Issue: <https://github.com/Akari787/xiaomusic-core/issues>
- PR: 欢迎提交代码与文档改进

## AI 开发说明

本项目在开发与维护过程中使用 AI 辅助开发，覆盖代码实现、重构、文档整理与问题排查等环节。
