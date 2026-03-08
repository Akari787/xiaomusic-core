# Network Audio 6.2 真机手动验收记录

## 验收目标

- 内网场景下，从 B 站/YouTube 页面 URL 出发，最终向小爱设备投放本地流 URL 并出声。
- 停止后链路可回收；重复 play/stop 不崩溃。

## 手动验收步骤（真机）

1. 启动服务（控制面使用 `/api/v1/*`，观测接口保留 `/network_audio/healthz`、`/network_audio/sessions`）。
2. 使用以下任一 URL 请求 `POST /api/v1/play_url`：
   - `https://www.bilibili.com/video/BV14EcazWEna`
   - `https://www.youtube.com/watch?v=iPnaF8Ngk3Q`
   - `https://www.youtube.com/watch?v=vNG3-GRjrAo`
3. 记录返回的 `sid` 与 `stream_url`（应为 `/network_audio/stream/{sid}`）。
4. 由服务自动调用小爱投放（无需手工二次调用 adapter）。
5. 观察小爱是否在 30 秒内出声。
6. 查询 `/sessions`，确认 `state` 与 `reconnect_count` 可观测。
7. 下发停止（当前实现通过 session 停止接口），确认音频停止且会话状态收敛为 `stopped`。

## 本轮结果

- 组件级验收：通过（UT/CT 全绿）。
- 真机验收（2026-02-17，测试服务器 `<TEST_SERVER_HOST>`）：
  - 第 1 次失败：使用 `FakeSourceServer` 的占位字节流，设备端提示播放失败（原因：非可解码音频帧）。
  - 第 2 次修正：改用真实可解码 MP3 源（`/music/tmp/*.mp3`）经 network audio 本地流转发后投放。
  - 服务端日志确认：`playurl -> play_by_music_url(code=0) -> group_player_play` 链路成功。
  - 结论：链路打通；后续真机听感以用户侧“是否出声/是否连续播放”为准继续迭代。

## 记录模板

- 时间：
- 环境：
- URL：
- speaker_id：
- `/api/v1/play_url` 返回：
- XiaomiAdapter 返回：
- 出声耗时：
- 停止结果：
- 结论：通过 / 不通过
