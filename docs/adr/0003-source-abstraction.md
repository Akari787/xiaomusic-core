# ADR-0003: Source 抽象职责边界

## 状态：已接受

## 上下文

Source 系统负责"获取音乐"，当前有 4 个内置插件：
- `JellyfinSourcePlugin`：从 Jellyfin 音乐库
- `DirectUrlSourcePlugin`：直接 URL
- `LocalLibrarySourcePlugin`：本地音乐库
- `SiteMediaSourcePlugin`：站点媒体（YouTube/Bilibili）

**核心问题**：`SiteMediaSourcePlugin` 通过 `runtime_provider` 间接持有 `RelayRuntime` 引用，调用 `runtime.prepare_link()`。

这违反了 Source 的抽象边界：
- Source 应该只负责"把 query 解析为 stream_url"
- "播放准备"（prepare_link）不应该在 Source 层执行

现状：
```
SiteMediaSourcePlugin.resolve()
  → runtime_provider() → RelayRuntime.prepare_link()  ← 越界！
  → Session 创建、流管理 ← 不应该在 Source 层
```

理想：
```
Source.resolve() → 返回 ResolvedMedia(stream_url)
(播放编排层) → 使用 stream_url 创建 Session、开始流传输
```

## 决策

**Source 插件只负责"获取音乐"（解析 query → stream_url），不负责播放编排、流会话管理、或持有 runtime 引用。**

### SourcePlugin 抽象约束

```python
class SourcePlugin(ABC):
    name = "base"

    def can_resolve(self, request: MediaRequest) -> bool:
        """判断这个插件是否能解析这个请求"""
        return False

    @abstractmethod
    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        """解析请求，返回媒体信息（不涉及播放编排）"""
        raise NotImplementedError

    async def search(self, query: str, limit: int = 20) -> list[dict]:
        raise NotImplementedError

    async def browse(self, path: str, page: int = 1, size: int = 50) -> list[dict]:
        raise NotImplementedError
```

**禁止**：
- `SourcePlugin` 的 `__init__` 不得接受 `runtime_provider` 参数
- `SourcePlugin.resolve()` 不得调用任何 `runtime`、`session_manager`、`stream_server` 相关的方法
- 插件不得持有 `xiaomusic` 实例引用

### 播放准备的归属

`SiteMediaSourcePlugin` 中的 `runtime.prepare_link()` 调用需要重构：

**方案**：将"播放准备"逻辑移出 Source，放在 `PlaybackCoordinator.play()` 之后：

```
PlaybackCoordinator.play()
  → SourceRegistry.get_plugin()
  → plugin.resolve() → ResolvedMedia(stream_url)
  → DeliveryAdapter.prepare_plan(resolved)
  → [播放编排] → 创建 Session、开始流传输
```

如果某些 Source 需要特殊的前置准备（如 Bilibili 的 stream_url 本身包含 session 信息），应在 `ResolvedMedia` 中通过 `context` 字段传递：

```python
@dataclass
class ResolvedMedia:
    stream_url: str
    context: dict[str, Any]  # 传递额外元数据，如 session_id、headers
```

## 后果

### 正面
- Source 插件可独立测试（mock `ResolvedMedia` 返回）
- Source 插件可以真正"热插拔"，不依赖 runtime
- 播放编排逻辑集中在一处，易于理解和修改

### 负面
- `SiteMediaSourcePlugin` 需要重构，"播放准备"逻辑移出
- 如果 `ResolvedMedia.context` 包含 session 信息，需要设计清楚 session 的生命周期
- 某些场景（如直接播放 URL）的流程会变长

## 合规检查

提交前自检：
- [ ] 新增 Source 插件是否接受了 `runtime_provider` 或 `xiaomusic` 参数？
- [ ] `SourcePlugin.resolve()` 是否调用了 runtime、session 相关方法？
- [ ] 是否有 `if source == "xxx"` 或 `hasattr(source, ...)` 的特判代码？