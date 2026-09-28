# PlaybackCoordinator 接口约束

> 状态：现行内部接口
> 最后核对：2026-09-28
> 实现：`xiaomusic/core/coordinator/playback_coordinator.py`

## 1. 作用

`PlaybackCoordinator` 编排 Source 解析、delivery plan、transport 分派与一次受控的过期流重解析。它不拥有歌单事实，也不直接定义 Public API 响应。

## 2. 依赖

构造时注入：

- `SourceRegistry`：根据 `source_hint` 与请求选择插件
- `DeviceRegistry`：读取设备 profile 与能力矩阵
- `DeliveryAdapter`：把 `ResolvedMedia` 转为 direct / proxy plan
- `TransportRouter`：下发播放与控制动作
- 可选 `playback_status_provider`：确认设备是否实际开始播放

Coordinator 不接收完整 `XiaoMusic` runtime。

## 3. 输入与输出

### `play(request: MediaRequest, device_id: str | None = None) -> dict`

- `device_id` 必须由参数或 `request.device_id` 提供。
- Source 返回 `ResolvedMedia`；delivery 返回 `DeliveryPlan`；transport 返回 dispatch result。
- 结果包含 `request_id`、`transport`、`prepared_stream`、`resolved_media`、`dispatch`、`delivery_plan` 与 `outcome`。
- `ExpiredStreamError` 最多按 `max_resolve_retry` 重做解析；其他错误不在 coordinator 内扩散重试。

### `resolve(request: MediaRequest) -> dict`

只执行 Source 解析，不下发设备命令。

### 控制方法

`stop`、`previous`、`next`、`pause`、`resume`、`tts`、`set_volume`、`probe` 统一经 `TransportRouter`。`resume` 当前通过 transport 的 pause toggle 兼容实现。

## 4. 状态投影边界

Coordinator 的返回值是内部编排结果，不等于 `/api/v1/player/state`。Public 状态由 `PlaybackFacade` 从设备运行态构建，并遵守 `player_state_projection_spec.md`：

- `track.id` 使用后端稳定 identity，不使用 `context_id + title` 的旧组合哈希规则；
- `context` 保持对象结构，不展开为 `context_type/context_id/context_name` 顶层字段；
- `accepted` 与 `started` 分离，命令被接收不代表设备已经播放；
- WebUI 不直接消费 Coordinator 原始对象。

## 5. 验证依据

- `tests/unit/test_core_playback_coordinator.py`
- `tests/unit/test_core_source_plugins_and_registry.py`
- `tests/unit/test_core_transport_router.py`
- `tests/unit/test_playback_facade.py`
- `docs/spec/player_state_projection_spec.md`
