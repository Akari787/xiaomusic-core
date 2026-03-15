# 目录职责

本目录负责项目的 HTTP API 层，对外暴露 FastAPI 路由、请求模型、响应封装与运行时依赖注入。

# 主要内容

- FastAPI 应用入口与挂载逻辑
- 路由模块（如 `routers/v1.py`、`routers/system.py`、`routers/device.py`）
- API 请求/响应模型
- 统一响应 envelope、错误映射、依赖注入
- 运行时对象获取、基础 URL 推导、WebSocket 辅助能力

典型文件：

- `app.py`
- `dependencies.py`
- `response.py`
- `api_error.py`
- `routers/*`
- `models/*`

# 不应该放什么

- 复杂业务逻辑
- 设备播放控制实现
- 网络音频解析与流媒体实现
- 插件运行时管理
- 需要长时间驻留的后台任务编排

如果某段逻辑需要访问设备状态、做复杂重试、调度多个子系统，通常不应直接写在 router 中。

# 关键入口

- `xiaomusic/api/app.py`：FastAPI 应用装配入口
- `xiaomusic/api/routers/v1.py`：正式结构化 API 入口
- `xiaomusic/api/dependencies.py`：运行时依赖与权限校验入口
- `xiaomusic/api/response.py` / `xiaomusic/api/api_error.py`：统一响应与错误语义

# 与其他目录的关系

- 与 `xiaomusic/playback/`：API 层调用 playback facade 暴露播放控制，不直接承载播放编排细节。
- 与 `xiaomusic/network_audio/`：API 可以触发网络音频能力，但不应内嵌 resolver、session、streamer 逻辑。
- 与 `xiaomusic/core/`：v1 播放接口通过 facade 间接使用 core 模块，不直接操作 coordinator 细节。
- 与 `xiaomusic/services/` / `xiaomusic/managers/`：API 可以调用 service/manager，但不应在 router 中重新实现其职责。

# 新增代码规则

- 当需求是“新增 HTTP 接口 / 请求模型 / 响应模型 / 错误映射”时，应放在本目录。
- 当需求是“把现有内部能力通过 HTTP 暴露”时，应在本目录增加薄路由，并把复杂逻辑留在 facade / service / manager / runtime 层。
- 如果新增代码主要是控制流程、业务规则或设备行为，不应直接放在 `api/`，而应先放到更合适的业务层，再由 API 调用。
- 新增 v1 能力时，优先遵循现有 `routers/v1.py` 的结构化风格，不新增字符串命令转发式接口。
