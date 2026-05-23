# ADR-0002: Runtime 职责边界

## 状态：已接受

## 上下文

在 S2-5 审计中发现 `RelayRuntime`（358行）承担了 **6 个不同领域** 的职责：

1. 音频流服务（`LocalHttpStreamServer`）
2. 会话管理（`StreamSessionManager`）
3. 播放策略（relay/direct/proxy 选择）
4. 投屏编排（play + cast 组合）
5. 缓存管理（`ResolverCache`）
6. 健康检查

同时 `xiaomusic.xiaomusic` 作为主应用对象，承担了 **9+ 个职责**。

这种"上帝对象"模式导致：
- 代码难以理解（一个类做太多事）
- 难以独立测试（依赖太多组件）
- 难以独立演进（修改一处可能影响多处）
- 新成员上手成本高

## 决策

**Runtime 组件只负责其核心职责，不得承担编排、策略、业务流程相关的职责。**

### RelayRuntime 的职责范围

保留：
- 音频流服务（`LocalHttpStreamServer`）
- 会话管理（`StreamSessionManager`）
- 音频流处理（`AudioStreamer`）
- 解析器缓存（`ResolverCache`）
- 健康检查（`healthz`、`sessions`、`cleanup`）

移除（重构到其他层）：
- **播放策略选择**：提取到 `PlaybackStrategyRouter`，判断用 relay/direct/proxy
- **投屏编排**：提取到 `PlayAndCastOrchestrator`，负责"播放 + 投屏"组合
- **LinkPlaybackStrategy 获取**：通过构造函数注入，不从 `xiaomusic` 获取

### 依赖注入原则

所有 Runtime 持有的外部依赖应通过构造函数注入，使用接口协议（Protocol）解耦：

```python
class PlaybackStrategyProvider(Protocol):
    def get_strategy(self) -> LinkPlaybackStrategy: ...

class RelayRuntime:
    def __init__(
        self,
        config_provider: ConfigProvider,
        playback_strategy_provider: PlaybackStrategyProvider | None = None,
    ):
```

## 后果

### 正面
- RelayRuntime 职责清晰，可独立测试
- 播放策略可独立演进（新增策略不需要改 RelayRuntime）
- `xiaomusic` 主对象的依赖减少，更容易理解

### 负面
- 重构成本较高，需要移动代码、修改调用方
- 短期增加了接口层的间接性
- 需要确保注入的依赖在 Runtime 生命周期内有效

## 合规检查

提交前自检：
- [ ] 新代码是否让 RelayRuntime 承担了新职责（> 6 个领域）？
- [ ] 是否有代码直接绕过新接口调用底层组件？
- [ ] 新增的构造函数参数是否使用了 Protocol 接口？