# S2-5: Runtime 边界审计

> 审计时间：2026-05-16
> 任务：s2-def（边界审计）
> 前提：阶段1完成

## 1. 核心 Runtime 文件定位

**注意**：项目架构中主要承担 runtime 职责的不是 `xiaomusic/core/runtime.py`（该文件不存在），而是：

| 类 | 文件 | 行数 | 主要职责 |
|---|---|---|---|
| `xiaomusic.xiaomusic` | `xiaomusic/xiaomusic.py` | ~1200+ | 主应用对象，全局状态持有者 |
| `RelayRuntime` | `xiaomusic/relay/runtime.py` | 358 | 网络音频流、会话管理、播放策略 |
| `PlaybackCoordinator` | `xiaomusic/core/coordinator/playback_coordinator.py` | ~380 | 播放链路编排 |

本文档重点审计 **`RelayRuntime`**（行数可控）和 **`xiaomusic.xiaomusic`** 的职责边界。

## 2. RelayRuntime 审计（358 行）

### 2.1 方法清单与职责分类

| 方法 | 行 | 职责领域 | 是否应该属于 Runtime |
|---|---|---|---|
| `__init__` | 1-50 | 组件初始化 | ✅ |
| `ensure_started` | 51-53 | 流服务器启动 | ✅ |
| `_public_base` | 55-56 | URL 构建 | ⚠️ 辅助 |
| `_internal_stream_url` | 58-59 | URL 构建 | ⚠️ 辅助 |
| `_external_stream_url` | 61-62 | URL 构建 | ⚠️ 辅助 |
| `play_and_cast` | 64-104 | 播放+投屏编排 | ❌ 应由上层编排 |
| `play_link` | 106-134 | 播放策略选择（relay/direct/proxy） | ❌ 应由策略层负责 |
| `prepare_link` | 136-175 | 播放准备（link 级别） | ⚠️ 部分越界 |
| `healthz` | 177-186 | 健康检查 | ✅ |
| `sessions` | 188-195 | 会话列表 | ✅ |
| `cleanup_sessions` | 197-201 | 会话清理 | ✅ |
| `stream_chunks` | 203-224 | 流数据读取 | ✅ |
| `stop_session` | 226-230 | 停止会话 | ✅ |
| `sweep_idle_sessions` | 232-267 | 空闲会话回收 | ✅ |
| `_is_stream_level_error` | 269-276 | 错误分类 | ⚠️ 辅助 |
| `_invalidate_cache_on_stream_failure` | 278-281 | 缓存失效 | ⚠️ 辅助 |
| `_on_stream_failed` | 283-290 | 错误回调 | ⚠️ 辅助 |
| `_active_session_limit` | 292-299 | 活跃会话限制 | ⚠️ 配置逻辑 |
| `_stop_oldest_active_session` | 301-320 | 会话淘汰 | ✅ |

### 2.2 职责领域分析

RelayRuntime 实际承担了 **6 个不同领域** 的职责：

1. **音频流服务**：流服务器管理（`LocalHttpStreamServer`）
2. **会话管理**：会话生命周期（`StreamSessionManager`）
3. **播放策略**：`play_link` 中的 relay/direct/proxy 模式选择
4. **投屏编排**：`play_and_cast` 中的"播放+投屏"组合逻辑
5. **缓存管理**：ResolverCache 和失效逻辑
6. **健康检查与监控**：healthz、sessions、cleanup

### 2.3 God Object 症状评估

RelayRuntime 有 **中等程度的 God Object 症状**：

- 承担 6 个不同领域的职责（正常单一职责对象应 ≤3 个领域）
- `play_and_cast` 和 `play_link` 混合了"播放编排"和"播放策略选择"，这两个职责不应在同一层
- `_stop_oldest_active_session` 和 `sweep_idle_sessions` 会同时操作会话管理器和音频流，存在隐式调用链

