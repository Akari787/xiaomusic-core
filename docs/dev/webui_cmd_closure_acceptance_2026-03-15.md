# WebUI `/cmd` 收口验收记录

日期：2026-03-15  
环境：`<test-server-ip>` / `xiaomusic-core` / 真实设备 `<device_id>`

## 本轮目标

1. WebUI 正式页面不再保留 `/cmd` 调用入口。  
2. 残留旧文案与当前实现保持一致。  
3. 正式功能仍通过 `/api/v1/*` 正常工作。  
4. 页面静态资源更新后不出现明显构建/渲染回归。  

## 代码侧核查

### `/cmd` 前端调用点核查

对 `xiaomusic/webui/src` 执行检索：

- `sendCustomCmd`
- `apiPost("/cmd"`
- `/cmd`
- `自定义口令`

结果：正式页面源码中**未发现** `/cmd` 调用入口或 `sendCustomCmd()` 残留。  

### 残留旧文案修正

修正前：

- `兼容口令入口：当前通过设备语音命令链路执行。`

修正后：

- `定时设置会直接通过正式控制接口发送到设备。`

## 服务器静态资源核查

服务实际加载静态资源：

- `/webui/assets/index-DUFeigp5.js`

对当前生效 bundle 检查结果：

- `has_cmd = false`
- `has_custom_cmd_text = false`
- `has_timer_text = true`
- `has_old_timer_text = false`

说明：当前线上实际生效前端 bundle 已不包含 `/cmd` 字符串，也不包含“自定义口令”“兼容口令入口”“语音命令链路”等旧提示语。

## 实机验收

说明：当前环境无法直接导出浏览器 DevTools Network 面板，因此采用“线上实际生效 bundle 不含 `/cmd` + 真实设备动作由 `/api/v1/*` 驱动成功”作为收口证据。

### 场景 1：下一首

操作：

1. 通过歌单接口让设备进入 `BGM` 歌单。
2. 触发下一首控制。
3. 检查当前歌单未被重置，设备状态与页面状态读取接口一致。

请求：

```bash
curl -s -X POST http://127.0.0.1:58090/api/v1/control/next \
  -H 'Content-Type: application/json' \
  -d '{"device_id":"<device_id>"}'
```

返回：

```json
{"code":0,"message":"ok","data":{"status":"ok","device_id":"<device_id>","action":"next"},"request_id":"dfbbc40e54a4402d"}
```

附加检查：

- `GET /curplaylist?did=<device_id>` -> `"BGM"`

结论：通过。正式“下一首”能力走 `/api/v1/control/next`，未回退到 `/cmd`。

### 场景 2：上一首

请求：

```bash
curl -s -X POST http://127.0.0.1:58090/api/v1/control/previous \
  -H 'Content-Type: application/json' \
  -d '{"device_id":"<device_id>"}'
```

返回：

```json
{"code":0,"message":"ok","data":{"status":"ok","device_id":"<device_id>","action":"previous"},"request_id":"8068adc3e7a84a18"}
```

附加检查：

- `GET /curplaylist?did=<device_id>` -> `"BGM"`

结论：通过。正式“上一首”能力走 `/api/v1/control/previous`。

### 场景 3：歌单内点击播放指定歌曲

请求：

```bash
curl -s -X POST http://127.0.0.1:58090/api/v1/playlist/play \
  -H 'Content-Type: application/json' \
  -d '{"device_id":"<device_id>","playlist_name":"其他","music_name":"local_silence"}'
```

返回：

```json
{"code":0,"message":"ok","data":{"status":"ok","device_id":"<device_id>","playlist_name":"其他","music_name":"local_silence"},"request_id":"48b78ca60e3d4f12"}
```

播放状态：

```json
{"code":0,"message":"ok","data":{"device_id":"<device_id>","is_playing":true,"cur_music":"local_silence","offset":82,"duration":148},"request_id":"4185d977c23545c9"}
```

结论：通过。歌单播放正式走 `/api/v1/playlist/play`。

### 场景 4：连续快速点击上一首/下一首 2~3 次

操作：

1. 设定在 `BGM` 歌单内播放。
2. 连续执行两次 `next`。
3. 检查最终歌单仍为 `BGM`，播放器状态可正常读取。

请求序列：

```bash
curl -s -X POST http://127.0.0.1:58090/api/v1/control/next -H 'Content-Type: application/json' -d '{"device_id":"<device_id>"}'
curl -s -X POST http://127.0.0.1:58090/api/v1/control/next -H 'Content-Type: application/json' -d '{"device_id":"<device_id>"}'
```

最终状态：

```json
{"code":0,"message":"ok","data":{"device_id":"<device_id>","is_playing":true,"cur_music":"白いスーツのテーマ-市川淳","offset":3,"duration":165},"request_id":"b45b538b0c044e80"}
```

附加检查：

- `GET /curplaylist?did=<device_id>` -> `"BGM"`

结论：通过。连续切歌后最终状态与设备一致，且歌单未被错误重置。

### 场景 5：收藏、定时关机等正式功能不再出现 `/cmd`

证据 A：当前线上实际生效 bundle 不包含 `/cmd`。  
证据 B：对应功能的后端调用已切换为结构化接口：

- 收藏：`POST /api/v1/library/favorites/add`
- 取消收藏：`POST /api/v1/library/favorites/remove`
- 定时关机：`POST /api/v1/control/shutdown-timer`

定时关机示例返回：

```json
{"code":0,"message":"ok","data":{"status":"ok","device_id":"<device_id>","minutes":1},"request_id":"57a8b0ca124a49f1"}
```

结论：通过。正式功能链路已经不再依赖 `/cmd`。

### 场景 6：页面布局与显示回归

当前环境无法直接导出浏览器截图对比，但完成了以下验证：

1. 服务实际加载的新 bundle 正常：`/webui/assets/index-DUFeigp5.js`。  
2. 静态资源与主页均可正常返回。  
3. 不存在旧 `/cmd` 文案残留。  
4. 未引入新的布局类 CSS 改动，仅替换一条提示语。  

结论：未发现构建级或静态资源级显示回归风险。

## 最终结果

- 前端正式页面已无 `/cmd` 调用入口。  
- 旧文案已修正，并与当前 `/api/v1/*` 实现一致。  
- 测试服务器上，正式控制能力可通过真实设备验证。  
- 当前未发现本轮 `/cmd` 收口的残留阻断项。  

## 备注

- 若后续需要更严格的“浏览器 Network 面板截图级”验收，可在带图形浏览器的环境补做一次人工复核；但从当前生效 bundle 与真实设备调用结果看，本轮收口已经完成。
