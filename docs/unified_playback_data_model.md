# 统一播放数据模型设计（Python）

本文设计一套统一数据模型，使 Core 不依赖具体来源（Jellyfin / 本地文件 / HTTP URL），只处理标准对象。

---

## 1. 对象用途

- `MediaItem`：来源侧内容实体，描述稳定元数据（标题、艺术家、来源 ID、时长等），不保证当前可播放。
- `Playable`：`MediaItem` 在某一时刻可播放的快照，包含播放 URL、过期时间、请求头等。
- `PlaySession`：一次播放任务的运行态，记录设备、状态流转、重试/回退、错误。
- `Device`：设备静态与半静态信息（did、型号、网络标识、分组等）。
- `DeviceCapability`：设备在当前环境可执行的能力集合，用于 transport 路由与回退策略。

---

## 2. Python dataclass 示例

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SourceType(str, Enum):
    JELLYFIN = "jellyfin"
    LOCAL = "local"
    HTTP = "http"


class SessionState(str, Enum):
    CREATED = "created"
    RESOLVING = "resolving"
    READY = "ready"
    DISPATCHING = "dispatching"
    PLAYING = "playing"
    FAILED = "failed"
    STOPPED = "stopped"
    FINISHED = "finished"


@dataclass(frozen=True)
class MediaItem:
    media_id: str                    # 全局唯一ID（可由 source + source_item_id 生成）
    source: SourceType
    source_item_id: str              # 来源内部ID（Jellyfin Item ID / filepath / URL）
    title: str
    artist: str = ""
    album: str = ""
    duration_sec: float = 0.0
    cover_url: str = ""
    lyric_ref: str = ""             # URL / path / key
    tags: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Playable:
    playable_id: str
    media_id: str
    source: SourceType
    stream_url: str
    protocol: str                    # http / https / hls / file
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    is_live: bool = False

    # URL 生命周期
    issued_at_ts: int = 0
    expires_at_ts: int = 0           # 0 表示未知或不过期
    refresh_token: str = ""         # 可选：用于快速刷新
    refresh_hint: str = ""          # re-resolve / signed-url / jellyfin-token

    # 失败恢复
    can_retry: bool = True
    max_retries: int = 1
    fallback_urls: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceCapability:
    can_play_via_mina: bool = True
    can_play_via_miio: bool = False
    can_tts_via_miio: bool = True
    can_stop_via_miio: bool = True
    can_pause_via_miio: bool = True
    can_set_volume_via_miio: bool = True
    supports_seek: bool = False
    supports_progress_query: bool = False
    min_volume: int = 0
    max_volume: int = 100


@dataclass
class Device:
    did: str
    device_id: str = ""
    name: str = ""
    model: str = ""
    ip: str = ""
    group: str = ""
    online: bool = True
    capability: DeviceCapability = field(default_factory=DeviceCapability)
    last_seen_ts: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionError:
    code: str
    message: str
    stage: str                       # resolve / dispatch / stream / control
    retriable: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlaySession:
    session_id: str
    state: SessionState
    device_did: str
    media: MediaItem
    playable: Playable | None = None

    created_at_ts: int = 0
    updated_at_ts: int = 0
    started_at_ts: int = 0
    finished_at_ts: int = 0

    position_sec: float = 0.0
    volume: int | None = None
    retry_count: int = 0
    fallback_count: int = 0

    last_error: SessionError | None = None
    errors: list[SessionError] = field(default_factory=list)
    route_history: list[str] = field(default_factory=list)  # e.g. ["mina", "miio"]
    extra: dict[str, Any] = field(default_factory=dict)
```

---

## 3. 对象关系

```text
MediaItem (稳定内容元数据)
   └─ resolve -> Playable (短生命周期可播快照)

Device
   └─ has -> DeviceCapability

PlaySession
   ├─ references -> Device.did
   ├─ references -> MediaItem
   └─ holds -> Playable (当前使用中的播放地址)
```

关系要点：

- 一个 `MediaItem` 可对应多个 `Playable`（不同时间解析、不同 token URL）。
- 一个 `Device` 在不同网络环境下 `DeviceCapability` 可以动态更新。
- 一个 `PlaySession` 通常绑定一个 `MediaItem`，但可在回退时切换 `Playable`。

---

## 4. URL 生命周期设计

建议按 URL 类型管理：

1. 永久型（例如本地 file/proxy URL）
   - `expires_at_ts = 0`
2. 短期签名型（例如 Jellyfin 或带签名的 HTTP URL）
   - 必须带 `issued_at_ts/expires_at_ts`
3. TTL 未知型（普通 HTTP 直链）
   - 赋予保守默认 TTL（例如 300 秒）

状态机建议：

```text
NEW -> VALID -> NEAR_EXPIRY -> EXPIRED -> REFRESHING -> VALID/FAILED
```

触发规则：

- `now >= expires_at_ts - refresh_window_sec`：进入 `NEAR_EXPIRY`
- `now >= expires_at_ts`：进入 `EXPIRED`

---

## 5. URL 过期刷新机制

统一给 Source Plugin 两个入口：

```python
class SourcePluginProtocol:
    async def resolve(self, media: MediaItem) -> Playable: ...
    async def refresh(self, playable: Playable) -> Playable: ...
```

刷新策略：

1. 预刷新窗口：`refresh_window_sec = min(60, ttl * 0.2)`
2. 刷新顺序：
   - 先 `refresh(playable)`（低成本）
   - 失败再 `resolve(media)`（重解析）
3. 并发去重：同一 `media_id` 刷新加锁，避免重复请求
4. 刷新失败：触发播放回退（见下节）

---

## 6. 播放回退机制

两层回退：

### 6.1 URL 回退

- 首选 `playable.stream_url`
- 失败后尝试 `playable.fallback_urls`
- 仍失败则执行 `refresh -> re-resolve`

### 6.2 Transport 回退

- `play_url`：默认 `Mina -> Miio(若支持)`
- `stop/pause/tts/set_volume`：默认 `Miio -> Mina`

回退状态记录建议写入 `PlaySession`：

- `fallback_count`
- `route_history`
- `errors[]`

停止回退条件：

- `retry_count >= playable.max_retries`
- 错误不可重试（如 `E_BAD_REQUEST`、`E_NOT_FOUND`、永久鉴权失败）

---

## 7. Metadata 缓存策略

建议三层缓存：

1. L1（进程内内存）
   - key: `media:{source}:{source_item_id}`
   - TTL: 10~30 分钟
2. L2（持久层：Redis/SQLite/文件）
   - TTL: 1~24 小时（按来源调节）
3. Negative Cache（空结果缓存）
   - TTL: 30~120 秒，防止缓存击穿

字段缓存建议：

- 稳定字段（`title/artist/album/duration`）：长 TTL
- 易变字段（签名 `cover_url`、临时歌词 URL）：短 TTL 或不缓存

失效策略：

- 手动刷新
- 来源变更事件触发（本地文件变更/Jellyfin webhook）
- 播放失败并判定 metadata 脏数据时定向失效

---

## 8. 设计收益

该模型可保证：

- Core 输入输出统一：`Device + MediaItem -> Playable -> PlaySession`
- 来源实现解耦：Jellyfin/本地/HTTP 仅在插件内实现
- 会话可观测：重试、回退、错误可追踪
- URL 可治理：过期可预测、刷新可控、失败可回退
