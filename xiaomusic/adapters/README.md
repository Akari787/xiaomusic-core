# 目录职责

本目录存放所有具体协议与来源的适配实现，是 `xiaomusic/core/` 定义的抽象接口的实现层。核心原则是：接口契约在 core 定义，具体实现在这里。

# 子目录说明

## sources/

五类内置来源插件的具体实现，均实现 `xiaomusic.core.source.SourcePlugin` 接口：

| 插件 | 文件 | 来源语义 |
|---|---|---|
| `DirectUrlSourcePlugin` | `direct_url_source_plugin.py` | HTTP/HTTPS 直链，不做站点解析 |
| `SiteMediaSourcePlugin` | `site_media_source_plugin.py` | YouTube / Bilibili 等需要站点解析的媒体 |
| `JellyfinSourcePlugin` | `jellyfin_source_plugin.py` | Jellyfin 媒体资源（URL 或资源 ID） |
| `LocalLibrarySourcePlugin` | `local_library_source_plugin.py` | 本地媒体库文件 |
| `OnlinePluginSourcePlugin` | `online_plugin_source_plugin.py` | 服务端 opaque TTL 引用对应的启用 MusicFree JS plugin item |

兼容插件：
- `LegacyPayloadSourcePlugin`（`legacy_payload_source_plugin.py`）：承接旧格式 payload 的兼容层，禁止新功能依赖，计划于 v1.2 评估移除。

注册入口：`default_registry.py` 中的 `register_default_source_plugins()` 函数，按确定性顺序注册上述五个正式插件。`OnlinePluginSourcePlugin` 只消费 v1 搜索生成的进程内 opaque 引用，不接收 WebUI raw item。

## mina/

`MinaTransport`（`mina_transport.py`）：基于 mina（micoapi）协议的传输适配，实现 `xiaomusic.core.transport.Transport` 接口。

当前覆盖：play_url / previous / next / stop / pause / resume / set_volume / probe，是唯一注册的 transport（2026-11 起 MiIO 适配已删除：本 fork 从未取得 xiaomiio 票据，原 `MiioTransport` 实际也是转调设备播放器走 MiNA）。

# 不应该放什么

- 接口定义与抽象基类（属于 `core/source/`、`core/transport/`）
- 传输路由策略（属于 `core/transport/transport_policy.py`）
- relay session、streamer、resolver 细节（属于 `relay/`）
- HTTP API 路由与请求模型（属于 `api/`）

# 关键入口

- `xiaomusic/adapters/sources/default_registry.py`：来源插件注册统一入口
- `xiaomusic/adapters/mina/mina_transport.py`：Mina 传输

# 与其他目录的关系

- **`xiaomusic/core/`**：本目录实现 core 定义的接口（`SourcePlugin` / `Transport`），core 通过注册表持有接口引用，不直接 import 本目录。
- **`xiaomusic/playback/`**：facade 层在启动时触发 `register_default_source_plugins()` 完成注册，之后不再直接访问本目录。
- **`xiaomusic/relay/`**：`SiteMediaSourcePlugin` 内部使用 relay 的 resolver / runtime 能力，属于单向依赖。

# 新增代码规则

- 新增来源插件时，实现 `SourcePlugin.resolve()` 接口，在 `default_registry.py` 中注册，不修改 core 层。
- 新增 transport 时，实现 `Transport` 接口，在应用启动时注册到 `TransportRouter`，并更新 `TransportPolicy` 中的 action 映射。
- 不在 source 插件内直接调用 transport，不在 transport 内调用 source——两者职责严格分离。
