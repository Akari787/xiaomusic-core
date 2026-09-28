# 状态权威与生命周期边界

> 状态：现行
> 最后核对：2026-09-28

本文档回答两个问题：每类运行时事实由谁拥有，以及其他模块应如何读取或改变它。它不保留阶段任务编号和已经解决的过程性偏差。

## 1. 权威表

| 状态 | 唯一权威 | 合法访问方式 | 保留依据 |
|---|---|---|---|
| 播放 phase、attempt、failure、当前 track reference | `XiaoMusicDevice` 的 `PlaybackRuntimeState` | `PlaybackFacade` 构建只读快照；命令经 coordinator / transport | `runtime_state.py`、`device_player.py`、T03/T04/T06 单元测试 |
| 当前 session 队列快照与索引 | `XiaoMusicDevice` | 由设备导航方法推进；next/previous 不在 WebUI 或 Facade 重建队列 | `playback-control-model.md`、T04 测试 |
| 歌单定义与 membership | `MusicLibrary` | identity API / library 方法 | identity 回归测试与 `webui_playlist_state.md` |
| 设备集合与生命周期 | `DeviceManager` | registry / manager 查询 | `device_manager.py`、`core/device/` |
| 持久认证事实 | `TokenStore` / `auth.json` | `TokenStore.commit()` 原子写入 | auth recovery 规范和 token store 测试 |
| 认证恢复状态与 runtime candidate | `AuthManager` | `ensure_auth()`、atomic recovery、verify 后 swap | auth recovery 规范与稳定性测试 |
| 运行时配置 | `Config`，持久化由 `ConfigManager` | settings API 与配置管理器 | `config.py`、`config_manager.py` |
| Source 注册集合 | `SourcePluginManager` 管理、`SourceRegistry` 分派 | default registration、reload 后按 registry version 重建 coordinator | source manager / registry 测试 |
| Relay session 状态 | relay session manager | manager API；其他模块不得直接改 session 字段 | relay component / unit tests |
| Public API 契约 | `api_v1_spec.md` + 生产路由装配 | router/model/schema gate | `test_api_boundary_phase1.py` |

## 2. 播放状态约束

- `PlaybackRuntimeState.phase`、`LifecycleToken` 与 `PlaybackTaskRegistry` 决定播放生命周期。
- `_last_cmd` 只允许作为 legacy diagnostic，不决定状态、失败、重试或完成合法性。
- `_play_session_id` 只用于异步媒体 session 失效，不替代 lifecycle token。
- `GET /api/v1/player/state` 与 SSE 只投影事实，不创建/取消任务、不调用 Mina、不写 runtime state。
- `accepted=true` 只表示命令被接收；设备是否真正进入 playing 仍由后续快照确认。

## 3. 队列与歌单约束

- `MusicLibrary` 管长期歌单事实；`XiaoMusicDevice` 管一次播放 session 的稳定快照。
- 随机播放只在新 session 建立时洗牌一次。
- 手动和自动 next/previous 消费同一快照，WebUI 不点名下一首重新调用 play。
- 设备快照不得反向写回歌单 membership。

## 4. 事件系统的实际定位

`EventBus` 当前只承载 `CONFIG_CHANGED`、`DEVICE_CONFIG_CHANGED`、`PLAYER_STATE_CHANGED` 三种通知。事件用于唤醒保存或状态推送，不是业务状态权威，也不承担 event sourcing / replay。

旧文档曾要求所有状态变化进入约二十种标准事件，并引入 `play_id` 与事件 schema 版本。该设计没有落地，ADR-0005 已撤回，不能据此要求当前代码。

## 5. 异步任务 ownership

- 应用主循环由 FastAPI lifespan 保存和取消。
- 播放任务由 `PlaybackTaskRegistry` 管理，名称和设备 scope 受测试约束。
- auth、library 等模块级后台任务由对应对象字段持有并在本模块边界取消或替换。
- 禁止依赖扫描全部 asyncio task 再读取自定义 `_owner` 属性的全局清理方式。

## 6. 修改前验证

涉及本页任一权威时，至少完成：

1. 确认写入仍发生在权威模块；
2. 确认查询路径没有新增副作用；
3. 运行对应模块的定点测试；
4. 若改变 Public API 字段或语义，同步 API/spec 并运行生产装配门禁。
