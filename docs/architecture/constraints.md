# 系统宪法 - xiaomusic-core 架构约束

> 版本：1.0
> 发布：2026-05-16
> 依据：ADR-0001 至 ADR-0005

本文档是 xiaomusic-core 的最高约束，所有代码必须遵守。违反即为违规，必须修复或通过 ADR 变更。

---

## 禁止清单（绝对红线）

以下行为**绝对禁止**，任何代码不得违反：

### 1. WebUI 直接访问 runtime 内部状态

```typescript
// ❌ 禁止：WebUI 直接 import runtime
import { getRuntime } from "xiaomusic/relay/runtime";

// ✅ 必须：通过 API
import { fetchPlayerState } from "../services/player";
const state = await fetchPlayerState();
```

**依据**：ADR-0001

### 2. Source 持有 runtime 引用

```python
# ❌ 禁止：SourcePlugin 接受 runtime_provider
class SiteMediaSourcePlugin(SourcePlugin):
    def __init__(self, runtime_provider=None):  # ❌
        self._runtime_provider = runtime_provider

# ✅ 正确：Source 只负责 resolve
class SiteMediaSourcePlugin(SourcePlugin):
    def resolve(self, request: MediaRequest) -> ResolvedMedia:
        # 不调用 runtime、session_manager、stream_server
```

**依据**：ADR-0003

### 3. runtime 特判 source 类型

```python
# ❌ 禁止：runtime 中 if source == "xxx"
if source == "site_media":
    prepared = runtime.prepare_link(...)
    
# ✅ 正确：通过 SourceRegistry 统一分发
plugin = source_registry.get_plugin(source_hint, request)
resolved = await plugin.resolve(request)
```

**依据**：ADR-0003

### 4. API 返回非结构化错误

```python
# ❌ 禁止：裸 raise 或非 ApiError 返回
raise ValueError("invalid request")  # ❌
return {"error": "invalid"}  # ❌

# ✅ 正确：使用 ApiError
raise ApiError(code=40001, message="invalid request", data={})
```

**依据**：ADR-0001

### 5. 绕过 event system 直接修改状态

```python
# ❌ 禁止：直接修改其他模块持有的状态
xiaomusic.device_manager.devices[did].cur_music = "xxx"  # ❌

# ✅ 正确：通过权威接口 + 事件通知
device_player.set_current_track(track_id)
event_bus.publish("PLAYBACK_STARTED", device_id=did, ...)
```

**依据**：ADR-0004、ADR-0005

### 6. 无标注的 silent except

```python
# ❌ 禁止：裸 except: pass
try:
    do_something()
except:  # ❌
    pass

# ✅ 正确：至少记录日志
try:
    do_something()
except Exception as e:
    LOG.warning(f"操作失败: {e}")  # 显式处理
```

**依据**：通用安全规范

### 7. 未经 ADR 修改 API contract / event schema

```python
# ❌ 禁止：直接修改已有 API 响应结构
@router.get("/api/v1/player/state")
def get_player_state():
    return {"state": "playing", "extra": "new_field"}  # 改变了契约

# ✅ 正确：走 ADR 流程，添加新字段通过版本化
```

**依据**：ADR-0001、ADR-0005

---

## 必须清单（强制遵守）

以下行为**必须执行**：

### 1. 所有状态变化通过 event system 通知

任何模块修改了属于自己的状态后，必须发布对应事件：

```python
def set_playing(self, is_playing: bool):
    self._is_playing = is_playing
    self.event_bus.publish(
        "PLAYBACK_STATE_CHANGED",
        device_id=self.did,
        is_playing=is_playing,
        request_id=None  # 如果有的话
    )
```

### 2. 所有 async task 可取消，且有明确 owner

```python
# ✅ 每个创建的 task 必须有 owner 标注
task = asyncio.create_task(self._do_recovery(), name="auth_recovery")
task._owner = "auth_manager"  # 标注 owner

# 在 shutdown 时取消
def shutdown(self):
    for task in asyncio.all_tasks():
        if getattr(task, "_owner", None) == "auth_manager":
            task.cancel()
```

### 3. 每个状态有唯一权威

| 状态 | 权威模块 | 访问方式 |
|---|---|---|
| 播放状态 | device_player | device_player.get_state() |
| 播放列表 | music_library | music_library.get_playlist_items() |
| 认证状态 | auth | auth.get_status() |
| 流会话 | session_manager | session_manager.update_state() |

不得在其他模块中直接持有或修改上述状态。

### 4. 所有生命周期明确 ownership

```python
# ✅ 每个组件在 __init__ 中明确自己的生命周期责任
class RelayRuntime:
    def __init__(self, ...):
        self._owns_stream_server = True
        self._owns_audio_streamer = True
    
    def shutdown(self):
        if self._owns_stream_server:
            self.stream_server.stop()
        if self._owns_audio_streamer:
            self.audio_streamer.stop()
```

### 5. 所有临时方案必须标注 TEMP-HACK

```python
# ✅ 临时方案必须包含注释
# TEMP-HACK:
# reason: Jellyfin API 返回的 URL 在某些情况下缺少协议前缀
# remove_after: v1.3 / Jellyfin API 修复后
# related_issue: #456
# owner: @shinpei
if not url.startswith("http"):
    url = "https://" + url
```

---

## TEMP-HACK 治理规范

### 格式要求

每个 TEMP-HACK 必须包含：

```python
# TEMP-HACK:
# reason: <为什么这样写>
# remove_after: <vX.Y / 某个功能完成后 / 某个 bug 修完后>
# related_issue: <issue 号或链接>
# owner: <负责人>
```

### 治理规则

1. **必须登记**：所有 TEMP-HACK 必须有 owner，不得无人认领
2. **必须有时限**：remove_after 必须有具体条件，不能写"以后"
3. **定期清理**：每个 sprint 回顾时检查 TEMP-HACK 列表
4. **禁止嵌套**：不允许 TEMP-HACK 中再套 TEMP-HACK
5. **不得扩散**：TEMP-HACK 不得作为新代码的参考模板

---

## 违规处理

### 违规发现

- **CI 检查**：PR 必须通过架构合规检查（静态分析规则）
- **Code Review**：Reviewer 有权要求修复违规代码
- **自我检查**：提交前对照本宪法自检

### 违规处理

1. **立即修复**：违规代码必须修复后才能合并
2. **回滚优先**：如果合入后才发现严重违规，应优先回滚
3. **ADR 豁免**：如果业务确实需要临时突破，需要创建 ADR 并经审批

---

## 相关文档

- [ADR-0001: API 作为唯一正式边界](../adr/0001-api-boundary.md)
- [ADR-0002: Runtime 职责边界](../adr/0002-runtime-ownership.md)
- [ADR-0003: Source 抽象职责边界](../adr/0003-source-abstraction.md)
- [ADR-0004: 状态权威单一化](../adr/0004-state-authority.md)
- [ADR-0005: 统一事件模型](../adr/0005-event-model.md)
- [State Authority 偏差表](./state-authority.md)
