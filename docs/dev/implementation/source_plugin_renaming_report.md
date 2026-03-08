# Source Plugin Renaming Report

## 1. 验收时间

- 日期：2026-03-07
- 时间窗口（UTC+8）：16:00 - 16:12

## 2. 验收环境

- 测试服务器：`root@192.168.7.178`（hostname: `test`）
- 部署目录：`/root/xiaomusic_oauth2_smoke`
- 部署方式：`docker compose -f /root/xiaomusic_oauth2_smoke/docker-compose.hardened.yml up -d --build xiaomusic`
- 服务地址：`http://192.168.7.178:58090`
- 验收设备：`DID=981257654`（Xiaomi Smart Speaker Pro / `OH2P`）

## 3. 命名重构结果

当前官方来源插件命名已统一为：

1. `DirectUrlSourcePlugin`
2. `SiteMediaSourcePlugin`
3. `JellyfinSourcePlugin`
4. `LocalLibrarySourcePlugin`

旧 source-hint 兼容映射已集中在 `SourceRegistry.LEGACY_HINT_MAP`：

- `http_url -> direct_url`
- `network_audio (deprecated) -> site_media`
- `local_music -> local_library`

## 4. 实机播放验收（新命名）

### 4.1 DirectUrl

- 接口：`POST /api/v1/play_url`
- 输入 URL：`http://192.168.7.178:58090/static/silence.mp3`
- 返回：`code=0`, `data.state=streaming`, `data.source_plugin=direct_url`, `data.transport=mina`
- 日志证据：
  - `source_hint=direct_url source_plugin=direct_url`

### 4.2 SiteMedia

- 接口：`POST /api/v1/play_url`
- 输入 URL：`https://www.youtube.com/watch?v=iPnaF8Ngk3Q`
- 返回：`code=0`, `data.state=streaming`, `data.source_plugin=site_media`, `data.transport=mina`
- 日志证据：
  - `source_hint=site_media source_plugin=site_media`

### 4.3 LocalLibrary

- 接口：`POST /api/v1/play_music`
- 输入：`music_name=local_silence`
- 返回：`code=0`, `data.state=streaming`, `data.source_plugin=local_library`, `data.transport=mina`
- 日志证据：
  - `source_hint=local_library source_plugin=local_library`

### 4.4 Jellyfin

- 接口：`POST /api/device/pushUrl`
- 输入：`source=jellyfin`
- 返回：`ok=true`, `mode=core`, `source=jellyfin`, `transport=mina`
- 日志证据：
  - `source_hint=jellyfin source_plugin=jellyfin`

## 5. 旧 hint 兼容验收

使用兼容入口 `POST /api/device/pushUrl` 验证：

- `source=http_url` -> 响应 `source=direct_url`
- `source=network_audio (deprecated)` -> 响应 `source=site_media`
- `source=local_music` -> 响应 `source=local_library`

说明：旧命名仅作为兼容输入，主链路输出已全部收敛为新语义命名（`network_audio` 已拆分为 `site_media` / `direct_url`）。

## 6. WebUI 同步说明

- `HomePage` 播放测试文案已更新为“直链媒体 / 网站媒体”。
- `playLink` 已统一走 `/api/v1/play_url` core 路径。
- 增加 envelope 解析，修复旧响应处理导致的误报文案（避免“失败：ok”）。
- `set_play_mode`、`stop` 也改为 envelope 解析后再提示。

## 7. 自动化测试

执行命令：

- `python -m pytest tests/unit/test_core_source_plugins_and_registry.py tests/unit/test_source_plugin_chain_integration.py tests/unit/test_core_playback_coordinator.py tests/unit/test_core_transport_router.py tests/unit/test_core_delivery_adapter.py tests/unit/test_miio_play_strategy.py tests/unit/test_site_media_source_plugin.py tests/unit/test_local_library_source_plugin.py tests/test_api_unified_chain_entry.py tests/test_api_v1_play_url_response_shape.py tests/test_response_consistency.py tests/test_api_v1_control_flow.py`

结果：`20 passed, 4 skipped`

## 8. 结论

- Source 插件重命名与主链路语义化已完成。
- API / Core / 测试 / 文档 / WebUI 的主路径命名已与新语义保持一致。
- 旧命名已收敛到集中兼容映射，未作为主路径命名继续扩散。
