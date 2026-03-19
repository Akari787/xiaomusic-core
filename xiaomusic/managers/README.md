# 目录职责

本目录负责需要独立生命周期管理的运行时进程或资源管理器。当前唯一实体是 MusicFree JS 插件管理器。

# 主要内容

## js_plugin_manager.py（正式入口）

`JSPluginManager` 负责 MusicFree JS 插件的完整运行时管理。

当前承担的职责：

- **Node 进程生命周期**：启动、监控、重启（带速率限制：60 秒窗口内上限控制）
- **IPC 消息通道**：通过 stdin/stdout JSON-lines 协议与 Node 进程通信
- **插件配置加载**：从 `conf/plugins-config.json` 读取插件列表与配置
- **插件状态管理**：维护已加载插件的内存状态字典
- **搜索执行入口**：`search()` 方法作为在线音乐插件搜索的调用点

配置路径约定：
- JS 插件目录：`<conf_path>/js_plugins/`
- 插件配置文件：`<conf_path>/plugins-config.json`

# 不应该放什么

- HTTP 路由（属于 `api/`）
- exec Python 插件管理（属于 `xiaomusic/plugin.py`）
- 在线音乐业务逻辑（属于 `services/online_music_service.py`）
- 与 JS 插件无关的后台任务调度

# 关键入口

- `xiaomusic/managers/js_plugin_manager.py`：MusicFree JS 插件运行时（正式入口）

# 与其他目录的关系

- **`xiaomusic/services/online_music_service.py`**：`OnlineMusicService` 持有 `JSPluginManager` 引用，通过其执行插件搜索，services 是业务层，managers 是运行时层。
- **`xiaomusic/js_plugin_manager.py`**（顶层）：兼容导出包装，转发到本目录，标记为 DEPRECATED，禁止新代码依赖。
- **`xiaomusic/plugin.py`**：管理 exec Python 插件，两者是平行关系（JS 插件链 vs exec 插件链），不互相依赖。

# 命名与兼容说明

`xiaomusic/js_plugin_manager.py`（顶层兼容包装）与本目录的 `xiaomusic/managers/js_plugin_manager.py`（正式实现）同名，容易混淆。

规则：
- 新代码一律 import `xiaomusic.managers.js_plugin_manager`
- 顶层包装仅为历史兼容，不作为新功能调用点

# 未来演进方向

`JSPluginManager` 当前职责较重（进程管理 + IPC + 配置 + 搜索入口集中在一个类）。如需拆分，建议方向：
1. `JSProcessManager`：进程生命周期 + IPC 通道
2. `JSPluginRegistry`：插件加载与状态管理
3. 搜索执行入口上移至 `OnlineMusicService`

此拆分不在当前版本计划内，记录于此供后续参考。
