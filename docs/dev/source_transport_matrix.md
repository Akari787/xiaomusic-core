# Source / Transport 能力矩阵（v1.1.0 Phase 1）

## 1. 主线流转图

```text
MediaRequest
  -> SourcePlugin.resolve()
  -> ResolvedMedia
  -> Runtime.prepare()           (DeliveryAdapter)
  -> PreparedStream
  -> Transport.deliver()         (TransportRouter + Transport)
  -> PlaybackOutcome
```

对应实现：

- `MediaRequest`：`xiaomusic/core/models/media.py`
- `SourcePlugin.resolve()`：`xiaomusic/core/source/source_plugin.py`
- `Runtime.prepare()`：`xiaomusic/core/delivery/delivery_adapter.py`
- `Transport.deliver()`：`xiaomusic/core/transport/transport_router.py`
- `PlaybackOutcome`：`xiaomusic/core/models/media.py`

---

## 2. SourcePlugin 语义

### 2.1 resolve 的职责

- 输入统一 `MediaRequest`
- 输出统一 `ResolvedMedia`
- 只做来源解析与媒体可播信息确定

### 2.2 输出约束

- 必须提供可追踪来源字段（`source`）
- 必须返回可用于后续 prepare 的媒体数据（`stream_url` 等）
- 不允许把 transport 选择写入来源结果

### 2.3 不应承担的职责

- 不直接调用设备传输层
- 不负责设备 reachability 写入
- 不做 API envelope 组装

---

## 3. 来源能力矩阵（当前三类主来源）

> 注：`direct_url` 为基础来源；本节按“路线图要求的三类来源”列出主干能力对比。

| 来源 | 典型输入类型 | 解析行为 | 常见错误差异 | 输出差异 |
|---|---|---|---|---|
| `site_media` | YouTube/Bilibili 等站点 URL | 通过站点解析器获取真实媒体流 | 解析超时、上游限制、命令非零退出 | 可能返回代理优先策略与直播属性 |
| `jellyfin` | Jellyfin 资源 URL/ID（配合 source_payload） | 通过 Jellyfin 来源插件解析流地址 | Jellyfin 资源不存在、鉴权/地址不可达 | `source_payload` 参与上下文，标题可回填 |
| `local_library` | 本地文件路径/本地库关键字 | 在本地媒体库中定位可播资源 | 本地资源不存在、索引未命中 | 标题与本地元数据绑定更强 |

补充（基础来源）：

- `direct_url`：用于 HTTP/HTTPS 直链，不做站点解析。

兼容项：

- `SourceRegistry.LEGACY_HINT_MAP` 仍保留：`http_url/network_audio (deprecated)/local_music` -> 新 hint（其中 `network_audio` 语义已拆分为 `site_media` 或 `direct_url`）。
- `LegacyPayloadSourcePlugin` 仅承接旧 caller，不作为新功能入口。

---

## 4. Transport 能力矩阵

当前策略（`TransportPolicy`）:

- `play`: `mina`
- `tts`: `miio` -> fallback `mina`
- `volume`: `miio` -> fallback `mina`
- `stop`: `miio` -> fallback `mina`
- `pause`: `miio` -> fallback `mina`
- `probe`: `miio` -> fallback `mina`

边界：

- `MiioTransport.play_url` 明确不支持（不承担正式播放下发）
- 路由只在 `能力矩阵 ∩ 策略` 交集内尝试，不做越权 fallback

---

## 5. 新实现者最小阅读路径

建议按以下顺序阅读源码：

1. `xiaomusic/api/routers/v1.py`（正式入口）
2. `xiaomusic/playback/facade.py`（边界收口层）
3. `xiaomusic/core/models/media.py`（`PlayOptions` / `MediaRequest`）
4. `xiaomusic/core/coordinator/playback_coordinator.py`（主流程编排）
5. `xiaomusic/core/source/source_registry.py` + `adapters/sources/*`
6. `xiaomusic/core/delivery/delivery_adapter.py`
7. `xiaomusic/core/transport/transport_router.py` + `adapters/mina|miio/*`

阅读原则：

- 先看正式 `/api/v1/*` 主链路，再看 deprecated wrapper。
- 兼容层只用于迁移，不作为新增能力扩展点。
