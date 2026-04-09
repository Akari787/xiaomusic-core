# xiaomusic-core

小爱音箱播放控制核心，聚焦自托管部署、认证运行时恢复与来源扩展能力。

## 1. 项目简介

xiaomusic-core 是基于 [hanxi/xiaomusic](https://github.com/hanxi/xiaomusic) 的独立维护分支，当前核心维护方向是小爱音箱交互、播放控制与认证运行时基础的稳定性。

## 2. 特性概览

**核心能力**

- 统一播放控制：`POST /api/v1/play` 为唯一正式播放入口，所有播放请求经由统一调度链路执行
- 控制面与状态面分离：命令接口与播放状态观测相互独立，状态通过 `GET /api/v1/player/state` 与 SSE 流获取
- 认证运行时恢复：扫码登录后 auth.json 持久化，短期会话失效时自动从长期态重建，无需频繁重新扫码

**来源与扩展**

- 来源扩展层：播放能力通过统一的来源扩展层接入，当前支持直链（direct_url）、本地媒体库（local_library）、Jellyfin、站点媒体（site_media）等

**工程化能力**

- API v1：`/api/v1/*` 为唯一正式对外接口层，统一 envelope 与结构化错误模型
- WebUI：React + TypeScript 前端，默认后端托管构建产物
- 自托管部署：Docker Hub 镜像分发，配套 hardened compose 与安全默认配置（HTTP_AUTH_HASH、SSRF 防护、日志脱敏）

## 3. 快速开始

```bash
# 1. 准备目录
mkdir -p conf music

# 2. 拉取镜像
docker pull akari787/xiaomusic-core:stable

# 3. 启动容器（使用 HTTP_AUTH_PASSWORD 自动生成哈希）
docker run -d --name xiaomusic-core \
  -p 58090:8090 \
  -v $(pwd)/conf:/app/conf \
  -v $(pwd)/music:/app/music \
  -e HTTP_AUTH_PASSWORD='your_password' \
  akari787/xiaomusic-core:stable

# 4. 访问
# http://<HOST>:58090/
```

说明：

- 若提供 `HTTP_AUTH_PASSWORD`，容器启动时会自动生成 bcrypt 哈希并用于运行时认证，无需手动生成
- 若希望使用固定哈希，也可直接提供 `HTTP_AUTH_HASH`（可通过 `python scripts/generate_password_hash.py` 生成）
- 两者都提供时，优先使用 `HTTP_AUTH_HASH`
- 启动后在设置页完成扫码登录即可使用

## 4. 文档导航

| 文档 | 说明 |
|------|------|
| [API v1 规范](docs/api/api_v1_spec.md) | 接口契约、白名单、错误模型、Class A/B/C 分级 |
| [认证系统架构](docs/authentication_architecture.md) | 长期态/短期态、auth.json、恢复链路、调试接口 |
| [认证运行时恢复规范](docs/spec/auth_runtime_recovery.md) | 当前 auth 主线：fresh session `_try_login()`、阶段边界、验收范围 |
| [认证运行时恢复路径规范](docs/spec/auth_runtime_reload_recovery_path.md) | `_try_login()` / runtime reload 的 login、verify、runtime swap 阶段定义 |
| [fresh session 主路径验收收口](docs/implementation/auth_fresh_session_runtime_recovery_acceptance.md) | 当前已确认通过的是哪一层，不是哪一层 |
| [spec rebuild 验收矩阵](docs/implementation/spec_rebuild_acceptance_matrix_2026-04-09.md) | 当前已覆盖 / 未覆盖范围与下一阶段优先级 |
| [播放状态快照规范](docs/spec/player_state_projection_spec.md) | 权威状态快照字段模型与消费约束 |
| [SSE 推送协议](docs/spec/player_stream_sse_spec.md) | 播放状态主通道协议、重连、心跳 |
| [架构说明](ARCHITECTURE.md) | 系统分层、模块职责、调用链 |
| [播放架构](docs/architecture/unified_playback_model.md) | 统一播放模型、来源、上下文、执行路径 |
| [WebUI 状态机规范](docs/spec/webui_playback_state_machine_spec.md) | 前端消费型状态机定义 |
| [Runtime 技术规范](docs/spec/runtime_specification.md) | core 层数据模型、错误体系、Source/Transport 接口 |
| [模块边界](docs/architecture/module_inventory.md) | 模块目录、正式入口、兼容层说明 |
| [Relay 术语](docs/spec/relay_terminology.md) | relay/proxy/delivery mode 术语定义 |

### 当前 auth 主线阅读路径

建议按以下顺序阅读：

1. [认证运行时恢复规范](docs/spec/auth_runtime_recovery.md)
2. [认证运行时恢复路径规范](docs/spec/auth_runtime_reload_recovery_path.md)
3. [fresh session 主路径验收收口](docs/implementation/auth_fresh_session_runtime_recovery_acceptance.md)
4. [spec rebuild 验收矩阵](docs/implementation/spec_rebuild_acceptance_matrix_2026-04-09.md)

其中：

- 第 1、2 篇说明当前主线与阶段边界
- 第 3 篇说明本轮已确认通过的是哪一层
- 第 4 篇说明当前未覆盖的不是哪一层

## 5. Roadmap

- [x] 统一播放入口已收敛到 `POST /api/v1/play`，旧并行播放入口已移除
- [x] `/api/v1/*` 已明确为唯一正式对外接口层，接口白名单、错误模型与分级契约已形成文档约束
- [x] 控制面与状态面已拆分，播放器权威状态通过 `GET /api/v1/player/state` 与 `GET /api/v1/player/stream` 提供
- [x] 认证运行时恢复链路已建立，长期态 / 短期态分层、auth.json 持久化与 runtime rebind 已落地
- [x] WebUI 主流程已围绕 v1 正式控制面持续收敛
- [ ] 核心能力与来源扩展边界仍需继续收敛，减少实现层相互渗透
- [ ] 现有来源能力仍需进一步整理为更清晰的插件范式
- [ ] 播放稳定性与认证恢复可观测性仍需继续加强
- [ ] 自托管部署体验与安全默认配置仍需持续打磨

## 6. 免责声明与致谢

本项目为非官方项目，与小米公司无关。

感谢 [hanxi/xiaomusic](https://github.com/hanxi/xiaomusic) 原作者与社区贡献，以及 Jellyfin 等开源生态支持。

许可证：[MIT](LICENSE)
