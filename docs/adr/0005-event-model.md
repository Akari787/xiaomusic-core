# ADR-0005: 统一事件模型

## 状态：已接受

## 上下文

当前事件系统（`xiaomusic/events.py`）存在以下问题：

1. **事件类型过少**：只有 3 种（`CONFIG_CHANGED`、`DEVICE_CONFIG_CHANGED`、`PLAYER_STATE_CHANGED`）
2. **触发方式不统一**：有些状态变化通过事件通知，有些直接调用方法
3. **payload 不规范**：不同事件的参数不同，没有统一 schema
4. **emit 失败无处理**：异常被静默捕获（只有 print）
5. **无 schema 变更流程**：事件结构变更没有 ADR 机制

**根本问题**：没有建立完整的事件驱动模型，状态变化依赖直接调用而非事件通知。

## 决策

**建立统一的事件模型，所有状态变化通过事件通知，事件有标准 schema、版本管理、和分级失败处理。**

### 标准事件列表

见 `docs/architecture/event-model.md`，核心事件：

| 类别 | 事件 |
|---|---|
| 播放 | `PLAY_REQUESTED`、`SOURCE_RESOLVED`、`PLAYBACK_STARTED`、`PLAYBACK_STOPPED`、`PLAYBACK_PAUSED`、`QUEUE_UPDATED` |
| 认证 | `AUTH_EXPIRED`、`AUTH_RESTORED`、`AUTH_FAILED`、`AUTH_LOGGED_OUT` |
| Source | `SOURCE_REGISTERED`、`SOURCE_ERROR` |
| 系统 | `CONFIG_CHANGED`、`DEVICE_STATE_CHANGED`、`SYSTEM_ERROR`、`STREAM_FAILED` |
| 会话 | `SESSION_CREATED`、`SESSION_DESTROYED` |

### 事件 Schema 规范

每个事件包含标准头部：

```python
@dataclass
class BaseEvent:
    _event_version: int = 1          # schema 版本号
    timestamp: float                  # Unix timestamp
    request_id: str | None = None     # 关联的 API 请求
    play_id: str | None = None       # 关联的播放会话
```

Payload 字段因事件类型而异，定义在各自的事件类中。

### 触发方式

- **默认同步触发**：`publish()` 调用立即返回，回调可能在同一线程执行
- **异步场景显式**：如果回调需要耗时操作（如 IO），回调本身应是 async，在内部调度为 task

### emit 失败处理

```python
CRITICAL_EVENTS = {"AUTH_EXPIRED", "AUTH_FAILED", "SYSTEM_ERROR"}
WARNING_EVENTS = {"PLAYBACK_STOPPED", "SOURCE_ERROR", "STREAM_FAILED"}

class EventBus:
    def publish(self, event_type: str, **kwargs) -> None:
        # ... 执行回调 ...
        # 失败时：
        if errors and event_type in CRITICAL_EVENTS:
            alert_critical(event_type, errors)  # 告警
        elif errors and event_type in WARNING_EVENTS:
            LOG.warning(f"Event had {len(errors)} callback failures: {event_type}")
```

### Schema 变更流程

1. 事件 schema 变更需要创建 ADR
2. 每个事件保持版本号（`_event_version`）
3. 新版本向前兼容，至少保留一个版本
4. 废弃的事件标记为 `_deprecated: true`，在下一个主要版本移除

## 后果

### 正面
- 状态变化通过统一的事件通知，便于追踪
- 模块间解耦，订阅者不需要知道发布者的具体实现
- 事件 replay 可以重建任意时间点的状态
- 便于集成监控和告警

### 负面
- 事件系统增加了一层间接性，调试可能更复杂
- 需要规范所有事件触发点，工作量大
- 如果事件回调抛出异常被静默捕获，可能导致状态不一致

## 合规检查

提交前自检：
- [ ] 新增的状态变化是否触发了对应的事件？
- [ ] 是否绕过了事件系统直接修改状态？
- [ ] 新增/修改的事件 schema 是否更新了版本号？