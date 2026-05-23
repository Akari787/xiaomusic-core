# S2-6: API Contract 审计

> 审计时间：2026-05-16
> 任务：s2-def（边界审计）
> 前提：阶段1完成

## 1. API 路由概览

### 1.1 路由文件分布

| 文件 | 路由数 | 说明 |
|---|---|---|
| `api/routers/v1.py` | ~60 | 主要业务 API（播放、控制、库管理） |
| `api/routers/system.py` | ~40 | 系统 API（诊断、设置、认证） |
| `api/routers/music.py` | ~15 | 音乐搜索/播放 |
| `api/routers/playlist.py` | ~10 | 播放列表 |
| `api/routers/file.py` | ~15 | 文件操作 |
| `api/routers/plugin.py` | ~15 | 插件管理 |
| `api/routers/relay.py` | ~5 | 中继会话 |
| `api/websocket.py` | ~2 | WebSocket |

**总计约 150+ 条路由**，分布在 8 个路由文件中。

### 1.2 主要 API 端点分类

| 类别 | 路径模式 | 认证要求 |
|---|---|---|
| 播放控制 | `/api/v1/play`, `/api/v1/control/*` | 需要 |
| 设备管理 | `/api/v1/devices`, `/api/v1/sources/*` | 需要 |
| 系统状态 | `/api/v1/system/*`, `/diagnostics` | 需要 |
| 认证 | `/api/v1/auth/status`, `/api/auth/*` | 部分需要 |
| 调试 | `/api/v1/debug/*` | include_in_schema=False |
| 音乐库 | `/api/v1/library/*`, `/searchmusic` | 需要 |
| 插件 | `/api/js-plugins/*`, `/api/plugin-source/*` | 需要 |

## 2. 错误返回格式审计

### 2.1 错误处理统一性

**正面**：`ApiError` 类统一了所有 API 错误格式

```python
# xiaomusic/api/api_error.py
class ApiError(Exception):
    def __init__(self, *, code: int, message: str, data: dict, request_id: str):
```

**异常映射**（`routers/v1.py` 中的 `_map_api_exception`）：

| 异常类型 | HTTP 状态码 | error_code | 结构化 |
|---|---|---|---|
| `ApiError` | code 字段（40001等） | `data.error_code` | ✅ |
| `InvalidRequestError` | 40001 | `E_INVALID_REQUEST` | ✅ |
| `SourceResolveError` | 20002 | `E_RESOLVE_NONZERO_EXIT` | ✅ |
| `TransportError` | 20001 | `E_TRANSPORT_ERROR` | ✅ |
| `DeviceNotFoundError` | 40002 | `E_DEVICE_NOT_FOUND` | ✅ |
| `DeliveryPrepareError` | 20003 | `E_DELIVERY_PREPARE` | ✅ |

**结论**：API 错误返回结构化程度高，`ApiError` 是统一的异常基类。

### 2.2 返回格式一致性

v1 API 统一使用 `ApiResponse` 包装：

```python
def _api_response(code: int, message: str, data: dict, request_id: str) -> dict:
    return {"code": code, "message": message, "data": data, "request_id": request_id}
```

**正面**：
- 所有 v1 路由使用统一的响应结构
- `request_id` 全链路传递

**需要注意**：system.py 中的部分老路由（如 `/getsetting`, `/savesetting`）可能未使用统一包装格式。

## 3. WebUI 访问模式审计

### 3.1 WebUI 是否绕过 API 直接访问 runtime？

**检查结果**：WebUI 通过 `services/` 中的 API 调用访问后端，不直接 import runtime。

| WebUI 文件 | 访问方式 |
|---|---|
| `src/services/auth.ts` | 调用 `/api/auth/status`、`/api/auth/refresh_runtime` 等 |
| `src/pages/HomePage.tsx` | 通过 auth service 访问状态 |
| `src/pages/ApiV1DebugPage.tsx` | 调试页直接调用 API v1 端点 |

**结论**：WebUI 没有直接 import `runtime` 或 `xiaomusic` 内部模块。✅

