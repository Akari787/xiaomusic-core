# xiaomusic-oauth2

`xiaomusic-oauth2` 是基于 `xiaomusic` 二次开发的独立维护分支，目标是提供更稳定的 OAuth2 登录体验与 Jellyfin 联动能力。

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
- Jellyfin 播放默认“先直连，失败自动走代理（/proxy）”，无需在 UI 里手动切换。

### 播放稳定性

- 修复 Jellyfin `.m4a` 播放无声问题（统一为可播放链路）。
- 优化播放结束后切下一首的计时触发逻辑，降低竞态问题。
- 新增统一链接播放策略层（`xiaomusic/playback/link_strategy.py`），Jellyfin 自动降级与网络音频入口复用同一套 URL 判定/规范化能力。
- 默认主题“播放测试”已整合为单一“播放链接”入口：同一输入框可直接处理普通音频链接、B 站与 YouTube 链接。
- 网络视频/直播播放统一通过站内路径 `/network_audio/stream/{sid}` 回放，不再需要额外暴露独立转流端口。

### API 收敛说明

- 控制面接口已统一为 `/api/v1/*`。
- **Breaking Change**：已移除 `/network_audio/play_url`、`/network_audio/play_link`、`/network_audio/stop`。
- 媒体流回放路径保留为 `/network_audio/stream/{sid}`。

### API v1 调用示例

```bash
curl -X POST http://<HOST>:<PORT>/api/v1/play_url \
  -H "Content-Type: application/json" \
  -d '{"speaker_id":"<SPEAKER_ID>","url":"https://example.com/test.mp3"}'

curl -X POST http://<HOST>:<PORT>/api/v1/stop \
  -H "Content-Type: application/json" \
  -d '{"speaker_id":"<SPEAKER_ID>"}'

curl "http://<HOST>:<PORT>/api/v1/status?speaker_id=<SPEAKER_ID>"
```

### API 详细说明

为避免 README 过长，完整 API 文档已独立维护在：`docs/API.md`

## 🐳 Docker 使用说明

本仓库已发布 Docker Hub 镜像：`akari787/xiaomusic-oauth2`（多架构：`linux/amd64`、`linux/arm64`、`linux/arm/v7`）。

推荐标签：

- `stable`：稳定版（推荐）
- `latest`：最新构建
- `v1.0.2`：指定版本

快速启动（示例）：

```bash
docker pull akari787/xiaomusic-oauth2:stable

docker run -d --name xiaomusic-oauth2 \
  -p 58090:8090 \
  -v /root/xiaomusic_conf:/app/conf \
  -v /root/xiaomusic_music:/app/music \
  akari787/xiaomusic-oauth2:stable
```

容器路径与端口约定：

- 音乐目录：`/app/music`
- 配置目录：`/app/conf`
- 服务端口：容器内 `8090`（示例映射到宿主 `58090`）
- OAuth2 Token：默认 `conf/auth.json`

可选：如需设置时区，可添加例如 `-e TZ=Asia/Tokyo`。

更安全的部署方式：参考 `docker-compose.hardened.yml`（`read_only`、`cap_drop`、`no-new-privileges` 等）。

运行进程模型约束：必须单 worker（`workers=1`）。
原因：`TokenStore/conf/auth.json` 仅提供单进程并发安全，多 worker 会导致 token 写入覆盖/回滚并引发掉线。

### docker compose 示例

1) 使用仓库内置 hardened compose（推荐）：

```bash
mkdir -p conf music
docker compose -f docker-compose.hardened.yml up -d --build
```

2) 使用 profile 化 compose（production/test）：

```bash
# 先准备 .env（必须包含 API_SECRET 与 HTTP_AUTH_HASH）
python scripts/generate_password_hash.py

# 生产
docker compose --profile production up -d

# 测试
docker compose --profile test up -d
```

3) 使用 Docker Hub 镜像（不本地 build）：

```yaml
services:
  xiaomusic:
    image: akari787/xiaomusic-oauth2:v1.0.5
    container_name: xiaomusic-oauth2
    restart: unless-stopped
    ports:
      - "58090:8090"
    environment:
      TZ: Asia/Shanghai
      XIAOMUSIC_PUBLIC_PORT: 58090
    volumes:
      - ./conf:/app/conf
      - ./music:/app/music
```

```bash
docker compose up -d
```

4) 常用运维命令：

```bash
# 查看版本
curl -fsS http://127.0.0.1:58090/getversion

# 查看日志
docker logs --tail 200 xiaomusic-oauth2

# 更新到新版本镜像（示例 v1.0.5）
docker compose pull
docker compose up -d --force-recreate
```

