# S3-1: Event Model 设计

> 设计时间：2026-05-16
> 任务：s3-ab（Event 与 Correlation）
> 前提：阶段2.5完成

## 1. 当前事件系统分析

### 1.1 现有实现

`xiaomusic/events.py` 提供了 `EventBus` 类，支持：

- 同步/异步回调订阅
- `publish(event_type, **kwargs)` 发布
- 异常安全（捕获回调中的异常）

### 1.2 现有事件类型

| 事件常量 | 触发位置 | 携带参数 |
|---|---|---|
| `CONFIG_CHANGED` | `music_library.py` | 无 |
| `DEVICE_CONFIG_CHANGED` | `device_player.py`（多处） | 无 |
| `PLAYER_STATE_CHANGED` | `device_player.py`（多处） | `device_id` |

### 1.3 当前事件系统的局限性

1. **事件类型过少**：只有3种事件，无法覆盖所有状态变化
2. **触发方式不一致**：有些状态变化通过事件通知，有些直接调用方法
3. **事件 payload 不规范**：没有统一的 schema，不同事件的参数不同
4. **无 event schema 变更流程**：如果需要新增/修改事件，没有 ADR 流程
5. **emit 失败无处理**：publish 中异常被静默捕获（只有 print），不重试、不告警

## 2. 标准事件列表定义

### 2.1 事件分类

#### 播放相关事件

| 事件名 | 触发时机 | payload |
|---|---|---|
| `PLAY_REQUESTED` | 用户发起播放请求（API 收到） | `request_id`, `device_id`, `source_hint`, `query` |
| `SOURCE_RESOLVED` | Source 插件解析完成 | `request_id`, `source`, `media_id`, `title` |
| `PLAYBACK_STARTED` | 播放真正开始（设备响应成功） | `request_id`, `device_id`, `media_id`, `stream_url` |
| `PLAYBACK_STOPPED` | 播放停止（主动或被动） | `request_id`, `device_id`, `reason`（normal/error/timeout） |
| `PLAYBACK_PAUSED` | 播放暂停 | `request_id`, `device_id` |
| `PLAYBACK_RESUMED` | 播放恢复 | `request_id`, `device_id` |
| `QUEUE_UPDATED` | 播放队列变化（增删改） | `device_id`, `queue_version`, `change_type` |
| `DEVICE_STATE_CHANGED` | 设备状态变化（在线/离线） | `device_id`, `state`（online/offline/unknown） |

#### 认证相关事件

| 事件名 | 触发时机 | payload |
|---|---|---|
| `AUTH_EXPIRED` | Token 过期检测到 | `reason` |
| `AUTH_RESTORED` | 认证恢复成功 | `method`（relogin/recovery） |
| `AUTH_FAILED` | 认证恢复失败 | `reason`, `attempt_count` |
| `AUTH_LOGGED_OUT` | 用户登出 | 无 |

#### Source 相关事件

| 事件名 | 触发时机 | payload |
|---|---|---|
| `SOURCE_REGISTERED` | 新 Source 插件注册 | `source_name`, `source_type` |
| `SOURCE_UNREGISTERED` | Source 插件注销 | `source_name` |
| `SOURCE_ERROR` | Source resolve 失败 | `source`, `error_code`, `request_id` |

#### 系统相关事件

| 事件名 | 触发时机 | payload |
|---|---|---|
| `CONFIG_CHANGED` | 配置变更 | `changed_keys`（list） |
| `DEVICE_CONFIG_CHANGED` | 设备配置变更 | `device_id`, `changed_keys` |
| `SYSTEM_ERROR` | 系统级错误（非特定模块） | `error_code`, `error_message` |
| `STREAM_FAILED` | 流媒体推送失败 | `sid`, `error_code`, `request_id` |

#### 会话相关事件

| 事件名 | 触发时机 | payload |
|---|---|---|
| `SESSION_CREATED` | 流会话创建 | `sid`, `mode`（relay/direct/proxy） |
| `SESSION_DESTROYED` | 流会话销毁 | `sid`, `reason` |
| `SESSION_IDLE_TIMEOUT` | 会话空闲超时 | `sid`, `idle_seconds` |

## 3. 触发方式决策

### 3.1 同步 vs 异步触发

