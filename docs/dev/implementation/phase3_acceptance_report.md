# Phase 3 Acceptance Report

## 1. 测试时间

- 日期：2026-03-07
- 时间窗口（UTC+8）：12:20 - 12:35

## 2. 测试服务器环境

- 服务器：`root@192.168.7.178`（hostname: `test`）
- 部署目录：`/root/xiaomusic_core_smoke`（历史目录名：`/root/xiaomusic_oauth2_smoke`）
- 运行方式：Docker Compose（`docker-compose.hardened.yml`）
- 容器：`xiaomusic-core`（历史容器名：`xiaomusic-oauth2`）
- 镜像：`xiaomusic-core:latest`（历史镜像名：`xiaomusic:oauth2-only`）
- 端口：`58090 -> 8090`

## 3. 部署分支 / Commit

- 分支：`core-main`（历史工作分支：`oauth2-only`）
- 部署基线 commit：`1de5ba2`（Phase 2 收口提交）
- Phase 3 验收代码：当前工作区 Phase 3 改动（已部署并实机验收）

## 4. 验收设备

- DID：`981257654`
- 设备：Xiaomi Smart Speaker Pro
- 型号：`OH2P`

## 5. JellyfinSourcePlugin 实机验收结果

### 5.1 验收步骤

- 先搜索 Jellyfin 来源：`GET /api/search/online?keyword=love&plugin=all&page=1&limit=20`
- 选择 `source=jellyfin` 的条目
- 调用：`POST /api/device/pushUrl`

### 5.2 实机结果

- 接口返回成功：
  - `ok=true`
  - `mode=core`
  - `source=jellyfin`
  - `transport=mina`
- 音箱实际开始播放（Jellyfin 音频地址下发成功）。
- 日志证明经过统一链路且命中正式插件：
  - `core_chain action=play ... source_hint=jellyfin plugin=jellyfin`
  - `core_chain prepared source=jellyfin transport=mina ...`

## 6. http_url 实机验收结果

### 6.1 验收步骤

- 调用：`POST /api/v1/play_url`
- URL：`http://192.168.7.178:58090/static/silence.mp3`

### 6.2 实机结果

- 返回：`ok=true`, `state=streaming`
- 音箱实际开始播放。
- 日志链路证据：
  - `core_chain action=play ... source_hint=http_url plugin=http_url`
  - `core_chain prepared source=http_url transport=mina ...`

## 7. stop / pause / tts / set_volume / probe 验收结果

### 7.1 验收命令

- `POST /api/v1/stop`
- `POST /api/v1/pause`
- `POST /api/v1/tts`
- `POST /api/v1/set_volume`
- `POST /api/v1/probe`（连续两次）

### 7.2 结果摘要

- `stop`：成功（`ok=true`，状态 `stopped`）。
- `pause`：成功（`ok=true`）。
- `tts`：成功（`ok=true`，设备播报正常）。
- `set_volume`：成功（`ok=true`，设备音量变更为 `37`）。
- `probe`：成功（`ok=true`，返回 `transport=miio` 与 reachability 快照）。

### 7.3 DeviceReachability 更新验证

- 连续 probe 的 `last_probe_ts` 递增：`1772857735 -> 1772857736`。
- 说明探测结果通过 `DeviceRegistry.update_reachability()` 更新。
- Transport 仅返回 probe 结果，不直接写设备状态。

## 8. Miio play_url 最终策略与验收结果

### 8.1 最终策略（Phase 3 结论）

- 采用方案 B：Miio 不承担 `play_url` 正式播放路径。
- `MiioTransport.play_url` 明确抛出 `TransportError`（显式不支持）。
- `TransportCapabilityMatrix.play` 收口为仅 `mina`。

### 8.2 验收结果

- 日志中所有 play 链路均路由到 `transport=mina`。
- 自动化测试覆盖：
  - `tests/unit/test_miio_play_strategy.py` 验证 Miio play_url 显式不支持。
  - `tests/unit/test_core_device_registry.py` 验证设备能力 play 仅 `mina`。

## 9. 已删除的旧逻辑清单

- API 主路径不再以 `core_minimal` 作为默认模式；统一改为 `core` 默认链路：
  - `/api/v1/play_url`
  - `/api/v1/test_reachability`
  - `/playurl`（兼容路由内部）
- Jellyfin 主路径不再由 `LegacyPayloadSourcePlugin` 承载：新增 `JellyfinSourcePlugin` 作为正式入口。
- 移除 Miio play_url 的“兼容占位委托旧路径”行为，改为显式不支持。

## 10. 保留的兼容层清单与原因

- `LegacyPayloadSourcePlugin`：
  - 仅保留给非 Jellyfin 的历史 payload 来源。
  - 原因：仍有旧入口依赖通用 payload 结构，需渐进迁移。
- `PlaybackFacade` 中 `network_audio (deprecated)` 分支：
  - 仅作为 YouTube/Bilibili 等站点的解析/转发兼容 fallback。
  - 原因：此类 URL 需要 resolver 链路，暂未插件化到 Source 层。
- `PlaybackFacade` 中 `legacy_direct_fallback`：
  - 仅用于非 HTTP URL 的历史兼容。
  - 原因：避免破坏老调用方的非标准 URL 输入。

## 11. 自动化测试新增情况

新增测试文件：

- `tests/unit/test_core_source_plugins_and_registry.py`
- `tests/unit/test_core_delivery_adapter.py`
- `tests/unit/test_core_transport_router.py`
- `tests/unit/test_core_device_registry.py`
- `tests/unit/test_core_playback_coordinator.py`
- `tests/unit/test_miio_play_strategy.py`

覆盖点：

- Coordinator：`play/stop/pause/tts/set_volume/probe` + 过期重试
- Source：`HttpUrlSourcePlugin.resolve`、`JellyfinSourcePlugin.resolve`、`SourceRegistry.get_plugin`
- Delivery：直链与过期 URL 触发 `ExpiredStreamError`
- Router：`capability ∩ policy`、fallback、多动作分发
- DeviceRegistry：`update_reachability`、legacy hydrate 能力矩阵
- Miio 策略：play_url 显式不支持

执行结果：

- `python -m pytest tests/unit/test_core_source_plugins_and_registry.py tests/unit/test_core_delivery_adapter.py tests/unit/test_core_transport_router.py tests/unit/test_core_device_registry.py tests/unit/test_core_playback_coordinator.py tests/unit/test_miio_play_strategy.py tests/test_api_v1_play_url_response_shape.py tests/test_response_consistency.py`
- 结果：`13 passed, 2 skipped`

## 12. 已知限制

- YouTube/Bilibili 仍通过 `network_audio (deprecated)` compatibility fallback 播放；其来源语义应归入 `Site Media`。
- `LegacyPayloadSourcePlugin` 仍需保留一段时间承接未迁移旧 payload 来源。

## 13. 下一阶段建议

1. 将网络站点（YouTube/Bilibili）抽象为独立 SourcePlugin，逐步收敛 `network_audio (deprecated)` fallback。
2. 继续收缩 legacy payload 入口，把非 Jellyfin 来源按类型拆分为显式插件。
3. 增加核心链路结构化指标（source/plugin/transport/action）并接入长期回归看板。
