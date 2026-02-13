# xiaomusic-oauth2 使用指南

`xiaomusic-oauth2` 是基于 `xiaomusic` 的独立维护分支，默认 OAuth2 登录，支持 Jellyfin 联动。

原项目: <https://github.com/hanxi/xiaomusic>

当前仓库: <https://github.com/Akari787/xiaomusic-oauth2>

文档目录: <https://github.com/Akari787/xiaomusic-oauth2/tree/main/docs>

FAQ: [常见问题集合](/issues/99)

## 主要特性

- OAuth2-only 登录：移除账号密码/cookie 登录路径
- Jellyfin 搜索与歌单同步
- 优化 `.m4a` 兼容与切歌稳定性

## Docker 使用说明

本仓库**未发布 Docker Hub 镜像**，为避免误导，文档不提供任何镜像构建或拉取命令。

仅提供部署要点：

- 容器内音乐目录：`/app/music`
- 容器内配置目录：`/app/conf`
- 默认服务端口：`8090`
- OAuth2 Token：`conf/auth.json`

## 安全建议

- 公网访问时请开启控制台认证并使用强密码
- 妥善保管 `conf/auth.json`，避免泄露
- 提交日志前请清理敏感信息

## 贡献

- Issue: <https://github.com/Akari787/xiaomusic-oauth2/issues>
- PR: 欢迎提交代码与文档改进

## AI 开发说明

本项目在开发与维护过程中使用 AI 辅助开发，覆盖代码实现、重构、文档整理与问题排查等环节。
