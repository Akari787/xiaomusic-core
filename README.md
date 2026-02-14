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

### 内网安全部署（默认安全）

- 默认禁用危险能力：`exec#...`（需显式开启并配置白名单）。
- 默认关闭遥测：`enable_analytics=false`。
- 建议启用 HTTP Basic：设置 `disable_httpauth=false` 并配置强口令。
- `conf/auth.json` 仅限容器/宿主可读写（Linux 推荐权限 `600`）。
- Jellyfin 建议使用可被音箱访问的 HTTPS 域名；若是内网地址可启用自动代理降级。
- Docker 推荐最小权限运行：非 root、`read_only`、`cap_drop: [ALL]`、`no-new-privileges`。
- 限制出站网络：如使用 `http_get`，必须配置 `allowlist_domains`；未配置将拒绝。
- 日志默认脱敏：`log_redact=true`，避免 token/api_key 泄露。
- 如需排障，优先导出脱敏日志片段再提交 issue。
- 永远不要把 GitHub Token / Jellyfin API Key / `auth.json` 内容贴到公开渠道。

### 迁移说明（安全默认）

- 之前配置里如包含 `exec#...`（如放在 `user_key_word_dict`），升级后会默认被拦截；需要手动设置 `enable_exec_plugin=true` 并配置 `allowed_exec_commands` 白名单。
- 如需 `http_get`：同时加入 `allowed_exec_commands`，并配置 `allowlist_domains`；未配置域名白名单会拒绝出站请求。
- Token 优先级变更为“环境变量 > `conf/auth.json`”：支持 `OAUTH2_ACCESS_TOKEN` / `OAUTH2_REFRESH_TOKEN`；若这些变量存在，删除 token 文件不会让其失效。
- 如不希望落盘 token：设置 `persist_token=false`，扫码/刷新只会保存到内存（重启后需重新登录）。

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