### 2.4 与其他模块的依赖关系

```
RelayRuntime 依赖：
├── xiaomusic.xiaomusic（通过 self.xiaomusic）
│   ├── xiaomusic.config（获取公开 URL、device ID 配置）
│   ├── xiaomusic.music_library（通过 link_playback_strategy）
│   └── xiaomusic.play_url（投屏调用）
├── LocalHttpStreamServer（自身创建的子组件）
├── StreamSessionManager（自身创建的子组件）
├── AudioStreamer（自身创建的子组件）
├── Resolver / ResolverCache / RelayPlayService（自身创建的子组件）
└── LinkPlaybackStrategy（从 xiaomusic 获取或创建）
```

RelayRuntime **直接持有 `xiaomusic` 引用**（通过 `self.xiaomusic`），这意味着：
- RelayRuntime 可以访问 xiaomusic 的任何公开属性/方法
- 如果 xiaomusic 的公开 API 变更，RelayRuntime 可能受影响
- 这是跨层依赖：RelayRuntime 不应该需要知道"播放策略"在哪里

## 3. xiaomusic.xiaomusic 审计（主应用对象）

> 由于文件行数较多，仅通过 grep 和 import 分析结构

### 3.1 职责领域（通过 grep 推断）

| 职责领域 | 证据 |
|---|---|
| 配置管理 | `self.config` |
| 音乐库管理 | `self.music_library` |
| 设备管理 | `self.device_manager` |
| 播放器控制 | `self.play_url()` |
| 认证管理 | `self.auth` |
| WebSocket/SSE | 通过 api 模块暴露 |
| 定时任务 | `self.crontab` |
| Source 插件管理 | 通过 source_registry |
| 播放协调 | 通过 PlaybackFacade |

**主应用对象承担了 9+ 个职责领域**，是典型的**超级上帝对象**。

### 3.2 与 Runtime 的边界模糊

- `xiaomusic.xiaomusic` 本身既是"应用入口"又是"状态聚合器"
- `get_runtime()` 函数依赖 `xiaomusic` 实例来构建 `RelayRuntime`
- RelayRuntime 又反过来持有 `xiaomusic` 引用，形成**双向依赖**

```
xiaomusic.xiaomusic ←───持有──→ RelayRuntime
```

## 4. 危险信号汇总

| 信号 | 严重程度 | 位置 | 描述 |
|---|---|---|---|
| God Object | 中 | `xiaomusic.xiaomusic` | 主应用对象承担 9+ 职责领域 |
| 双向依赖 | 中 | `relay/runtime.py` ↔ `xiaomusic.py` | RelayRuntime ↔ xiaomusic 互相持有 |
| 职责混合 | 低 | `RelayRuntime.play_link` | 播放策略选择 + 播放执行在同一方法内 |
| 跨层调用 | 中 | `RelayRuntime.play_and_cast` | 投屏（xiaomusic.play_url）和流管理在同一方法 |
| 缓存逻辑内联 | 低 | `RelayRuntime` | 缓存失效逻辑与流管理紧耦合 |

## 5. 建议

1. **RelayRuntime 拆分建议**：
   - 提取 `PlaybackStrategyRouter` 负责 relay/direct/proxy 策略选择
   - 提取 `PlayAndCastOrchestrator` 负责"播放+投屏"组合编排
   - RelayRuntime 只保留：流服务、会话管理、音频流、解析器

2. **解除双向依赖**：
   - RelayRuntime 不应持有 `xiaomusic` 引用，应只依赖必要的接口（如 `ConfigProvider`）
   - `link_playback_strategy` 应通过构造函数注入，而非从 `xiaomusic` 获取

3. **xiaomusic.xiaomusic 拆分建议**（长期）：
   - 将认证、设备管理、Source 插件管理、WebSocket/SSE 分别拆分为独立 Manager
   - 主应用对象只负责协调，不承担具体业务逻辑