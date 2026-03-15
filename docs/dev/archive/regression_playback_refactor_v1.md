# Playback Refactor v1 Regression Baseline

> 历史归档文档，仅用于回溯早期播放重构阶段的回归基线。
> 文中的旧接口、旧术语与旧测试流程不代表当前主线实现。

## 0. 回归目标

- 将当时的可播放能力冻结为重构基线
- 每次播放重构子任务后执行完整 A/B/C/D 验收并记录结果
- 如果基线本身不稳定，优先修复输入样本，再继续重构

## 1. 环境约束（首次填写）

- 局域网入口：`<LAN_IP>:<PORT>`
- 音箱 ID：`<SPEAKER_ID>`
- 运行模式：Docker / local / other
- 依赖快照：`yt-dlp --version`、`ffmpeg -version`
- 网络类型（可选）：家庭宽带 / 热点

## 2. 用例定义（A/B/C/D）

所有用例必须满足要求项，否则样本无效。

### Case A（YouTube 点播）

- URL 占位：`<YOUTUBE_VOD_URL>`
- 要求：公开、无年龄限制、时长 3-10 分钟、无强地理限制
- 步骤：
  1. 调用 `play_url(<YOUTUBE_VOD_URL>, <SPEAKER_ID>, options)`（或兼容 wrapper）
  2. 验证音箱是否在 N 秒内开始出声
  3. 调用 stop
  4. 重复 play/stop 3 次
- 通过标准：
  - N=30 秒内开始播放
  - stop 可用
  - 3 次重复无崩溃 / 无卡死

### Case B（YouTube 直播）

- URL 占位：`<YOUTUBE_LIVE_URL>`
- 要求：正在直播、直播时间较长、地理限制较低
- 步骤：同 Case A
- 通过标准：
  - N=30 秒内开始播放
  - stop 可用
  - 如实现支持，允许一次短暂中断后的自动恢复

### Case C（bilibili 直播）

- URL 占位：`<BILIBILI_LIVE_URL>`
- 要求：正在直播、非付费直播间、支持自动清晰度协商
- 步骤：同 Case A
- 通过标准：同 Case A

### Case D（直链流媒体）

- URL 占位：`<DIRECT_STREAM_URL>`
- 要求：m3u8 或 mp3 直链、响应稳定、可连续播放至少 2 分钟
- 步骤：
  1. 调用 `play_url`
  2. 连续播放 2 分钟
  3. 调用 stop
- 通过标准：
  - N=15 秒内开始播放
  - 可连续播放 2 分钟（或仅允许一次预期内短暂重连）
  - stop 可用

## 3. `fail_stage` 枚举

仅允许以下取值：

- `resolve`：URL 解析失败 / 超时
- `stream`：本地流端点失败（如 `/stream` 无输出 / 客户端连接失败）
- `ffmpeg`：转码或抽流失败，进程崩溃或无法启动
- `xiaomi`：向音箱投放失败或音箱无声音输出
- `unknown`：无法分类，必须在备注中附关键日志

## 4. 回归记录模板

字段规则：

- `date/time`：`YYYY-MM-DD HH:mm:ss`
- `case`：仅允许 `A/B/C/D`
- `result`：仅允许 `pass` 或 `fail`
- `fail_stage`：通过时留空
- `notes`：记录时延、关键日志、重试次数、外部波动

| date/time           | commit | case | result | fail_stage | notes |
| ------------------- | ------ | ---- | ------ | ---------- | ----- |
| YYYY-MM-DD HH:mm:ss | `<SHA>`  | A    | pass   |            | t=12s; retries=0; keylog=...; ext_var=no |
| YYYY-MM-DD HH:mm:ss | `<SHA>`  | B    | fail   | resolve    | t=30s; retries=2; keylog=timeout; ext_var=live_unstable |
| YYYY-MM-DD HH:mm:ss | `<SHA>`  | C    | pass   |            | t=18s; retries=1; keylog=reconnect; ext_var=yes |
| YYYY-MM-DD HH:mm:ss | `<SHA>`  | D    | pass   |            | t=6s; retries=0; keylog=ok; ext_var=no |

## 5. Test Server Baseline Record

Environment:

- target: `<TEST_SERVER_HOST>:58090`
- service: `xiaomusic-core` image `akari787/xiaomusic-core:v1.0.3`
- speaker_id: `<device_id>`
- note: on this speaker firmware, `getplayerstatus.status=2` is treated as stopped (non-playing)

| date/time           | commit             | case | result | fail_stage | notes |
| ------------------- | ------------------ | ---- | ------ | ---------- | ----- |
| 2026-02-24 21:24:21 | `v1.0.3-test-server` | A    | pass   |            | i1:t=4.02s stop_status=2; i2:t=3.82s stop_status=2; i3:t=8.66s stop_status=2 |
| 2026-02-24 21:25:20 | `v1.0.3-test-server` | B    | pass   |            | i1:t=4.15s stop_status=2; i2:t=3.57s stop_status=2; i3:t=4.23s stop_status=2 |
| 2026-02-24 21:26:29 | `v1.0.3-test-server` | C    | pass   |            | i1:t=6.38s stop_status=2; i2:t=6.17s stop_status=2; i3:t=4.67s stop_status=2 |
| 2026-02-24 21:28:39 | `v1.0.3-test-server` | D    | pass   |            | t=0.96s; samples=10s:1|30s:1|60s:1|90s:1|120s:1; stop_status=2 |
| 2026-02-25 22:57:09 | `legacy-main-wip` | A    | pass   |            | `/api/v1/play_url` state=streaming; status=1; stop=OK; t=19.23s |
| 2026-02-25 22:57:27 | `legacy-main-wip` | B    | pass   |            | `/api/v1/play_url` state=streaming; status=1; stop=OK; t=17.40s |
| 2026-02-25 22:57:44 | `legacy-main-wip` | C    | pass   |            | `/api/v1/play_url` state=streaming; status=1; stop=OK; t=17.46s |
| 2026-02-25 22:57:58 | `legacy-main-wip` | D    | pass   |            | `/api/v1/play_url` state=streaming; status=0(固件差异); stop=OK; t=13.34s |

## 6. Jellyfin Playback Troubleshooting Note

- Symptom: web UI play returns failure or no sound for Jellyfin tracks while direct network stream still works.
- Root cause seen on test server: `hostname` drifted to a non-routable address (`http://192.168.2.5`), making generated proxy URLs unreachable by speaker.
- Quick check: call `/musicinfo?name=<track>` and verify URL host uses current LAN endpoint (for test server should be `http://<TEST_SERVER_HOST>:58090`).
- Fix: update setting `hostname` and `public_port`, then retry web UI playback.
- Verified after fix: multiple Jellyfin tracks played via `/playmusic`, player status reached `status=1` each run.

## 7. 2026-02-25 Additional Fix Note

- Symptom: `/api/v1/play_url` occasionally returned `state=streaming`, but runtime emitted `OSError: [Errno 98] Address in use` when reading `/network_audio/stream/{sid}`.
- Root cause: `v1` router and `network_audio (deprecated)` router each owned an independent runtime instance, both trying to bind the same local stream port.
- Fix: make `xiaomusic/api/routers/v1.py` reuse `network_audio (deprecated)` router runtime singleton so both routers share one runtime instance.
- Verification on `<TEST_SERVER_HOST>`: repeated play/stop no longer hit `Errno 98`; A/B/C/D smoke via `/api/v1/*` all pass.
