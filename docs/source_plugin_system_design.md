# 播放来源插件系统设计（Source Plugin System）

下面是一份可直接落地的 **Python 播放来源插件系统设计**，满足以下目标：

- 插件之间隔离
- Core 只面向统一接口，不感知 Jellyfin/本地/HTTP 细节

---

## 设计目标与边界

**Core 负责**：
- 队列、调度、设备控制、transport 路由

**Source Plugin 负责**：
- 搜索媒体（search）
- 浏览媒体（browse）
- 解析媒体（resolve）
- 生成可播放 URL（playable）

**关键原则**：
1. Core 只依赖抽象接口 `SourcePlugin`
2. 插件能力通过声明暴露，不靠 if/else 判断具体插件
3. 每个插件有独立配置、状态、缓存、错误熔断
4. 插件异常不影响 Core 和其他插件

---

## 推荐目录结构

```text
xiaomusic/
  core/
    playback_coordinator.py
    queue_manager.py
    transport_router.py
  plugins/
    contracts/
      models.py
      errors.py
      base.py
      capability.py
    manager/
      plugin_manager.py
      registry.py
      config_store.py
      state_store.py
      cache.py
      isolation.py
    sources/
      jellyfin/
        plugin.py
        client.py
        mapper.py
      localfile/
        plugin.py
        scanner.py
      httpurl/
        plugin.py
        resolver.py
  conf/
    plugins/
      jellyfin.yaml
      localfile.yaml
      httpurl.yaml
```

---

## 1) Plugin 基础接口

```python
# plugins/contracts/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import (
    PluginMeta,
    PluginHealth,
    SearchQuery,
    BrowseQuery,
    ResolveRequest,
    MediaItem,
    BrowseNode,
    Playable,
)
from .capability import PluginCapabilitySet

class SourcePlugin(ABC):
    """Core only depends on this contract."""

    @property
    @abstractmethod
    def meta(self) -> PluginMeta: ...

    @property
    @abstractmethod
    def capabilities(self) -> PluginCapabilitySet: ...

    @abstractmethod
    async def on_init(self, ctx: "PluginContext") -> None: ...

    @abstractmethod
    async def on_start(self) -> None: ...

    @abstractmethod
    async def on_stop(self) -> None: ...

    @abstractmethod
    async def health(self) -> PluginHealth: ...

    async def search(self, query: SearchQuery) -> list[MediaItem]:
        raise NotImplementedError("search not supported")

    async def browse(self, query: BrowseQuery) -> list[BrowseNode]:
        raise NotImplementedError("browse not supported")

    @abstractmethod
    async def resolve(self, req: ResolveRequest) -> Playable: ...
```

---

## 2) Plugin 生命周期

统一生命周期状态：

```text
DISCOVERED -> LOADED -> INITIALIZED -> STARTED -> DEGRADED -> STOPPED -> UNLOADED
```

建议流程：
1. **discover**：扫描入口点/目录
2. **load**：导入插件类
3. **init**：注入上下文（配置、日志、缓存、http client）
4. **start**：建立连接/预热
5. **serve**：响应 search/browse/resolve
6. **degrade**：连续失败进入降级态（熔断）
7. **stop/unload**：释放资源、反注册

---

## 3) Plugin 注册机制

支持两种：

1. **静态注册**（简单可靠）
2. **Entry Points 动态注册**（适合三方插件）

```python
# plugins/manager/registry.py
from typing import Dict
from plugins.contracts.base import SourcePlugin

class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: Dict[str, SourcePlugin] = {}

    def register(self, plugin: SourcePlugin) -> None:
        pid = plugin.meta.plugin_id
        if pid in self._plugins:
            raise ValueError(f"duplicate plugin id: {pid}")
        self._plugins[pid] = plugin

    def get(self, plugin_id: str) -> SourcePlugin:
        return self._plugins[plugin_id]

    def list_ids(self) -> list[str]:
        return list(self._plugins.keys())

    def list_plugins(self) -> list[SourcePlugin]:
        return list(self._plugins.values())
```

Core 通过 `PluginManager.resolve(...)` 使用，不直接拿具体类。

---

## 4) Plugin 能力声明

```python
# plugins/contracts/capability.py
from dataclasses import dataclass

@dataclass(frozen=True)
class PluginCapabilitySet:
    can_search: bool = False
    can_browse: bool = False
    can_resolve: bool = True
    supports_live_stream: bool = False
    supports_lyric: bool = False
    supports_cover: bool = False
    supports_prefetch: bool = False
```

Core 调度逻辑示例：
- 用户输入关键词：只在 `can_search=True` 的插件中 fan-out
- 用户指定 `source=jellyfin`：只调用该插件
- URL 输入：仅 `httpurl` 插件 `resolve`

---

## 5) Plugin 配置系统

每插件独立配置模型 + 独立配置文件，避免互相污染。

```python
# plugins/contracts/models.py
from dataclasses import dataclass, field
from typing import Any, Literal

@dataclass(frozen=True)
class PluginMeta:
    plugin_id: str
    name: str
    version: str
    provider: str = "builtin"

@dataclass
class PluginConfig:
    enabled: bool = True
    timeout_sec: float = 8.0
    max_retries: int = 1
    ext: dict[str, Any] = field(default_factory=dict)
```

