# 系统约束

> 状态：现行
> 适用版本：xiaomusic-core 1.1.x
> 最后核对：2026-09-28

本文档只保留当前实现中可以由代码、测试或运行装配验证的约束。愿景、尚未实施的设计和历史方案不再作为强制规则。

## 1. 约束优先级

发生冲突时按以下顺序裁决：

1. `docs/api/api_v1_spec.md`：Public API 契约
2. `docs/spec/*`：运行时行为与字段语义
3. `docs/architecture/system_overview.md` 与本文件：边界和工程约束
4. 其他 architecture 文档
5. `ARCHITECTURE.md`：入口摘要

ADR 记录决策历史。只有状态为 Accepted 且未被撤回或取代的 ADR 才能约束当前实现。

## 2. Public API 与路由

- 正式外部接口只位于 `/api/v1/*`，其 OpenAPI 方法与路径集合必须等于白名单。
- Admin 与 Internal Diagnostics 使用独立命名空间；`include_in_schema=False` 不能替代命名空间隔离或鉴权。
- 新增或修改 v1 路由时，必须同步更新 `docs/api/api_v1_spec.md`、严格请求模型和生产装配测试。
- 命令响应只表达动作受理；权威播放状态从 `/api/v1/player/state` 或 `/api/v1/player/stream` 读取。
- 已弃用路由只有在存在明确兼容窗口及回归测试时才能保留。没有调用方、没有契约价值且已被正式接口取代的路由应删除。

**判断依据**：`tests/test_api_boundary_phase1.py`、`tests/test_removed_device_wrappers.py`、`xiaomusic/api/routers/__init__.py`。

## 3. WebUI 边界

- WebUI 只能通过 `xiaomusic/webui/src/services/` 调用后端，不得依赖 Python runtime、playback 或 device 内部对象。
- 正式播放、控制、状态与在线搜索使用 v1 service；Internal API 仅承载仍有实际 WebUI 调用方的内部工具能力。
- 前端不得用标题、旧字段或本地推测替代服务端的 track identity、transport state 或在线搜索 `play_reference`。

**判断依据**：`docs/architecture/webui_architecture.md`、`docs/spec/webui_playback_state_machine_spec.md`、WebUI service 与回归测试。

## 4. Playback 与状态权威

- API 播放与控制必须经 `PlaybackFacade` 进入 `PlaybackCoordinator` / transport 链路，router 不直接操纵设备队列。
- 播放运行态以 `PlaybackRuntimeState`、`LifecycleToken` 和 `PlaybackTaskRegistry` 为准；Facade 只做投影与边界适配。
- 歌单 membership 由 `MusicLibrary` 管理；设备当前 session 的队列快照和索引由 `XiaoMusicDevice` 管理。
- state/SSE 查询路径只读，不创建或取消播放任务，不调用 Mina，不隐式修复运行态。

**判断依据**：`docs/architecture/state-authority.md`、`docs/architecture/playback-control-model.md`、`tests/unit/test_t03h_runtime_snapshot.py`、`tests/unit/test_t06_playback_task_registry.py`。

## 5. Source 边界

- Source 实现 `SourcePlugin.resolve()`，只负责把请求解析为 `ResolvedMedia`；不得直接下发设备命令或修改播放队列。
- 内置 source 必须经 `register_default_source_plugins()` 注册，并在 API `source_hint` 契约中声明。
- Source 不得持有完整 runtime / `XiaoMusic` 实例；允许注入完成本 source 职责所需的窄能力，例如 `LinkPreparer`、引用 store 或 `OnlineMusicService`。
- MusicFree JS action 属于动态插件协议，不能仅因缺少 Python 静态调用就删除。

**判断依据**：`xiaomusic/core/source/`、`xiaomusic/adapters/sources/default_registry.py`、`xiaomusic/js_plugin_runner.js`、source 测试。

## 6. Auth、配置与敏感数据

- `TokenStore` / `auth.json` 是持久认证事实来源；候选 runtime verify 成功后才允许 commit 与 swap。
- 不得把 cookie、token、密码、原始插件 item 或媒体敏感 URL 放进 Public API、日志、测试夹具或版本库。
- API 和插件错误对外必须结构化并脱敏；内部日志也应通过既有 redaction 边界。

**判断依据**：`docs/spec/auth/auth_runtime_recovery.md`、`xiaomusic/security/token_store.py`、`xiaomusic/security/redaction.py` 及安全测试。

## 7. 异步任务与生命周期

- 后台任务必须由创建它的组件保存引用、登记到既有 task registry，或由应用 lifespan 持有，并在对应关闭/失效边界取消。
- 不使用扫描 `asyncio.all_tasks()` 加自定义 `_owner` 属性的全局清理模式。
- 播放相关任务名称和所有权必须通过 `PlaybackTaskRegistry` 保持唯一；旧 session 的回调不得覆盖新 session 状态。

**判断依据**：`xiaomusic/playback/task_registry.py`、`xiaomusic/api/app.py`、`tests/unit/test_t06_playback_task_registry.py`。

## 8. 异常与临时兼容

- 禁止无日志、无返回语义的 `except: pass`。
- 临时兼容必须写清原因、退出条件和保护测试；无需使用固定人名或虚构版本日期充当 owner。
- 兼容代码不得成为新功能入口。删除前需检查生产注册、动态分派、现行测试和发布兼容窗口。

## 9. 已撤销的旧规则

以下内容曾出现在旧版架构文档中，但与当前实现不符，不再生效：

- “所有状态变化必须通过统一事件模型通知”。当前 EventBus 只承载三个既有通知事件，状态权威仍由各所属模块维护。
- 约二十种标准事件、事件 replay、`play_id` / SSE `session_id` 和 `/api/v1/debug/snapshot`。这些设计未落地。
- “所有 asyncio task 通过 `_owner` 属性由全局 shutdown 扫描取消”。当前采用 lifespan、模块字段和 `PlaybackTaskRegistry` 的局部 ownership。
- “某些核心文件绝对不能修改”。高风险区域需要更多证据和测试，但不以文件名建立永久禁改名单。
- “CI 已有通用架构静态检查”。现行门禁以具体测试和 workflow 为准，不宣称不存在的检查。
