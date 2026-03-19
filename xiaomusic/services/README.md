# 目录职责

本目录是业务服务层，封装需要协调多个底层能力（插件、外部 API、媒体库）的业务逻辑实体。当前主要实体是在线音乐服务。

# 主要内容

## online_music_service.py（正式入口）

`OnlineMusicService` 负责在线音乐搜索与 Jellyfin 媒体库联动的所有业务逻辑。

主要能力：

- **MusicFree 插件搜索**：通过 `JSPluginManager` 调用 MusicFree JS 插件执行关键词搜索
- **Jellyfin 搜索**：通过 `JellyfinClient` 在 Jellyfin 媒体库中搜索曲目
- **Jellyfin 歌单同步**：将 Jellyfin 播放列表同步为本地歌单
- **播放链接获取**：解析插件搜索结果并返回可播放 URL
- **代理 URL 构造**：为 Jellyfin 资源构造经代理适配的播放链接

依赖：
- `JSPluginManager`（`managers/js_plugin_manager.py`）
- `JellyfinClient`（`jellyfin_client.py`）
- `online_music_keywords`（`providers/online_music_keywords.py`）

# 不应该放什么

- HTTP 路由（属于 `api/`）
- JS 插件进程管理（属于 `managers/js_plugin_manager.py`）
- 播放链路编排（属于 `playback/facade.py` 或 `core/`）
- 本地媒体库索引与扫描（属于 `music_library.py`）

# 关键入口

- `xiaomusic/services/online_music_service.py`：在线音乐业务正式入口

# 与其他目录的关系

- **`xiaomusic/managers/`**：`OnlineMusicService` 使用 `JSPluginManager` 执行 JS 插件搜索，managers 负责运行时，services 负责业务语义。
- **`xiaomusic/adapters/sources/jellyfin_source_plugin.py`**：来源插件在解析 Jellyfin 资源时调用 `online_music_service._get_plugin_proxy_url()`，是单向依赖。
- **`xiaomusic/online_music.py`**（顶层）：兼容包装，转发到本模块，标记为 DEPRECATED。
- **`xiaomusic/xiaomusic.py`**：主运行时在初始化时创建 `OnlineMusicService` 实例并持有引用。

# 新增代码规则

- 当新业务逻辑需要协调插件 + 外部 API + 媒体库等多个能力时，适合放在本目录。
- 不在 service 中直接持有 HTTP 请求上下文或 FastAPI 依赖——service 应是无状态或轻状态的可测试单元。
- 新增 service 时，应在 `ARCHITECTURE.md` 的服务层索引中补充说明。