## ⚠️ 安全建议

### 内网安全部署（默认安全）

- 默认禁用危险能力：`exec#...`（需显式开启并配置白名单）。
- 默认关闭遥测：`enable_analytics=false`。
- 建议启用 HTTP Basic：设置 `disable_httpauth=false` 并配置强口令。
- 从 `v1.0.5` 起建议改为哈希认证：设置 `HTTP_AUTH_HASH`（bcrypt），不再使用明文密码比对。
- `conf/auth.json` 仅限容器/宿主可读写（Linux 推荐权限 `600`）。
- Jellyfin 建议使用可被音箱访问的 HTTPS 域名；若是内网地址可启用自动代理降级。
- Docker 推荐最小权限运行：非 root、`read_only`、`cap_drop: [ALL]`、`no-new-privileges`。
- 限制出站网络：默认拒绝出站；如使用网络歌单/抓取/`http_get`，需配置 `outbound_allowlist_domains`。
- CORS 默认收紧：只允许 localhost；如经由 Nginx/HA 域名访问需加入 `cors_allow_origins` 白名单。
- 日志默认脱敏：`log_redact=true`，避免 token/api_key 泄露。
- 如需排障，优先导出脱敏日志片段再提交 issue。
- 永远不要把 GitHub Token / Jellyfin API Key / `auth.json` 内容贴到公开渠道。

### 配置迁移与字段优先级

- 出站访问（SSRF 防护）：默认拒绝所有出站请求。
- 域名白名单优先级：`outbound_allowlist_domains` > `allowlist_domains`（后者保留兼容但视为 deprecated）。
- CORS 默认仅允许 `http://localhost`、`http://127.0.0.1`。
- 启动必须注入以下环境变量（支持 `.env`）：`API_SECRET`、`HTTP_AUTH_HASH`。
- Self-update 默认关闭：`enable_self_update=false`。

### 迁移说明（安全默认）

- 之前配置里如包含 `exec#...`（如放在 `user_key_word_dict`），升级后会默认被拦截；需要手动设置 `enable_exec_plugin=true` 并配置 `allowed_exec_commands` 白名单。
- 如需 `http_get`：同时加入 `allowed_exec_commands`，并配置 `outbound_allowlist_domains`。

示例：允许 `example.com`

```json
{
  "enable_exec_plugin": true,
  "allowed_exec_commands": ["http_get"],
  "outbound_allowlist_domains": ["example.com"]
}
```
- 如需通过反向代理访问 WebUI：配置 `cors_allow_origins`，否则浏览器可能被 CORS 拦截。
- Jellyfin API Key 不再在设置页明文显示；`/getsetting` 也会返回脱敏值（`******`）。需要修改时请重新输入。
- 口令合并模式新增：`keyword_override_mode` 默认 `override`（同名口令冲突时以用户自定义为准）；可改为 `append` 保留默认口令。
- Token 优先级变更为“环境变量 > `conf/auth.json`”：支持 `OAUTH2_ACCESS_TOKEN` / `OAUTH2_REFRESH_TOKEN`；若这些变量存在，删除 token 文件不会让其失效。
- 如不希望落盘 token：设置 `persist_token=false`，扫码/刷新只会保存到内存（重启后需重新登录）。
- 掉线自愈策略：认证错误优先走 OAuth2 refresh，再重建会话；仅在 refresh 明确失败时才回退账号登录。
- 新增手动刷新接口：`POST /api/oauth2/refresh`。
- 新增可调参数：
  - `OAUTH2_REFRESH_INTERVAL_HOURS`（默认 `12`）
  - `OAUTH2_REFRESH_MIN_INTERVAL_MINUTES`（默认 `30`）
  - `XIAOMUSIC_MINA_HIGH_FREQ_MIN_INTERVAL_SECONDS`（默认 `8`）
  - `XIAOMUSIC_MINA_AUTH_FAIL_THRESHOLD`（默认 `3`）
  - `XIAOMUSIC_MINA_AUTH_COOLDOWN_SECONDS`（默认 `600`）

## 🤝 贡献与反馈

- Bug 反馈: <https://github.com/Akari787/xiaomusic-oauth2/issues>
- 文档与功能建议: 欢迎提交 issue / pull request

## 版本

当前维护版本: `1.0.5`

更新记录: [CHANGELOG.md](CHANGELOG.md)

## AI 开发说明

本项目在开发与维护过程中使用了 AI 辅助开发工具，包含代码实现、重构、文档整理与问题排查等环节。

## License

[MIT](LICENSE)
