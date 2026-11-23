# API v2 设计草案（待审阅）

状态：草案（2026-11-23，小克起草，等 Akari 审阅）
关联：[ADR-0007](../adr/0007-speaker-owned-queue.md) 第 6 步；现行规范 [api_v1_spec.md](./api_v1_spec.md)

## 1. 为什么要重新设计

v1 是在"服务端逐首指挥音箱"的架构上长出来的。改成"音箱持有队列、服务端跟随"（ADR-0007）以后，很多字段和接口已经没有意义：

- `POST /api/v1/play` 有一个查询字符串、一个来源提示、两套上下文（`context_hint`、`source_payload`）和十几个选项。其中 `confirm_start*` 已无效，`start_position` 音箱根本做不到（不能跳转），`timeout`、`prefer_proxy` 是服务端内部的事。
- 状态里同一个意思有好几种写法：`is_playing` / `transport_state`、`cur_music` / `track.title`、`offset` / `position_ms`、`duration` / `duration_ms`、`current_index` / `context.current_index`。
- 两代接口并存：`/api/v1/*` 37 个，旧式接口（`/playlistadd`、`/savesetting`、`/musiclist` …）66 个；`/api/admin/v1/sources` 与 `/api/v1/sources` 重复。
- 6 个 `debug/auth_*`、`control/probe`、`/diagnostics` 都是诊断，分散在三处。

Akari 的方针（2026-11-23）：**有必要就重新设计，不为兼容保留无用的东西；play 只保留一个接口，播什么由路由决定。**

## 2. 现状：谁在用哪些接口