### 3.2 runtime_provider 机制

```python
# xiaomusic/api/runtime_provider.py
def get_runtime() -> RelayRuntime:
    global _runtime
    if _runtime is None:
        from xiaomusic.api.dependencies import xiaomusic
        _runtime = RelayRuntime(xiaomusic)
    return _runtime
```

- `get_runtime()` 是 **API 层内部** 获取 runtime 的方式
- **不是** API 端点，是供 `api/routers/` 内部使用的依赖注入函数
- WebUI 无法直接访问此函数，只能通过 API 端点

### 3.3 debug 端点

以下端点标记为 `include_in_schema=False`，未在 OpenAPI 文档中暴露，但仍在运行：

- `/api/v1/debug/auth_state`
- `/api/v1/debug/auth_recovery_state`
- `/api/v1/debug/miaccount_login_trace`
- `/api/v1/debug/auth_rebuild_state`
- `/api/v1/debug/auth_runtime_reload_state`
- `/api/v1/debug/auth_short_session_rebuild_state`

这些是**调试用内部端点**，返回 auth 和 runtime 的详细状态。生产环境应考虑禁用或添加额外认证。

## 4. API 契约一致性评估

### 4.1 边界完整性

| 检查项 | 状态 | 说明 |
|---|---|---|
| 所有播放控制通过 API | ✅ | `/api/v1/play`、`/api/v1/control/*` |
| 所有状态查询通过 API | ✅ | `/api/v1/player/state`、`/api/v1/system/status` |
| WebUI 不直接访问 runtime | ✅ | 通过 services 层调用 API |
| API 错误结构化 | ✅ | `ApiError` 统一异常 |
| runtime_provider 仅 API 层使用 | ✅ | 不暴露给 WebUI |

### 4.2 潜在问题

| 问题 | 严重程度 | 描述 |
|---|---|---|
| system.py 老路由格式不一致 | 低 | `/getsetting`、`/savesetting` 等可能未使用 `ApiResponse` 包装 |
| debug 端点无额外认证 | 中 | `/api/v1/debug/*` 暴露内部状态，生产环境应禁用或保护 |
| v1.py 行数过多（1425行） | 低 | 单文件过大，维护性下降 |
| 缺少 API 版本管理策略 | 低 | 只有 `v1` 前缀，未来 API 变更缺乏版本隔离 |

## 5. 状态同步机制

### 5.1 状态权威归属（当前分析）

| 状态 | 权威 | 访问方式 |
|---|---|---|
| 播放状态 | `device_player.py` | `/api/v1/player/state` |
| Auth 状态 | `auth.py` | `/api/v1/auth/status` |
| 系统设置 | `config.py` | `/api/v1/system/settings` |
| 设备列表 | `device_manager.py` | `/api/v1/devices` |
| Source 列表 | `source_registry` | `/api/v1/sources` |

### 5.2 潜在状态同步问题

**SSE（Server-Sent Events）**：

- 搜索 `xiaomusic/api/websocket.py`，有 WebSocket 支持
- 检查 SSE 推送机制：状态变化时是否主动推送更新？
- 如果有，需要确认 SSE 推送和 API 查询之间的一致性

**WebUI 本地状态**：

- WebUI 在 `HomePage.tsx` 中维护本地状态（如 `authStatus`）
- 通过 `useEffect` 定时或事件驱动刷新
- **潜在风险**：如果 SSE 推送延迟或丢失，本地状态可能与后端不一致

**结论**：当前架构中，**状态同步机制不够显式**，主要依赖轮询或手动刷新。长期应引入统一的事件通知机制。

## 6. 建议

1. **统一 system.py 路由格式**：将 `/getsetting`、`/savesetting` 等老路由也用 `ApiResponse` 包装，保持格式一致

2. **保护 debug 端点**：生产环境禁用或添加 admin 认证

3. **拆分 v1.py**：按功能领域（playback、library、system）拆分为多个路由文件

4. **引入显式状态同步**：用统一的事件通知机制替代轮询，确保 WebUI 状态与后端一致