**决策：默认同步触发，异步场景显式标注**

- 同步触发：事件处理与状态变化在同一调用栈，行为可预测，调试简单
- 异步触发：用于耗时操作（如网络请求、文件IO），但会增加调试难度
- 当前实现中，`EventBus.publish` 内部将异步回调调度为 task，但调用者感知为同步

**规范**：
- 事件发布本身是同步的（`publish` 调用立即返回）
- 回调执行可以是同步或异步，由回调本身决定
- 如果事件发布者在发布后需要等待所有回调完成，使用 `publish_and_wait` 替代

### 3.2 emit 失败处理

**当前问题**：`EventBus.publish` 中异常被静默 print，不重试、不记录、不告警

**决策：按事件类型分级处理**

| 事件类型 | 失败处理 |
|---|---|
| 播放相关（PLAY_*） | 记录 warning，记录到 analytics，不阻断主流程 |
| 认证相关（AUTH_*） | 记录 error，写入专用 auth 日志 |
| 系统错误（SYSTEM_ERROR） | 记录 critical，考虑告警 |
| 会话相关（SESSION_*） | 记录 info，session 本身已有状态 |

**实现建议**：

```python
class EventBus:
    CRITICAL_EVENTS = {"AUTH_EXPIRED", "AUTH_FAILED", "SYSTEM_ERROR"}
    WARNING_EVENTS = {"PLAYBACK_STOPPED", "SOURCE_ERROR", "STREAM_FAILED"}

    def publish(self, event_type: str, **kwargs) -> None:
        if event_type not in self._subscribers:
            return
        errors = []
        for callback in self._subscribers[event_type]:
            try:
                result = callback(**kwargs)
                if result is not None and inspect.isawaitable(result):
                    self._schedule_async_callback(result)
            except Exception as e:
                errors.append((callback, e))
                LOG.error(f"Event callback failed: {event_type} -> {callback}: {e}")
        
        if errors and event_type in self.CRITICAL_EVENTS:
            self._alert_event_failure(event_type, errors)
        elif errors and event_type in self.WARNING_EVENTS:
            LOG.warning(f"Event had {len(errors)} callback failures: {event_type}")
```

## 4. Event Bus 位置

**决策：EventBus 保持为 `xiaomusic.events.EventBus` 单例**

- 位置：`xiaomusic/events.py`
- 访问方式：通过 `xiaomusic.event_bus` 持有
- 不应将 EventBus 作为全局单例（导致隐式依赖），应显式注入

**规范**：模块订阅事件时应通过构造器注入 EventBus，不应直接 import 全局 event_bus 实例

## 5. Schema 变更流程

**问题**：如果事件 payload 结构变更，没有显式流程管理

**决策：引入 ADR 机制管理 event schema 变更**

- 每个事件定义包含：事件名、版本号、payload schema
- 事件版本号在 payload 中传递（如 `_event_version: 1`）
- Schema 变更需要创建新的 ADR
- 保留旧版本兼容（向前兼容），至少一个版本

**事件定义模板**：

```python
@dataclass
class PlaybackStartedEvent:
    _event_version: int = 1
    request_id: str
    device_id: str
    media_id: str
    title: str
    stream_url: str
    started_at: float  # Unix timestamp
    source: str
```

## 6. 遗留事件处理

### 6.1 现有事件的迁移

| 现有事件 | 迁移方式 |
|---|---|
| `CONFIG_CHANGED` | 保留，添加 payload |
| `DEVICE_CONFIG_CHANGED` | 合并到 `DEVICE_STATE_CHANGED` 或独立，添加 device_id |
| `PLAYER_STATE_CHANGED` | 拆分为 `PLAYBACK_STARTED`、`PLAYBACK_STOPPED` 等细粒度事件 |

### 6.2 触发方式规范化

**规范**：所有状态变化必须通过事件通知，不得直接调用其他模块的方法更新状态

**例外**：播放控制（play/pause/stop）作为命令直接调用，但命令执行后的状态变化必须触发事件

## 7. 实施建议

1. **短期**：将现有 3 个事件添加标准 payload，规范化参数
2. **中期**：将分散的状态变化调用替换为事件发布（如 device_player 中的多处状态变更）
3. **长期**：所有事件引入版本管理，建立 schema 注册表