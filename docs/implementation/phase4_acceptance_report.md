# Phase 4 Acceptance Report

## 1. 测试时间

- 日期：2026-03-07
- 时间窗口（UTC+8）：12:58 - 13:05

## 2. 测试服务器环境

- 服务器：`root@192.168.7.178`（hostname: `test`）
- 部署目录：`/root/xiaomusic_oauth2_smoke`
- 运行方式：Docker Compose（`docker-compose.hardened.yml`）
- 容器：`xiaomusic-oauth2`
- 镜像：`xiaomusic:oauth2-only`
- 端口：`58090 -> 8090`

## 3. 部署 commit

- 分支：`oauth2-only`
- 部署基线 commit：`6dc94a9`（Phase 3）
- Phase 4 验证代码：在该基线上部署当前工作区变更并完成实机验收。

## 4. 验收设备

- DID：`981257654`
- 设备：Xiaomi Smart Speaker Pro
- 型号：`OH2P`

## 5. Jellyfin 实机播放结果

### 验收步骤

- `GET /api/search/online?keyword=love&plugin=all&page=1&limit=20`
- 选取 `source=jellyfin` 条目
- `POST /api/device/pushUrl`

### 结果

- 返回成功：`ok=true`, `mode=core`, `source=jellyfin`, `transport=mina`
- 音箱实际开始播放。
- 关键日志证明链路：
  - `core_chain action=play ... source_hint=jellyfin source_plugin=jellyfin`
  - `url_prepare_result=ok ... source=jellyfin`
  - `transport_route action=play candidate_transports=['mina'] selected_transport=mina`

## 6. http_url 实机播放结果

### 验收步骤

- `POST /api/v1/play_url`
- URL：`http://192.168.7.178:58090/static/silence.mp3`

### 结果

- 返回成功：`ok=true`, `state=streaming`
- 音箱实际开始播放。
- 关键日志证明链路：
  - `source_hint=http_url source_plugin=http_url`
  - `url_prepare_result=ok ... source=http_url`
  - `selected_transport=mina`

## 7. network_audio 实机播放结果

### 验收步骤

- `POST /api/v1/play_url`
- URL：`https://www.youtube.com/watch?v=iPnaF8Ngk3Q`

### 结果

- 返回成功：`ok=true`, `state=streaming`
- 返回 `stream_url` 为可播放的解析后音频直链（googlevideo）。
- 音箱实际开始播放。
- 关键日志证明链路：
  - `source_hint=network_audio source_plugin=network_audio`
  - `url_prepare_result=ok ... source=network_audio`
  - `transport_action action=play_url success=true`

## 8. stop / pause / tts / set_volume / probe 验收结果

执行接口：

- `POST /api/v1/stop`
- `POST /api/v1/pause`
- `POST /api/v1/tts`
- `POST /api/v1/set_volume`
- `POST /api/v1/probe`（连续两次）

结果：

- `stop`：`ok=true`，状态 `stopped`
- `pause`：`ok=true`
- `tts`：`ok=true`，设备播报成功
- `set_volume`：`ok=true`，设备音量变为 `41`
- `probe`：`ok=true`，返回 `transport=miio`，reachability 更新成功
- 连续 probe `last_probe_ts` 递增：`1772859730 -> 1772859731`

## 9. LegacyPayloadSourcePlugin 剩余职责

- 仅用于旧 API payload 的兼容入口（非 Jellyfin / 非 network_audio 主路径）。
- 已明确禁止承载新业务来源。
- Jellyfin 主路径已迁移至 `JellyfinSourcePlugin`。
- network_audio 主路径已迁移至 `NetworkAudioSourcePlugin`。

## 10. 新增自动化测试情况

新增测试：

- `tests/unit/test_network_audio_source_plugin.py`
- `tests/test_api_unified_chain_entry.py`

扩展与覆盖：

- `tests/unit/test_core_source_plugins_and_registry.py`（Http/Jellyfin resolve + Delivery）
- `tests/unit/test_core_transport_router.py`（capability∩policy、fallback、play 路由）
- `tests/unit/test_miio_play_strategy.py`（Miio play_url 排除）
- `tests/unit/test_core_delivery_adapter.py`（直链、headers、过期异常）
- `tests/unit/test_core_playback_coordinator.py`（play/control/probe/重试）
- `tests/unit/test_core_device_registry.py`（reachability 更新与 play 能力矩阵）

执行命令：

- `python -m pytest tests/unit/test_core_source_plugins_and_registry.py tests/unit/test_network_audio_source_plugin.py tests/unit/test_core_transport_router.py tests/unit/test_core_delivery_adapter.py tests/unit/test_core_device_registry.py tests/unit/test_core_playback_coordinator.py tests/unit/test_miio_play_strategy.py tests/test_api_v1_play_url_response_shape.py tests/test_response_consistency.py`
- 结果：`14 passed, 2 skipped`

## 11. 已知限制

- NetworkAudioSourcePlugin 当前依赖 yt-dlp 解析稳定性与站点策略变动。
- `tests/test_api_unified_chain_entry.py` 在缺少 `aiofiles` 环境下会被 skip。
- 旧非 HTTP URL 输入仍存在 `legacy_direct_fallback` 兼容分支（已标注兼容原因）。

## 12. 下一阶段建议

1. 将 network_audio 的解析超时、站点失败分类为更细错误码（便于告警路由）。
2. 继续收缩 `legacy_direct_fallback`，推动所有入口标准化为可解析 URL/payload。
3. 增加 `/api/v1/play_url` 真实 HTTP 集成测试（带请求 ID 断言与日志字段断言）。
