# 目录职责

本目录负责播放控制门面层，把 API 层请求适配为统一播放编排入口，并承载与播放链接策略相关的薄业务逻辑。

# 主要内容

- 播放控制 facade
- 播放请求到 core coordinator 的适配逻辑
- 链接播放策略、代理/直链选择策略
- 对 API 层稳定暴露的播放调用入口

典型文件：

- `facade.py`
- `link_strategy.py`

# 不应该放什么

- HTTP 路由与 API 请求解析
- 前端/UI 逻辑
- 网络音频 URL 解析器、streamer、session 管理
- 单设备播放器内部定时器与播放状态细节
- 配置持久化、插件管理等无关逻辑

# 关键入口

- `xiaomusic/playback/facade.py`：当前播放控制的正式门面入口
- `xiaomusic/playback/link_strategy.py`：直链/代理/外部链接播放策略入口

# 与其他目录的关系

- 与 `xiaomusic/api/`：API 层应优先通过 facade 调用播放能力，而不是直接拼装底层播放细节。
- 与 `xiaomusic/core/`：playback 目录本质上是对 core 播放能力的薄适配层。
- 与 `xiaomusic/network_audio/`：network_audio 负责网络音频子系统细节，playback 不应重复实现 resolver 或 runtime。
- 与 `xiaomusic/device_player.py`：传统设备播放链仍存在，但 `playback/facade.py` 更偏向结构化 v1 接口的新入口。

# 新增代码规则

- 当代码职责是“给 API 提供稳定播放入口”“封装播放编排调用”“保持 API 与 core 解耦”时，适合放在本目录。
- 如果代码属于 resolver、session、stream、重连策略、URL 分类，应放到 `network_audio/` 而不是这里。
- 如果代码只是 HTTP 参数校验或响应封装，应放到 `api/`。
- 新增 facade 方法时，应保持薄门面风格：参数校验适度、编排清晰，不把大量底层运行时逻辑重新堆回本目录。
