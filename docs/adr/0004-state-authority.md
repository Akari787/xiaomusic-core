# ADR-0004: 状态权威单一化

## 状态：已接受

## 上下文

在 S2.5 质量门禁审计中，发现以下状态权威偏差问题：

| 状态 | 问题 |
|---|---|
| 播放列表（_play_list vs music_list） | `device_player` 和 `music_library` 各持有一份，内容可能不同步 |
| 设备属性（cur_music, entity_id） | `device_player` 和 `device.model` 同时持有，可直接修改 model |
| session 状态 | `RelayRuntime` 绕过 `session_manager` 直接修改 session |
| 异步任务（running_task） | 分散在多个模块，无统一 owner 追踪 |

**根本问题**：没有明确"每个状态的唯一权威"原则，导致同一份数据被多处持有和修改。

## 决策

**每个状态有且只有一个权威来源，所有状态变化必须通过权威对应的接口，不允许跨层直接修改。**

### 状态权威原则

1. **单一权威**：每个状态只有一个模块对其负全责
2. **显式接口**：状态的读写必须通过该模块暴露的接口
3. **禁止越界修改**：不允许 A 模块直接修改 B 模块持有的状态
4. **状态变化通过事件**：状态变化时触发事件通知，不依赖调用方主动查询

### 已识别的状态权威归属

| 状态 | 权威模块 | 访问方式 |
|---|---|---|
| 播放状态（is_playing） | `device_player.py` | `device_player.get_state()` |
| 播放列表 | `music_library.py` | `music_library.get_playlist_items()` |
| 播放队列快照与当前索引（当前设备 session） | `device_player.py` | 设备导航接口与状态快照；调用方不得直接计算或修改索引 |
| 认证状态 | `auth.py` | `auth.get_status()` |
| 设备列表 | `device_manager.py` | `device_manager.list_devices()` |
| 流会话状态 | `stream_session_manager.py` | `session_manager.update_state()` 等 API |
| 配置 | `config.py` | `config.get()` |
| 事件订阅 | `event_bus.py` | `event_bus.subscribe()` |

### 播放队列补充决策（2026-07-23）

`music_library` 与 `device_player` 的职责必须区分：

- `music_library` 拥有歌单 membership 与 entity record 的事实。
- `device_player` 从上述事实建立当前设备 session 的运行时队列快照，并独占当前索引。
- 随机模式在新 session 建立时生成稳定的打乱快照；next/previous 只移动索引。
- WebUI、Facade、Coordinator 与 transport 均不得自行计算下一首曲目或重建队列。
- UI next、系统控制 next 与自动完成最终必须汇入同一个 `device_player` 导航入口。

这不是播放列表双写：运行时快照服务于 session 顺序与历史回退，不能反向作为歌单成员事实写回 `music_library`。

### 禁止的模式

```python
# 禁止：跨模块直接修改状态
class BadPlayer:
    def play(self):
        self.xiaomusic.device_manager.devices[did].cur_music = "xxx"  # ❌ 直接修改其他模块状态

# 禁止：同一数据多处持有
class BadPlayer:
    def __init__(self):
        self._play_list = []  # music_library 也有自己的 music_list

# 禁止：绕过 manager 直接修改内部状态
relay_runtime._session_manager.sessions[sid].state = "stopped"  # ❌ 应通过 session_manager.update_state()
```

### 正确的模式

```python
# 正确：通过权威接口修改状态
self.event_bus.publish("PLAYER_STATE_CHANGED", device_id=did)  # 通知
# device_player 内部监听事件，更新自己的状态

# 正确：通过统一的 session manager 接口
session_manager.update_state(sid, "stopped", error_code=None)
```

## 后果

### 正面
- 状态变化路径唯一可追踪
- 便于测试（mock 权威接口）
- 减少隐式状态依赖，降低调试难度
- 为未来重构提供清晰边界

### 负面
- 重构成本高（需要修改跨层直接访问的代码）
- 某些"快速修复"场景需要额外编码（通过接口而非直接访问）
- 短期可能影响性能（间接访问 vs 直接访问）

## 合规检查

提交前自检：
- [ ] 新代码是否在某模块中直接修改了其他模块持有的状态？
- [ ] 是否引入了新的状态双写（某状态被两个模块同时持有）？
- [ ] session 状态修改是否通过 `session_manager` 接口？
- [ ] next/previous 是否只发送控制意图，并由 `device_player` 移动当前队列索引？
- [ ] 是否错误地在 next/previous 中调用了新播放入口、重新解析歌单或重新洗牌？

相关控制不变量见 [`../architecture/playback-control-model.md`](../architecture/playback-control-model.md)。

## T07 补充决策（2026-07-28）

- `PlaybackRuntimeState.phase` 是运行时播放状态权威；`LifecycleToken` 分别表达 queue session、command generation、track attempt。
- `PlaybackTaskRegistry` 是 playback async task 的 owner；opaque task properties 仅为 test/legacy bridge，不承担 owner 职责。
- `_last_cmd` 只允许作为 legacy diagnostic/logging，禁止参与 Facade snapshot、failure/retry/completion 判断。
- `_play_session_id` 只用于 async media session invalidation，不复用为 queue/command/attempt ID。
- 正常 completion 唯一由 duration timer expiry gate 负责。confirmation 只观察，不切歌；保留 T05 的 two-false 与 unknown grace 行为。
- 查询路径必须纯读：状态异常不得写状态、创建/取消任务、调用 Mina 或隐式修复。