示例 `conf/plugins/jellyfin.yaml`：
```yaml
enabled: true
timeout_sec: 8
max_retries: 1
ext:
  base_url: "http://127.0.0.1:8096"
  api_key: "xxxx"
  user_id: "xxxx"
```

`PluginManager` 启动时注入对应配置对象，不允许插件直接读全局配置。

---

## 6) Plugin 状态管理

插件状态与业务会话分离，状态集中托管，插件不可直接写其他插件状态。

```python
# plugins/manager/state_store.py
from dataclasses import dataclass, field
from time import time

@dataclass
class PluginRuntimeState:
    status: str = "INITIALIZED"
    last_error: str = ""
    error_count: int = 0
    success_count: int = 0
    last_active_ts: float = field(default_factory=time)
    circuit_open_until_ts: float = 0.0
```

`PluginManager` 维护：
- 每插件健康状态
- 熔断状态
- 最近错误
- 调用计数/耗时指标

---

## 7) Plugin 错误隔离

### 统一异常层次

```python
# plugins/contracts/errors.py
class PluginError(Exception): ...
class PluginConfigError(PluginError): ...
class PluginTimeoutError(PluginError): ...
class PluginUnavailableError(PluginError): ...
class PluginResolveError(PluginError): ...
class PluginNotSupportedError(PluginError): ...
```

### 隔离策略
1. 单插件超时（`asyncio.wait_for`）
2. 单插件熔断（连续失败 N 次，冷却 M 秒）
3. 插件调用包裹器（捕获所有异常并转 `PluginError`）
4. fan-out 搜索时使用 `gather(return_exceptions=True)`，失败插件不影响结果聚合
5. 插件不可共享可变全局对象

---

## 8) Plugin 缓存策略

三层建议：

1. **Resolve 短缓存**（30-120 秒）  
   - 减少重复解析 URL 成本
2. **Search 中缓存**（10-60 秒）  
   - 减少搜索接口抖动
3. **Browse 中缓存**（60-300 秒）  
   - 分类目录变化慢

统一缓存键：
`{plugin_id}:{op}:{hash(query)}`

```python
class PluginCache:
    async def get(self, key: str): ...
    async def set(self, key: str, value, ttl_sec: int): ...
    async def delete_prefix(self, prefix: str): ...
```

插件只能访问自己的 namespace，例如 `jellyfin:*`。

---

## 9) Plugin 热加载是否可行

**可行，但建议分级实现：**

### V1（推荐）：配置热更新 + 插件重启
- 监控 `conf/plugins/*.yaml`
- 触发 `on_stop -> on_init -> on_start`
- 稳定、可控

### V2：代码热加载（开发模式）
- `importlib.reload`
- 风险：旧对象引用、连接资源泄漏、类型漂移
- 仅建议 dev，不建议生产

### 生产建议
- 不做代码级热替换
- 做“平滑重启插件实例”（实例级热切换）

---

## 10) 示例插件实现

下面给三个最小可用插件骨架。

### 10.1 通用模型

```python
# plugins/contracts/models.py (补充)
from dataclasses import dataclass, field
from typing import Any, Literal

@dataclass
class PluginHealth:
    ok: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

@dataclass
class SearchQuery:
    keyword: str
    limit: int = 20

@dataclass
class BrowseQuery:
    path: str = "/"
    page: int = 1
    size: int = 50

@dataclass
class ResolveRequest:
    source_hint: str | None = None
    media_id: str | None = None
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

@dataclass
class MediaItem:
    media_id: str
    source: str
    title: str
    artist: str = ""
    duration_sec: float = 0
    extra: dict[str, Any] = field(default_factory=dict)

@dataclass
class BrowseNode:
    node_id: str
    name: str
    is_dir: bool
    extra: dict[str, Any] = field(default_factory=dict)

@dataclass
class Playable:
    media_id: str
    source: str
    title: str
    stream_url: str
    protocol: Literal["http", "https", "file", "hls"] = "http"
    is_live: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    expire_at_ts: int | None = None
```

### 10.2 HTTP URL 插件（最简单）

```python
# plugins/sources/httpurl/plugin.py
from urllib.parse import urlparse
from plugins.contracts.base import SourcePlugin
from plugins.contracts.models import *
from plugins.contracts.capability import PluginCapabilitySet
from plugins.contracts.errors import PluginResolveError

class HttpUrlSourcePlugin(SourcePlugin):
    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(plugin_id="httpurl", name="HTTP URL Source", version="1.0.0")

    @property
    def capabilities(self) -> PluginCapabilitySet:
        return PluginCapabilitySet(can_search=False, can_browse=False, can_resolve=True, supports_live_stream=True)

    async def on_init(self, ctx): self.ctx = ctx
    async def on_start(self): pass
    async def on_stop(self): pass
    async def health(self) -> PluginHealth: return PluginHealth(ok=True)

    async def resolve(self, req: ResolveRequest) -> Playable:
        if not req.url:
            raise PluginResolveError("url is required")
        p = urlparse(req.url)
        if p.scheme not in ("http", "https"):
            raise PluginResolveError("only http/https supported")
        return Playable(
            media_id=req.url,
            source="httpurl",
            title=req.raw.get("title", req.url),
            stream_url=req.url,
            protocol=p.scheme,
            is_live=req.raw.get("is_live", False),
        )
```

