# WebUI 与 v1 主链路对齐说明（v1.1.0 Phase 1）

## 1. 正式 API 调用入口

前端正式入口：`xiaomusic/webui/src/services/v1Api.ts`

当前主调用：

- `POST /api/v1/play`
- `POST /api/v1/control/stop`
- `POST /api/v1/control/tts`
- `POST /api/v1/control/volume`
- `GET /api/v1/devices`
- `GET /api/v1/system/status`
- `GET /api/v1/player/state`

说明：

- 页面层应优先调用 `v1Api.ts`，避免散落 endpoint 字符串。

---

## 2. deprecated 前端入口/调用（仍存在）

以下为兼容或历史控制面调用，不属于 v1 正式合同：

- `/cmd`、`/cmdstatus`
- `/device_list`（作为设备获取 fallback）
- `/getvolume`

处理策略：

- 保留现有行为以兼容历史功能。
- 不新增对这些入口的依赖。
- 面向外部调用方的文档只写 `/api/v1/*`。

---

## 3. 默认首页与关键操作流

默认页面：`HomePage`（`xiaomusic/webui/src/pages/HomePage.tsx`）

关键主链路：

1. 设备加载（优先 `/api/v1/devices`）
2. 状态轮询（`/api/v1/player/state`）
3. 播放操作（`/api/v1/play`）
4. 停止/音量/TTS（`/api/v1/control/*`）

调试入口：

- `ApiV1DebugPage` 作为低显眼度入口挂在首页右下角，不作为默认首页。

---

## 4. 错误提示与状态反馈

前端当前可区分的错误阶段：

- 参数错误（如 `device_id/query` 缺失）
- 来源解析失败（`resolve`）
- 运行时准备失败（`prepare`）
- 传输/投递失败（`dispatch` / `xiaomi`）

实现位置：

- `apiErrorInfo()`：`xiaomusic/webui/src/services/v1Api.ts`
- `explainPlaybackError()`：`xiaomusic/webui/src/pages/HomePage.tsx`

提示原则：

- 优先展示阶段与错误码语义，减少“统一未知错误”提示。
