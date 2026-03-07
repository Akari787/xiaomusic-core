# Final Acceptance Report

## 1. 测试时间

- 日期：2026-03-07
- 时间窗口（UTC+8）：13:15 - 13:20

## 2. 测试服务器环境

- 服务器：`root@192.168.7.178`（hostname: `test`）
- 部署目录：`/root/xiaomusic_oauth2_smoke`
- 运行方式：Docker Compose（`docker-compose.hardened.yml`）
- 容器：`xiaomusic-oauth2`
- 镜像：`xiaomusic:oauth2-only`
- 服务端口：`58090 -> 8090`

## 3. 部署 commit

- 分支：`oauth2-only`
- 部署基线 commit：`cf16bf5`（Phase 4）
- Final Phase 验证代码：在该基线上部署当前工作区改动并完成实机验收。

## 4. 验收设备

- DID：`981257654`
- 设备：Xiaomi Smart Speaker Pro
- 型号：`OH2P`

## 5. Jellyfin 播放结果

### 验收路径

- `GET /api/search/online` 选取 `source=jellyfin`
- `POST /api/device/pushUrl`

### 结果

- 接口返回：`ok=true`, `mode=core`, `source=jellyfin`, `transport=mina`
- 音箱实机开始播放。
- 日志证据：
  - `source_hint=jellyfin source_plugin=jellyfin`
  - `url_prepare_result=ok ... source=jellyfin`
  - `selected_transport=mina`

## 6. http_url 播放结果

### 验收路径

- `POST /api/v1/play_url`
- URL：`http://192.168.7.178:58090/static/silence.mp3`

### 结果

- API 返回统一结构：
  - `code=0`
  - `message=ok`
  - `data.state=streaming`
- 音箱实机开始播放。

## 7. network_audio 播放结果

### 验收路径

- `POST /api/v1/play_url`
- URL：`https://www.youtube.com/watch?v=iPnaF8Ngk3Q`

### 结果

- API 返回统一结构：`code=0`, `data.state=streaming`
- Source 由统一链路选择：`source_hint=network_audio source_plugin=network_audio`
- 音箱实机开始播放。

## 8. 控制动作结果

验证接口：

- `POST /api/v1/stop`
- `POST /api/v1/pause`
- `POST /api/v1/tts`
- `POST /api/v1/set_volume`
- `POST /api/v1/probe`

结果：

- 全部动作返回 `code=0`。
- `set_volume` 后设备音量更新为 `39`。
- `probe` 返回 reachability，连续调用 `last_probe_ts` 递增（`1772860765 -> 1772860767`）。

## 9. 剩余兼容层

- `LegacyPayloadSourcePlugin`
  - 标记：`compatibility_layer`
  - 原因：承接历史 payload 调用
  - 预计移除：下一大版本，旧 API caller 迁移完成后
- `PlaybackFacade` 中 `network_audio_cast` / `network_audio_link`
  - 标记：`compatibility_layer`
  - 原因：兼容历史 runtime 调用
  - 预计移除：v2 API 冻结后
- `PlaybackFacade` 中 `legacy_direct_fallback`
  - 标记：`compatibility_layer`
  - 原因：兼容非 HTTP 历史输入
  - 预计移除：下一大版本

## 10. 自动化测试情况

Final Phase 新增 / 完善：

- `tests/test_api_v1_control_flow.py`
- `tests/test_api_unified_chain_entry.py`
- `tests/unit/test_network_audio_source_plugin.py`
- `tests/unit/test_core_source_plugins_and_registry.py`（补全 resolve -> delivery）

执行命令：

- `python -m pytest tests/test_api_v1_play_url_response_shape.py tests/test_response_consistency.py tests/test_api_v1_control_flow.py tests/unit/test_core_source_plugins_and_registry.py tests/unit/test_network_audio_source_plugin.py tests/unit/test_core_transport_router.py tests/unit/test_core_playback_coordinator.py tests/unit/test_core_delivery_adapter.py tests/unit/test_miio_play_strategy.py`
- 结果：`12 passed, 3 skipped`

> 说明：`aiofiles` 缺失环境下，部分 API 集成测试按设计 skip。

## 11. 系统限制

- `NetworkAudioSourcePlugin` 依赖 yt-dlp 可用性与上游站点策略。
- legacy 路由仍存在，但已收敛为兼容层并标注移除阶段。
- `/api/device/pushUrl` 保留历史响应结构（非 `/api/v1/*`），用于旧调用兼容。

## 12. 未来路线

1. 在 v2 中移除 `compatibility_layer` 标记分支（legacy payload / runtime modes / direct fallback）。
2. 将 legacy 路由响应结构迁移到统一 `code/message/data` 后下线旧格式。
3. 增加完整 CI 环境依赖（含 `aiofiles`）以避免关键 API 集成测试被 skip。