| 调用方 | 用到的接口 |
|---|---|
| WebUI | `/api/v1/` 下 21 个（play、resolve、control/*、player/state、player/stream、devices、library/*、search/online、system/*），`/api/admin/v1/sources*`，以及 `/api/auth/*`、`/api/get_qrcode`、`/api/jellyfin/login`、`/api/jellyfin/logout`、`/api/file/cleantempdir`、`/diagnostics`、`/downloadlog` |
| Home Assistant | 经 **Node-RED**（192.168.7.6:1880）调用，具体接口**待查**（需要 Node-RED 账号或导出的流程） |
| 没有前端在用 | 旧式接口里的歌单增删改、`/savesetting`、`/getsetting`、`/musiclist`、`/searchmusic`、`/playingmusic`、`/curplaylist`、`/uploadmusic`、`/downloadplaylist`、`/delmusic`、`/setmusictag`、`/api/js-plugins*`、`/api/openapi/*`、`/api/plugin-source/*`、版本更新类等。**删除前先确认 Node-RED 没有调用。** |

音箱访问的媒体地址（`/stream/v1/...`、`/music/...`、`/relay/stream/...`、`/proxy/...`）不属于 API，本次不动。

## 3. 设计原则

1. **一个播放入口**：`POST /play`，来源由服务端按固定规则判断，调用方不传来源。
2. **设备放在路径里**：`/api/v2/devices/{device_id}/...`。HA、Node-RED 写起来更直观，也不用每个请求体都带 `device_id`。
3. **一种状态写法**：每个意思只有一个字段，时间统一用毫秒。
4. **不保留无效字段**：做不到的（跳转、暂停）和服务端内部的（超时、代理偏好）不出现在接口里。
5. **v1 与 v2 短期并存**：先上线 v2，WebUI 和 Node-RED 改完后删除 v1 和旧式接口。

## 4. 新接口清单

前缀统一为 `/api/v2`。响应外壳沿用 v1 的 `{code, message, data, request_id}`。

### 4.1 播放与控制（设备）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/devices/{device_id}/play` | 唯一播放入口，见第 5 节 |
| POST | `/devices/{device_id}/stop` | 停止 |
| POST | `/devices/{device_id}/next` | 下一首（当前会话内） |
| POST | `/devices/{device_id}/previous` | 上一首 |
| PUT | `/devices/{device_id}/volume` | `{"volume": 0-100}` |
| PUT | `/devices/{device_id}/play-mode` | `{"mode": "one" \| "all" \| "random" \| "single" \| "sequence"}` |
| PUT | `/devices/{device_id}/sleep-timer` | `{"minutes": N}`，0 = 取消（原 `control/shutdown-timer`） |

删除：`control/pause`、`control/resume`（已删）、`control/probe`（并入诊断）、`/api/v1/resolve`（改为 `play` 的 `dry_run`）。

### 4.2 状态

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/devices` | 设备列表 |
| GET | `/devices/{device_id}/state` | 当前状态快照 |
| GET | `/devices/{device_id}/events` | SSE，事件体与 `state` 相同 |

状态只有一种写法：

```json
{
  "device_id": "981257654",
  "revision": 42,
  "session": "sess_7",
  "state": "playing",            // idle | starting | switching | playing | stopping | stopped | error
  "track": {"id": "jellyfin:b63a…", "title": "Warm Light-牛尾 憲輔", "source": "jellyfin"},
  "playlist": {"name": "OTS", "index": 142, "length": 181},   // 单独链接时为 null
  "position_ms": 26000,
  "duration_ms": 89933,
  "volume": 30,
  "play_mode": "random",
  "up_next": [{"id": "jellyfin:…", "title": "…"}],             // 音箱队列里接下来的几首
  "error": null,                  // state=error 时说明原因
  "snapshot_at_ms": 1795391943025
}
```

删除的重复字段：`is_playing`、`cur_music`、`offset`、`duration`、`current_track_id`、`current_index`、`context_type`、`transport_state`（改名 `state`）、`server_instance_id`（并入 `session`）。新增 `up_next`、`play_mode`、`error`。

### 4.3 曲库与搜索

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/library/playlists` | 歌单列表 |
| GET | `/library/playlists/{name}` | 歌单内的曲目（按播放顺序） |
| GET | `/library/tracks/{track_id}` | 曲目信息（原 `library/music-info`） |
| POST | `/library/refresh` | 重新扫描/同步 |
| PUT | `/library/favorites/{track_id}` | 加入收藏 |
| DELETE | `/library/favorites/{track_id}` | 移出收藏 |
| GET | `/search?q=…` | 在线搜索，结果里的 `id` 可直接作为 `play` 的 `target` |

歌单增删改（旧式 `/playlistadd` 等）WebUI 没有入口，**是否保留取决于 Node-RED 是否在用**。

### 4.4 来源插件

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/sources` | 插件列表 |
| POST | `/sources` | 上传插件 |
| PUT | `/sources/{name}` | `{"enabled": true/false}` |
| DELETE | `/sources/{name}` | 删除插件 |
| POST | `/sources/reload` | 重新加载 |

合并 `/api/admin/v1/sources*` 与 `/api/v1/sources*`。

### 4.5 系统、登录与诊断

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/system/status` | 版本、运行状态 |
| GET | `/system/settings` | 设置 |
| PATCH | `/system/settings` | 改一项或多项（合并原 `settings` 与 `settings/item`） |
| GET | `/auth` | 小米账号登录状态（原 `/api/auth/status` 与 `/api/v1/auth/status`） |
| POST | `/auth/qrcode` | 发起扫码登录（原 `/api/get_qrcode`） |
| POST | `/auth/refresh` | 刷新登录 |
| POST | `/auth/logout` | 退出登录 |
| GET/POST/DELETE | `/jellyfin` | Jellyfin 状态 / 登录 / 退出 |
| POST | `/jellyfin/sync` | 同步 Jellyfin 歌单 |
| GET | `/diagnostics` | 合并 `/diagnostics`、`control/probe`、6 个 `debug/auth_*` |
| GET | `/diagnostics/log` | 下载日志（原 `/downloadlog`） |
| POST | `/system/clean-temp` | 清理临时目录（原 `/api/file/cleantempdir`） |

## 5. 播放接口

```
POST /api/v2/devices/{device_id}/play
{
  "target": "<要播的东西>",     // 可选（只给 playlist 时从记住的位置继续）
  "playlist": "OTS",           // 可选
  "dry_run": false             // 可选：只返回路由结果，不播放（取代 /resolve）
}
```

### 5.1 `target` 的路由规则（按顺序）

1. 曲库条目 ID：`jellyfin:…`、`local:…` → 曲库
2. 在线搜索结果 ID：`online:…` → 在线插件
3. URL：
   - 主机是配置的 Jellyfin → Jellyfin
   - YouTube、Bilibili 等视频网站 → 视频中转（yt-dlp）
   - `.m3u8` / `.m3u` 或电台地址 → 电台
   - 其他 http(s) → 直链
4. 本地路径或文件名 → 本地曲库
5. 以上都不是 → 当作歌名在曲库里查找（模糊匹配），找不到返回错误

### 5.2 `playlist` 决定播放方式

| 请求 | 行为 |
|---|---|
| `target` + `playlist` | 从 `target` 在歌单中的位置开始，按队列播放该歌单 |
| 只有 `playlist` | 从该歌单记住的位置（没有则第一首）开始按队列播放 |
| 只有 `target` | 单独播放这一首/这个链接 |

`target` 不在 `playlist` 里时返回错误，不静默改成单独播放。

### 5.3 返回路由结果

```json
{
  "accepted": true,
  "source": "site_media",        // jellyfin | local | site_media | radio | direct | online
  "mode": "single",              // queue | single
  "track": {"id": "…", "title": "…"},
  "playlist": null
}
```

`accepted` 只表示命令已受理；是否真正开播看状态里的 `state`（音箱拉流后变为 `playing`）。

## 6. 迁移步骤

1. 实现 v2（与 v1 并存），行为测试覆盖第 5 节的路由规则。
2. WebUI 切到 v2。
3. 改 Node-RED 流程（需要先看到流程；改动预计很小：换路径、请求体去掉 `device_id`、状态字段改名）。
4. 观察一段时间日志，确认没有 v1/旧式接口的调用后，删除 v1 和旧式接口。

## 7. 待 Akari 确认

1. 设备放路径里（`/devices/{id}/play`）还是继续放请求体里（`/play` + `device_id`）？草案用前者。
2. v1 与 v2 并存一小段时间再删，还是 WebUI 和 Node-RED 改好后直接替换？草案用前者，方便 Node-RED 逐步迁移。
3. 歌单增删改要不要保留？（取决于 Node-RED；WebUI 没有入口。）
4. 状态里的 `up_next` 显示几首？草案取音箱当前窗口里接下来的 5 首。
5. 需要先拿到 Node-RED 的流程（账号或导出的 JSON），才能确认第 2 节的"没有前端在用"里哪些可以删。
