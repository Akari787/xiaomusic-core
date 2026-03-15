# API v1 Cleanup Report

> 说明（2026-03-08）：本报告是 2026-03-07 的阶段性清理记录。
> 当前正式契约请以 `docs/api/api_v1_spec.md` 为准。
> 若与本报告内容冲突（例如白名单数量），以规范文档优先。

## 1. 测试时间

- 本地改造与单测：2026-03-07
- 测试服务器实机验收：2026-03-07（Asia/Shanghai）

## 2. 测试服务器环境

- 服务器：`192.168.7.178`
- 登录方式：`ssh -i ~/.ssh/id_ed25519_opencode root@192.168.7.178`
- 部署目录：`/root/xiaomusic_core_smoke`（历史目录名：`/root/xiaomusic_auth_smoke`）
- 运行方式：`docker compose -f docker-compose.hardened.yml up -d --build xiaomusic-core`
- 服务地址：`http://127.0.0.1:58090`

## 3. 部署 commit / 分支

- 本地分支：`core-main`（历史工作分支：`auth-only`）
- 本地基线 commit：`7851d06dd3e46c6f7694e1c994c7ca3997a1a37d`
- 实机部署内容：基于上述 commit + 本次工作区变更（未额外新建发布分支）

## 4. 保留的正式 API 白名单（历史快照）

`/api/v1` 在本次清理时仅保留以下 10 个接口：

1. `POST /api/v1/play`
2. `POST /api/v1/resolve`
3. `POST /api/v1/control/stop`
4. `POST /api/v1/control/pause`
5. `POST /api/v1/control/resume`
6. `POST /api/v1/control/tts`
7. `POST /api/v1/control/volume`
8. `POST /api/v1/control/probe`
9. `GET /api/v1/devices`
10. `GET /api/v1/system/status`

注：后续规范升级已新增 `GET /api/v1/player/state`，最新白名单请查看 `docs/api/api_v1_spec.md`。

## 5. 删除的旧 /api/v1 接口清单

已从 `xiaomusic/api/routers/v1.py` 删除以下非白名单接口：

- `GET /api/v1/detect_base_url`
- `POST /api/v1/play_url`
- `POST /api/v1/play_music`
- `POST /api/v1/play_music_list`
- `POST /api/v1/set_play_mode`
- `POST /api/v1/stop`
- `POST /api/v1/pause`
- `POST /api/v1/tts`
- `POST /api/v1/set_volume`
- `POST /api/v1/probe`
- `GET /api/v1/status`
- `POST /api/v1/sessions/cleanup`
- `POST /api/v1/test_reachability`

对应旧请求模型与测试也已同步清理。

## 6. 统一响应结构说明

正式 API 统一返回：

```json
{
  "code": 0,
  "message": "ok",
  "data": {},
  "request_id": "..."
}
```

错误同样统一为上述结构，`code/message` 区分错误，历史字段不再作为顶层主字段。

## 7. WebUI 调整内容

WebUI（`xiaomusic/webui/src/pages/HomePage.tsx`）已完成以下收口：

- 停止按钮改为调用 `POST /api/v1/control/stop`
- TTS 测试改为调用 `POST /api/v1/control/tts`
- 音量调节改为调用 `POST /api/v1/control/volume`
- 删除对已移除接口 `POST /api/v1/stop` 与 `POST /api/v1/set_play_mode` 的依赖
- 播放测试页继续按规范先 `resolve` 再 `play`

## 8. 自动化测试情况

执行命令：

```bash
PYTHONPATH=. pytest tests/test_api_play.py tests/test_api_resolve.py tests/test_api_v1_control_flow.py tests/test_response_consistency.py tests/test_api_error_handling.py tests/test_api_import_boundary.py
```

结果：`21 passed`。

覆盖点：

- `/api/v1` 白名单路由检查
- `play/resolve/control` 主流程
- `SourceResolveError/TransportError/InvalidRequestError` 统一错误转换
- `import xiaomusic.api.models` 导入边界（不触发 app 初始化链）

## 9. 实机播放结果

设备：`device_id=981257654`。

- `direct_url`：`POST /api/v1/play` 返回 `code=0`，`source_plugin=direct_url`
- `site_media`：`POST /api/v1/play` 返回 `code=0`，`source_plugin=site_media`
- `jellyfin`：`POST /api/v1/play` 返回 `code=0`，`source_plugin=jellyfin`
- `local_library`：`POST /api/v1/play` 返回 `code=0`，`source_plugin=local_library`

返回体均为统一 `ApiResponse`，并包含 `device_id/source_plugin/transport/status`。

## 10. 实机控制结果

以下接口均以正式 API 直接验收：

- `POST /api/v1/control/stop`：成功（`status=stopped`）
- `POST /api/v1/control/pause`：成功（`status=paused`）
- `POST /api/v1/control/resume`：成功（`status=resumed`）
- `POST /api/v1/control/tts`：成功（`status=ok`）
- `POST /api/v1/control/volume`：成功（`status=ok`）
- `POST /api/v1/control/probe`：成功（`reachable=true`，并回写 reachability 信息）

## 11. 已知限制

- 当前 WebUI 运行时静态资源来自 `xiaomusic/webui/static`；本次代码已对齐 `src` 调用路径，若要让线上界面立即体现需配套前端产物构建/替换。
- `GET /api/v1/devices` 的 `online` 字段依赖底层设备元数据，可能与实时探测结果存在短时差异；以 `control/probe` 结果为准。

## 12. 后续建议

1. 将 `webui/src` 变更纳入标准构建发布流程，确保生产 UI 与 API 收口状态一致。
2. 为 `/api/v1` 增加集成级 contract test（启动真实 app 后枚举路由 + 响应 schema 校验）。
3. 后续继续清理非 v1 历史路由（`/playtts`、`/setvolume`、`/cmd` 等），逐步推进前端仅依赖正式 API。