### 10.3 本地文件插件

```python
# plugins/sources/localfile/plugin.py
from pathlib import Path
from plugins.contracts.base import SourcePlugin
from plugins.contracts.models import *
from plugins.contracts.capability import PluginCapabilitySet
from plugins.contracts.errors import PluginResolveError

class LocalFileSourcePlugin(SourcePlugin):
    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(plugin_id="localfile", name="Local File Source", version="1.0.0")

    @property
    def capabilities(self) -> PluginCapabilitySet:
        return PluginCapabilitySet(can_search=True, can_browse=True, can_resolve=True)

    async def on_init(self, ctx):
        self.ctx = ctx
        self.root = Path(ctx.config.ext["music_root"]).resolve()

    async def on_start(self): pass
    async def on_stop(self): pass
    async def health(self) -> PluginHealth: return PluginHealth(ok=self.root.exists())

    async def search(self, query: SearchQuery) -> list[MediaItem]:
        out = []
        for p in self.root.rglob("*"):
            if p.is_file() and query.keyword.lower() in p.name.lower():
                out.append(MediaItem(media_id=str(p), source="localfile", title=p.stem))
                if len(out) >= query.limit:
                    break
        return out

    async def browse(self, query: BrowseQuery) -> list[BrowseNode]:
        base = (self.root / query.path.strip("/")).resolve()
        if not str(base).startswith(str(self.root)):
            return []
        out = []
        for p in base.iterdir():
            out.append(BrowseNode(node_id=str(p), name=p.name, is_dir=p.is_dir()))
        return out

    async def resolve(self, req: ResolveRequest) -> Playable:
        if not req.media_id:
            raise PluginResolveError("media_id required")
        p = Path(req.media_id).resolve()
        if not p.exists() or not str(p).startswith(str(self.root)):
            raise PluginResolveError("file not found or out of root")
        # 注意：给设备播放通常需要 HTTP 可访问 URL，这里只返回 file，Core/transport 可转 proxy
        return Playable(media_id=str(p), source="localfile", title=p.stem, stream_url=str(p), protocol="file")
```

### 10.4 Jellyfin 插件（骨架）

```python
# plugins/sources/jellyfin/plugin.py
from plugins.contracts.base import SourcePlugin
from plugins.contracts.models import *
from plugins.contracts.capability import PluginCapabilitySet
from plugins.contracts.errors import PluginResolveError

class JellyfinSourcePlugin(SourcePlugin):
    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(plugin_id="jellyfin", name="Jellyfin Source", version="1.0.0")

    @property
    def capabilities(self) -> PluginCapabilitySet:
        return PluginCapabilitySet(can_search=True, can_browse=True, can_resolve=True, supports_cover=True)

    async def on_init(self, ctx):
        self.ctx = ctx
        self.client = ctx.http_clients["jellyfin"]

    async def on_start(self): pass
    async def on_stop(self): pass

    async def health(self) -> PluginHealth:
        # ping jellyfin
        return PluginHealth(ok=True)

    async def search(self, query: SearchQuery) -> list[MediaItem]:
        # call jellyfin api -> map
        return []

    async def browse(self, query: BrowseQuery) -> list[BrowseNode]:
        return []

    async def resolve(self, req: ResolveRequest) -> Playable:
        if not req.media_id:
            raise PluginResolveError("media_id required")
        # 通过 Jellyfin API 生成可播放 URL（可能带 token）
        stream_url = f"{self.ctx.config.ext['base_url']}/Audio/{req.media_id}/stream"
        return Playable(
            media_id=req.media_id,
            source="jellyfin",
            title=req.raw.get("title", req.media_id),
            stream_url=stream_url,
            protocol="http",
        )
```

---

## Core 侧如何“无感知”使用插件

```python
class SourceFacade:
    def __init__(self, plugin_manager: "PluginManager"):
        self.pm = plugin_manager

    async def resolve(self, req: ResolveRequest) -> Playable:
        # 1) source_hint 指定插件
        if req.source_hint:
            return await self.pm.call_resolve(req.source_hint, req)

        # 2) 自动匹配（例如 url -> httpurl）
        return await self.pm.auto_resolve(req)

    async def search(self, keyword: str) -> list[MediaItem]:
        return await self.pm.fanout_search(SearchQuery(keyword=keyword, limit=20))
```

Core 只看到 `Playable`，后续交给 transport，不知道来源实现细节。

---

## 最后建议（落地顺序）

1. 先实现 `contracts + manager + httpurl`  
2. 再迁移 `localfile`  
3. 最后迁移 `jellyfin`（外部依赖更复杂）  
4. 加熔断与缓存  
5. 再考虑热加载
