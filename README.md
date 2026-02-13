# xiaomusic-oauth2

`xiaomusic-oauth2` 是基于 `xiaomusic` 的独立维护分支，目标是提供更稳定的 OAuth2 登录体验与 Jellyfin 联动能力。

原项目: <https://github.com/hanxi/xiaomusic>

当前仓库: <https://github.com/Akari787/xiaomusic-oauth2>

项目文档: <https://github.com/Akari787/xiaomusic-oauth2/tree/main/docs>

FAQ: <https://github.com/Akari787/xiaomusic-oauth2/blob/main/docs/issues/99.md>

## 🙏 致谢

- 本项目基于 [hanxi/xiaomusic](https://github.com/hanxi/xiaomusic) 进行维护与扩展，感谢原作者与社区贡献。
- Jellyfin 能力依赖 Jellyfin 服务接口，感谢其开源生态支持。

## 🚀 分支定位

- OAuth2-only：仅保留 OAuth2 扫码登录，不再维护账号密码/cookie 登录路径。
- Jellyfin 联动：支持在线搜索与歌单同步，适配家庭媒体库场景。

## ✨ 主要改动

### OAuth2 登录改造

- 登录方式统一为 OAuth2 Token 流程。
- Token 默认保存到 `conf/auth.json`。
- 登录状态可在设置页直接查看，便于排障。

### Jellyfin 功能

- 新增 Jellyfin 客户端配置项（地址、API Key、可选用户信息）。
- 在线搜索可聚合 Jellyfin 结果。
- 支持 Jellyfin 歌单同步到本地播放列表。

### 播放稳定性

- 修复 Jellyfin `.m4a` 播放无声问题（统一为可播放链路）。
- 优化播放结束后切下一首的计时触发逻辑，降低竞态问题。

## 🐳 Docker 使用说明

由于本仓库**未发布 Docker Hub 镜像**，请勿直接套用第三方镜像名称。

部署时请注意：

- 容器内音乐目录为 `/app/music`。
- 容器内配置目录为 `/app/conf`。
- 默认服务端口为 `8090`。
- OAuth2 Token 文件默认在 `conf/auth.json`。

## ⚠️ 安全建议

- 若开放公网访问，请务必启用控制台认证并使用强密码。
- 妥善保管 `conf/auth.json`，避免泄露登录凭据。
- 建议通过设置页下载日志后再提交 issue，并先清理敏感信息。

## 🤝 贡献与反馈

- Bug 反馈: <https://github.com/Akari787/xiaomusic-oauth2/issues>
- 文档与功能建议: 欢迎提交 issue / pull request

## 版本

当前维护版本: `1.0.0`

更新记录: <https://github.com/Akari787/xiaomusic-oauth2/blob/main/CHANGELOG.md>

## AI 开发说明

本项目在开发与维护过程中使用了 AI 辅助开发工具，包含代码实现、重构、文档整理与问题排查等环节。

## License

[MIT](LICENSE)
