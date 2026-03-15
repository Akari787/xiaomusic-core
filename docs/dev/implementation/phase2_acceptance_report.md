# Phase 2 验收报告

> 历史阶段文档，仅用于回溯 2026-03-07 的 Phase 2 状态。
> 本文包含当时的接口与兼容实现快照，不代表当前正式 API。
> 当前正式契约请以 `docs/api/api_v1_spec.md` 为准。

## 1. 验收时间

- 日期：2026-03-07
- 时间窗口（UTC+8）：12:00 - 12:15

## 2. 测试服务器环境

- 服务器：`root@<test-server-ip>`（hostname: `test`）
- 部署目录：`/<deploy-root>/xiaomusic_core_smoke`（历史目录名：`/<deploy-root>/xiaomusic_auth_smoke`）
- 运行方式：Docker Compose（`docker-compose.hardened.yml`）
- 容器：`xiaomusic-core`（历史容器名：`xiaomusic-auth`）
- 镜像：`xiaomusic-core:latest`（历史镜像名：`xiaomusic:auth-only`）
- 服务端口：`58090 -> 8090`

## 3. 部署分支 / Commit

- 分支：`core-main`（历史工作分支：`auth-only`）
- 基线 commit：`c3375bfd897a68854e9b651ac17539b8cb4815c5`
- 说明：Phase 2 验收基于当时工作区改动部署。

## 4. 验收设备

- 音箱 DID：`<device_id>`
- 设备名称：<speaker_name>
- 硬件型号：`<speaker_model>`

## 5. Jellyfin 真机播放

### 5.1 验收方法

- 查询在线来源：`GET /api/search/online?keyword=love&plugin=all&page=1&limit=20`
- 选择 `source=jellyfin` 的条目
- 触发播放：`POST /api/device/pushUrl`

### 5.2 结果

- 播放请求返回成功：
  - `ok=true`
  - `mode=core_minimal`
  - `source=jellyfin`
  - `transport=mina`
- 音箱开始播放，当时未观察到回归。
- 统一链路日志证据：
  - `core_chain action=play ... source_hint=legacy_payload plugin=legacy_payload`

## 6. `http_url` 真机播放

### 6.1 验收方法

- API：`POST /api/v1/play_url`
- URL：`http://<test-server-ip>:58090/static/silence.mp3`

### 6.2 结果

- API 返回：
  - `ok=true`
  - `state=streaming`
- 统一链路日志证据：
  - `core_chain action=play ... source_hint=http_url plugin=http_url`
- 当时确认的投递路径：
  - `HttpUrlSourcePlugin -> DeliveryAdapter -> TransportRouter -> MinaTransport`

## 7. 停止 / 暂停 / TTS / 音量 / 探测验收

### 7.1 执行命令

- `POST /api/v1/set_volume`（`volume=42`）
- `POST /api/v1/tts`
- `POST /api/v1/probe`
- `POST /api/v1/pause`
- `POST /api/v1/stop`

### 7.2 结果

- `set_volume`：成功（`ok=true`），设备音量更新到 `42`
- `tts`：成功（`ok=true`），音箱正常播报
- `probe`：成功（`ok=true`），返回 `transport=miio` 与 reachability 快照
- `pause`：成功（`ok=true`）
- `stop`：成功（`ok=true`，状态 `stopped`）

### 7.3 Probe + DeviceRegistry 验证

- 连续 probe 调用时，`last_probe_ts` 递增（`1772856414 -> 1772856416`）
- 这说明 probe 结果已写入 `DeviceRegistry.update_reachability()`
- Transport 适配层仅返回 probe 结果，不直接写设备 reachability 状态

## 8. 未完成项与已知限制

- `MiioTransport.play_url` 当时仍是兼容占位实现：
  - 它会委托到历史 `xiaomusic.play_url` 路径
  - 并不是独立的本地 Miio 流媒体实现
- Jellyfin 来源当时仍通过 `LegacyPayloadSourcePlugin` 兼容层接入
- 暂停状态语义受设备固件影响（`getplayerstatus` 在 pause/stop 后可能都返回 `status=2`）

## 9. 后续建议（历史记录）

1. 用独立 `JellyfinSourcePlugin` 替代 `LegacyPayloadSourcePlugin`
2. 实现真正的本地 Miio 媒体播放路径，或在完成前移除其 play fallback
3. 增加 core 层动作指标（`action`、`transport`、`success/failure`）
4. 扩展 `/api/v1/pause|tts|set_volume|probe` 的自动化集成测试
