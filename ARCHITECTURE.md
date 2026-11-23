# xiaomusic-core 架构入口

> 状态：现行入口
> 最后核对：2026-11-22

xiaomusic-core 是面向小米音箱的 Python 后端，负责播放编排、设备控制、认证、媒体来源解析、relay 与 WebUI 服务。

## AI / 贡献者开工顺序

1. 读 `docs/architecture/system_overview.md`，确认改动属于哪个边界。
2. 读 `docs/architecture/constraints.md`，确认现行硬约束。
3. 涉及 API 时读 `docs/api/api_v1_spec.md`。
4. 涉及状态、生命周期或跨层依赖时读 `docs/architecture/state-authority.md` 及对应 `docs/spec/*`。
5. 涉及播放、设备或流地址时先读 `docs/adr/0007-speaker-owned-queue.md`（正在分步实施的播放架构），再读 `docs/architecture/playback-control-model.md`（现行逐首路径）。
6. 提交前按 `docs/architecture/contributor_guide.md` 运行受影响测试和生产装配门禁。

## 九个一级边界

| 边界 | 核心职责 | 入口 |
|---|---|---|
| api | Public / Admin / Internal HTTP 边界 | `xiaomusic/api/` |
| runtime | 生命周期与依赖装配 | `xiaomusic/xiaomusic.py` |
| playback | 播放编排、控制意图、状态投影 | `xiaomusic/playback/`、`xiaomusic/core/` |
| source | 媒体事实解析 | `xiaomusic/adapters/sources/`、`xiaomusic/core/source/` |
| device | 设备命令与当前 session 队列 | `xiaomusic/device_manager.py`、`xiaomusic/device_player.py`（编排）与 `xiaomusic/player/`（按职责拆分的 mixin：`lifecycle` 运行时状态与生命周期、`playlist` 歌单快照、`queue_dispatch` 窗口下发、`follower` 拉流跟随与开播确认、`commands` 命令仲裁与切歌、`failure` 失败策略、`external_play` API 点播入口、`library_tasks` 下载） |
| auth | 持久认证、短会话与 runtime 恢复 | `xiaomusic/auth.py`、`xiaomusic/security/token_store.py` |
| config | 配置对象与持久化 | `xiaomusic/config.py`、`xiaomusic/config_manager.py` |
| relay | 站内流媒体中转 | `xiaomusic/relay/` |
| webui | 浏览器界面与 API 消费 | `xiaomusic/webui/` |

## 核心调用边界

- WebUI 只调用 service 层封装的 Public / Internal API。
- API 播放与控制经 `PlaybackFacade`，不得直接操纵设备队列。
- Playback 经 SourceRegistry 解析来源，经 transport 下发设备命令。
- Source 不调用 device，不持有完整 runtime。
- Relay 提供流服务，不主动发起播放命令。
- 播放状态、歌单事实、认证状态、配置与 relay session 各有唯一权威，详见 `state-authority.md`。

## 当前正式 source

内置 source 共五种：`direct_url`、`site_media`、`jellyfin`、`local_library`、`online_plugin`。注册入口为 `register_default_source_plugins()`；完整约束见 `docs/architecture/source_architecture.md`。

## 需要谨慎修改的区域

以下区域并非永久禁改，但必须先补足调用链和针对性测试：

- `auth.py` 与 `security/token_store.py`：持久认证、短会话和 runtime swap 形成事务边界。
- `device_player.py`：运行态、队列、timer 与任务 ownership 高度耦合；将按 ADR-0007 拆分。改播放行为时先在 `tests/behavior/`（FakeSpeaker 行为测试）补用例。
- `playback/facade.py`：API、core coordinator 与状态投影的适配边界。
- `xiaomusic/xiaomusic.py`：应用装配与多个子系统生命周期入口。

## 当前可证实的维护边界

- v1 Public API 由 24 项白名单和生产装配测试约束；其中 `control/pause`、`control/resume` 计划在 ADR-0007 第 6 步删除（项目不提供暂停/恢复）。
- `/api/v1/sources*`、`/api/v1/auth/status` 与 `/api/v1/debug/*` 仍是 v1.1.x 兼容入口；已有等价性测试，未到明确移除窗口前保留。
- `LegacyPayloadSourcePlugin` 与 source hint 映射是兼容层，计划到 v1.2 再依据真实调用证据评估。
- 旧统一事件模型、`play_id` / SSE `session_id`、snapshot 调试端点从未落地，不属于当前规则。

## 文档入口

| 文档 | 用途 |
|---|---|
| `docs/architecture/README.md` | 架构文档地图 |
| `docs/spec/README.md` | 行为规范地图 |
| `docs/api/api_v1_spec.md` | v1 Public API 权威契约 |
| `docs/adr/README.md` | 架构决策记录及状态 |
| `docs/architecture/constraints.md` | 当前可执行约束 |
| `docs/architecture/contributor_guide.md` | 改动与验证要求 |
