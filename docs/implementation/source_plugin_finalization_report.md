# Source Plugin Finalization Report

## 1. 测试时间

- 日期：2026-03-07
- 时间窗口（UTC+8）：14:15 - 14:20

## 2. 测试服务器环境

- 服务器：`root@192.168.7.178`（hostname: `test`）
- 部署目录：`/root/xiaomusic_oauth2_smoke`
- 运行方式：Docker Compose（`docker-compose.hardened.yml`）
- 容器：`xiaomusic-oauth2`
- 镜像：`xiaomusic:oauth2-only`
- 服务端口：`58090 -> 8090`

## 3. 部署 commit / 分支

- 分支：`oauth2-only`
- 部署基线 commit：`86eca58`
- 本次验收代码：在该基线上部署当前工作区“来源彻底插件化”改动完成验收。

## 4. 默认内置插件清单

统一默认注册逻辑：`xiaomusic/adapters/sources/default_registry.py`

默认内置插件：

1. `JellyfinSourcePlugin`
2. `HttpUrlSourcePlugin`
3. `LocalMusicSourcePlugin`
4. `NetworkAudioSourcePlugin`

## 5. Jellyfin 实机播放结果

- 入口：`POST /api/device/pushUrl`（payload `source=jellyfin`）
- 结果：`ok=true`，`source=jellyfin`，`transport=mina`
- 统一链路日志：
  - `source_hint=jellyfin source_plugin=jellyfin`
  - `url_prepare_result=ok ... source=jellyfin`
  - `transport_route ... selected_transport=mina`

## 6. http_url 实机播放结果

- 入口：`POST /api/v1/play_url`
- URL：`http://192.168.7.178:58090/static/silence.mp3`
- 结果：`code=0`，`data.state=streaming`
- 日志：`source_hint=http_url source_plugin=http_url`

## 7. local_music 实机播放结果

- 预置本地测试音频：`/root/xiaomusic_music/local_silence.mp3`
- 刷新库：`POST /api/music/refreshlist`
- 入口：`POST /api/v1/play_music`，`music_name=local_silence`
- 结果：`code=0`，`data.state=streaming`，`data.stream_url` 为 `/music/local_silence.mp3`
- 日志：`source_hint=local_music source_plugin=local_music`

## 8. network_audio 实机播放结果

- 入口：`POST /api/v1/play_url`
- URL：`https://www.youtube.com/watch?v=iPnaF8Ngk3Q`
- 结果：`code=0`，`data.state=streaming`
- 日志：`source_hint=network_audio source_plugin=network_audio`

## 9. LegacyPayloadSourcePlugin 剩余职责

- 职责已收缩为：旧 payload -> 标准 `MediaRequest/source_hint/context` 适配器。
- 不再注册进 `SourceRegistry`，不再承担真实来源解析。
- 真实来源解析全部由正式插件执行（Jellyfin/http_url/local_music/network_audio）。

## 10. 已删除或迁移的旧分支

- 迁移：`/api/v1/play_music` 从 `xiaomusic.do_play` 迁移到 `PlaybackCoordinator -> LocalMusicSourcePlugin`。
- 迁移：`/playmusic`（旧路由）改为兼容入口，内部转统一链路（已标记 `compatibility_layer`）。
- 收口：`play_payload` 不再依赖 LegacyPayloadSourcePlugin 进行 URL 解析，改为先适配请求再路由正式插件。

## 11. 自动化测试情况

新增：

- `tests/unit/test_local_music_source_plugin.py`
- `tests/unit/test_source_plugin_chain_integration.py`

扩展：

- `tests/unit/test_core_source_plugins_and_registry.py`
- `tests/unit/test_network_audio_source_plugin.py`
- `tests/test_api_unified_chain_entry.py`

执行命令：

- `python -m pytest tests/unit/test_local_music_source_plugin.py tests/unit/test_core_source_plugins_and_registry.py tests/unit/test_network_audio_source_plugin.py tests/unit/test_source_plugin_chain_integration.py tests/unit/test_core_playback_coordinator.py tests/unit/test_core_transport_router.py tests/test_api_unified_chain_entry.py tests/test_api_v1_play_url_response_shape.py tests/test_response_consistency.py tests/test_api_v1_control_flow.py`
- 结果：`17 passed, 4 skipped`

## 12. 已知限制

- `LegacyPayloadSourcePlugin` 仍需保留为旧 payload 兼容输入适配层。
- `network_audio` 解析仍受上游站点与 yt-dlp 可用性影响。
- 部分 API 测试在缺少 `aiofiles` 的环境中会按设计 skip。
