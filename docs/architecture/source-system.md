# S2-4: Source 系统审计

> 审计时间：2026-05-16
> 任务：s2-def（边界审计）
> 前提：阶段1完成

## 1. Source 抽象层

### 1.1 核心类

| 类 | 文件 | 职责 |
|---|---|---|
| `SourcePlugin` | `xiaomusic/core/source/source_plugin.py` | 抽象基类，定义 `can_resolve/resolve/search/browse` |
| `SourceRegistry` | `xiaomusic/core/source/source_registry.py` | 插件注册表，只负责查找，不持有 runtime |

### 1.2 内置插件

| 插件 | 注册位置 | 职责 |
|---|---|---|
| `JellyfinSourcePlugin` | `default_registry.py` | 从 Jellyfin 音乐库获取媒体 |
| `DirectUrlSourcePlugin` | `default_registry.py` | 直接 URL（http/https/文件路径） |
| `LocalLibrarySourcePlugin` | `default_registry.py` | 本地音乐库扫描 |
| `SiteMediaSourcePlugin` | `default_registry.py` | 站点媒体（YouTube/Bilibili 等） |

## 2. Source 插件化程度评估

### 2.1 正面：设计清晰

- **SourcePlugin 抽象干净**：只定义 `resolve/search/browse`，没有要求插件持有 runtime
- **SourceRegistry 只负责查找**：不持有 runtime 实例，不承担编排职责
- **无 `if source ==` 判断**：代码库中没有 `if source == "xxx"` 类型的 source-type 特判
- **无 `hasattr(source, ...)` 判断**：未发现插件属性探测模式
- **SourceRegistry._infer_hint 自动推断**：通过 URL 模式或文件扩展名自动推断 source 类型，无需硬编码

### 2.2 已解决：防腐层协议

**历史问题（已修复）**：`SiteMediaSourcePlugin` 曾通过 `runtime_provider` 持有 `RelayRuntime` 引用，用反射调用 `prepare_link`。

**方案B修复（2026-05-23）**：提取 `LinkPreparer` Protocol 接口，消除反射和 runtime 直接依赖。

```python
# xiaomusic/core/source/source_protocols.py
@runtime_checkable
class LinkPreparer(Protocol):
    """播放准备服务接口（防腐层）"""
    def prepare_link(self, url: str, prefer_proxy: bool = False, *, no_cache: bool = False) -> dict[str, object]:
        ...

# xiaomusic/adapters/sources/site_media_source_plugin.py
class SiteMediaSourcePlugin(SourcePlugin):
    def __init__(self, ..., link_preparer: LinkPreparer | None = None, ...):
        self._link_preparer = link_preparer  # ← Protocol 接口，非具体类型

    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        if self._link_preparer is not None and request.device_id:
            prepared = self._link_preparer.prepare_link(...)  # 直接调用，无反射
```

- `RelayRuntime` 通过 structural subtyping 自动满足 `LinkPreparer` Protocol
- 不会引入对 relay 模块的类型硬依赖
- `default_registry.py` 中 `SiteMediaSourcePlugin(link_preparer=link_preparer)` 不再暴露 runtime 语义

### 2.3 其他 source 相关代码位置

| 文件 | 用途 | 与 runtime 关系 |
|---|---|---|
| `xiaomusic/adapters/sources/jellyfin_source_plugin.py` | Jellyfin 音乐源 | 通过 `_resolve_jellyfin_source_url(xiaomusic)` 获取配置，依赖 `xiaomusic.music_library` 和 `xiaomusic.online_music_service` |
| `xiaomusic/adapters/sources/local_library_source_plugin.py` | 本地音乐库 | 直接持有 `music_library` 引用 |
| `xiaomusic/adapters/sources/direct_url_source_plugin.py` | 纯 URL | 无 runtime 依赖，最干净 |
| `xiaomusic/core/delivery/delivery_adapter.py` | 播放流投递 | 无 source 类型判断 |
| `xiaomusic/core/transport/transport_router.py` | 传输路由 | 无 source 类型判断 |

## 3. Source 与播放器/runtime 的边界

### 3.1 正确边界

```
MediaRequest → SourceRegistry.get_plugin() → SourcePlugin.resolve() → ResolvedMedia
                                              ↓
                                    不涉及播放器控制
                                              ↓
                                    不涉及 auth 状态
                                              ↓
                                    不涉及 runtime 生命周期
```

### 3.2 实际边界（已修复）

```
MediaRequest → SourceRegistry.get_plugin() → SiteMediaSourcePlugin.resolve()
                                                        ↓
                                              link_preparer.prepare_link()
                                              (LinkPreparer Protocol → RelayRuntime)
```

`SiteMediaSourcePlugin` 通过 `LinkPreparer` Protocol 接口与 runtime 交互，不再持有具体类型引用。

## 4. 插件化障碍点汇总

| 位置 | 障碍类型 | 描述 | 状态 |
|---|---|---|---|
| `SiteMediaSourcePlugin.__init__` | runtime 持有 | 曾通过 `runtime_provider` 间接持有 RelayRuntime 引用 | 已修复（LinkPreparer Protocol） |
| `default_registry.py` | 同步注入 | `get_runtime()` 在注册时同步执行，如果 xiaomusic 尚未初始化可能出问题 | 已缓解（lazy factory） |

## 5. 建议

1. **已完成**：`SiteMediaSourcePlugin` 已通过 `LinkPreparer` Protocol 解耦，不再直接依赖 `RelayRuntime` 类型
2. **长期**：将“播放准备”逻辑从 Source 插件中移除，Source 只负责 resolve，播放编排由上层（PlaybackCoordinator）负责
3. **SourceRegistry 层面**：`JellyfinSourcePlugin` 的 `_resolve_jellyfin_source_url(xiaomusic)` 闭包捕获了 `xiaomusic`，属于隐式依赖，建议显式传递所需字段（URL 配置、music_library）