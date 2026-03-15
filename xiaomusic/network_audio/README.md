# 目录职责

本目录负责网络音频子系统，处理 URL/站点媒体解析、session 管理、流媒体传输、重连策略与相关运行时支撑。

# 主要内容

- URL 分类与媒体解析
- 会话管理与运行时状态
- stream 服务、音频转发与本地 HTTP 流
- 重连策略、缓存与契约定义
- Xiaomi 设备适配到网络音频播放链的桥接能力

典型文件：

- `resolver.py`
- `session_manager.py`
- `runtime.py`
- `audio_streamer.py`
- `play_service.py`
- `reconnect_policy.py`
- `contracts.py`
- `xiaomi_adapter.py`

# 不应该放什么

- FastAPI 路由与 API 请求模型
- WebUI 或前端逻辑
- 通用播放控制门面
- 配置管理、插件管理、设备生命周期管理
- 与网络音频无关的传统本地播放流程

# 关键入口

- `xiaomusic/network_audio/runtime.py`：网络音频运行时聚合入口
- `xiaomusic/network_audio/play_service.py`：网络音频播放服务入口
- `xiaomusic/network_audio/resolver.py`：解析入口
- `xiaomusic/network_audio/api.py`：子系统内部 API 暴露点

# 与其他目录的关系

- 与 `xiaomusic/api/`：API 只应调用 network_audio 暴露的稳定能力，不应直接嵌入解析流程。
- 与 `xiaomusic/playback/`：playback 负责面向上层的控制门面，network_audio 负责网络音频实现细节。
- 与 `xiaomusic/core/`：core 更偏通用播放架构；network_audio 是专门的网络音频子域实现。
- 与 `xiaomusic/device_player.py`：device_player 关注单设备传统播放行为，network_audio 关注网络媒体链路。

# 新增代码规则

- 当代码与“网站媒体 / 直链媒体 / URL 解析 / streaming / reconnect / session 生命周期”直接相关时，应放在本目录。
- 如果新增能力只是把网络音频功能通过 HTTP 暴露，应优先放到 `api/`，不要把路由写进这里。
- 如果新增能力是统一播放编排或 API 门面，优先考虑 `playback/` 或 `core/`。
- 本目录新增代码应优先保持子系统内聚，不把无关业务逻辑、UI 逻辑或设备全局管理逻辑混入。
