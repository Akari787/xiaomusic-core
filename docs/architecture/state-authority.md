# S2.5: 质量门禁 - 状态权威偏差表

> 审计时间：2026-05-16
> 任务：s2.5-1（质量门禁）
> 前提：S2-abc 和 S2-def 都完成

## 1. 状态权威分析

### 1.1 所有状态汇总

基于 `runtime-dependency.md`、`state-flow.md`、`lifecycle.md`、`source-system.md`、`runtime-boundary.md`、`api-contract.md` 的分析，汇总如下：

| 状态 | 文件 | 应有权威 | 当前实际权威 | 偏差说明 | 严重程度 |
|---|---|---|---|---|---|
| 播放状态（is_playing） | `device_player.py` | `device_player.py` | `device_player.py` + `xiaomusic.xiaomusic`（通过 xiaomusic 实例访问） | 当前权威正确，但 device_player 直接持有 xiaomusic 引用，访问路径不干净 | 低 |
| 歌单事实与设备运行时队列 | `music_library.py` / `device_player.py` | `music_library` 拥有 membership；`device_player` 拥有当前 session 快照与索引 | 职责已分离 | `_play_list_items` 是从歌单事实建立的 session 快照，不得写回或在 next/previous 时重建 | 无 |
| 认证状态（auth_state） | `auth.py` | `auth.py` | `auth.py`（正确） | 无偏差 | 无 |
| Auth token（_mina_token） | `auth.py` | `auth.py` | `auth.py`（正确） | 无偏差 | 无 |
| runtime_auth_ready | `auth.py` | `auth.py` | `auth.py`（正确） | 无偏差 | 无 |
| 设备列表（devices） | `device_manager.py` | `device_manager.py` | `device_manager.py`（正确） | 无偏差 | 无 |
| 设备播放状态（cur_music, entity_id） | `device.model` | `device_player.py`（播放控制者） | `device_player.py` 更新 + `device.model` 持有 | device model 作为数据容器被直接修改，缺少通过 player 的封装 | 低 |
| Source 插件注册（_plugins） | `source_registry.py` | `source_registry.py` | `default_registry.py`（注册时）+ `source_plugin_manager.py`（管理） | 注册逻辑分散在 default_registry，source_registry 只负责查找，职责分离不清 | 低 |
| 流会话状态（session.state） | `stream_session_manager.py` | `stream_session_manager.py` | `stream_session_manager.py` + `relay_runtime.py`（多处直接修改） | RelayRuntime._stop_oldest_active_session 和 sweep_idle_sessions 直接操作 session 状态，越过 manager 接口 | 中 |
| 配置状态（config.*） | `config.py` | `config.py`（或专用的 settings manager） | `config.py` + `xiaomusic.xiaomusic`（直接持有） | xiaomusic 直接持有 config 实例，多处代码通过 xiaomusic.config 访问，缺少统一的配置读取封装 | 低 |
| 事件订阅（_subscribers） | `events.py` | `events.py`（EventBus 单例） | `events.py`（正确） | 无偏差 | 无 |
| 异步任务（running_task） | `xiaomusic.py` | `xiaomusic.py`（统一管理） | `xiaomusic.py` + 分散在各模块（_recovery_task, _next_timer, _duration_probe_task 等） | 各模块自行创建 asyncio.Task，但 xiaomusic.running_task 只追踪主任务，子任务的 owner 不清晰 | 中 |
| 播放进度（_start_time, _duration） | `device_player.py` | `device_player.py` | `device_player.py`（正确） | 无偏差，但计算依赖轮询（get_if_xiaoai_is_playing()），不是事件驱动 | 低 |
| relay 流 URL（stream_url） | `relay/runtime.py` | `relay/runtime.py` | `relay/runtime.py`（正确） | 无偏差 | 无 |
| LinkPlaybackStrategy | `link_strategy.py` | `link_strategy.py` | `relay/runtime.py`（持有）+ `xiaomusic.xiaomusic`（可选持有） | LinkPlaybackStrategy 可以从 xiaomusic 获取也可以独立创建，生命周期不明确 | 低 |

### 1.2 高度可疑状态（已解决）

| 状态 | 问题 | 状态 |
|---|---|---|
| 播放列表双写 | `device_player._play_list_items` 和 `music_library.music_list` 内容可能不一致 | ✅ 已解决：删除 `_play_list`，统一通过 `_get_playlist_names()` 派生 |
| 异步任务 owner 缺失 | `_recovery_task`、`_next_timer`、`_duration_probe_task` 创建者不明确 | ✅ 已标注：所有 task 创建处有 `# owner: xxx` 注释 |
| session 状态越界修改 | `relay_runtime` 绕过 `session_manager` 直接修改 session 状态 | ✅ 已解决：通过 `session_manager.stop_session()` API |
| source 注册逻辑分散 | 注册逻辑在 default_registry + source_registry + manager 三处 | ✅ 已解决：SiteMediaSourcePlugin 通过 LinkPreparer Protocol 解耦 |

## 2. 质量门禁判断

### 2.1 严重偏差汇总

| 偏差类型 | 数量 | 说明 |
|---|---|---|
| 高严重程度 | 0 | 无架构级别矛盾 |
| 中严重程度 | 0 | 全部 4 个已解决（播放列表双写、session越界、task owner、source注册分散） |
| 低严重程度 | 6 | 访问路径不干净、设备 model 直接修改、配置访问分散、轮询驱动、非单例访问等 |

**结论**：全部中严重偏差已解决。系统进入"低风险技术债"状态，剩余问题均为可管理的细节。

### 2.2 核心架构矛盾（无）

经过 S2 全面审计，未发现架构级别的根本性矛盾：
- Source 插件化设计合理，`SiteMediaSourcePlugin` 的 runtime_provider 依赖是已知问题，但不影响整体可运行性
- API 边界清晰，WebUI 不直接访问 runtime
- 状态权威大部分正确，只有"数据双写"问题需要关注

## 3. 下一步建议

### 3.1 已落地的低风险约束

1. **歌单事实与运行时队列分离**：`music_library` 提供 membership，`device_player` 只为当前 session 保存稳定队列快照与索引。
2. **控制意图单入口**：WebUI next/previous 不点名曲目，统一经 Public API、Coordinator、Transport 进入设备导航。
3. **随机 session 稳定**：新 session 只洗牌一次；手动和自动 next/previous 均消费同一快照。
4. **异步任务 owner 明确**：延迟任务受 session ID 约束，旧回调不得覆盖新播放状态。
5. **session 状态通过 manager 接口修改**：relay session 不得被其他模块越界写入。

### 3.2 中期重构（需要 ADR）

1. **Source 插件解耦**：`SiteMediaSourcePlugin` 通过 `runtime_provider` 调用 runtime 的问题，需要通过 ADR 确定"播放准备"职责归属
2. **RelayRuntime 拆分**：将策略选择（relay/direct/proxy）和播放编排（play+cast）分离
3. **AuthManager 拆分**：认证状态管理和 runtime 管理应分离为不同模块

### 3.3 长期架构演进

1. **xiaomusic.xiaomusic 减肥**：作为应用入口，不承担具体业务逻辑
2. **引入状态权威注册表**：将每个状态的权威显式注册，所有状态变化必须经过权威对应的接口

当前播放控制模型见 [`playback-control-model.md`](playback-control-model.md)。