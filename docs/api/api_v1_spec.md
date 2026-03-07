# XiaoMusic Runtime API v1 规范

**XiaoMusic Runtime API v1 Specification**

版本：Draft v0.1
状态：草案
适用范围：XiaoMusic Runtime HTTP API

---

# 1 API 设计原则

Runtime API 遵循以下原则：

## 1.1 单一播放入口

所有播放请求必须通过：

```
POST /api/v1/play
```

不允许存在多个播放入口。

---

## 1.2 统一请求结构

所有 API 请求必须使用 JSON。

请求结构必须明确。

---

## 1.3 统一响应结构

所有 API 返回统一结构：

```json
{
  "code": 0,
  "message": "ok",
  "data": {},
  "request_id": "xxxx"
}
```

---

## 1.4 统一错误处理

错误必须使用统一错误码。

不允许返回未定义结构。

---

## 1.5 WebUI 不负责业务判断

WebUI 不应判断：

* 来源类型
* 插件类型
* transport 类型

这些逻辑必须由 Runtime 完成。

---

# 2 API 命名空间

Runtime API 命名空间：

```
/api/v1
```

所有接口必须以 `/api/v1` 开头。

---

# 3 API 分类

Runtime API 分为四类：

| 类型   | 说明   |
| ---- | ---- |
| 播放接口 | 媒体播放 |
| 控制接口 | 设备控制 |
| 查询接口 | 状态查询 |
| 解析接口 | 媒体解析 |

---

# 4 播放接口

## POST /api/v1/play

播放媒体。

该接口是 **Runtime 唯一播放入口**。

---

## 请求结构

```json
{
  "device_id": "speaker123",
  "query": "https://example.com/song.mp3",
  "source_hint": "auto",
  "options": {
    "start_position": 0,
    "proxy_mode": "auto"
  }
}
```

---

## 字段说明

| 字段          | 说明   |
| ----------- | ---- |
| device_id   | 目标设备 |
| query       | 媒体输入 |
| source_hint | 来源提示 |
| options     | 播放选项 |

---

### source_hint

可选值：

```
auto
direct_url
site_media
jellyfin
local_library
```

默认：

```
auto
```

当为 auto 时：

Runtime 自动识别来源。

---

### query 示例

可能输入：

```
https://example.com/song.mp3
https://youtube.com/...
https://bilibili.com/...
jellyfin:track:12345
/local/music/song.mp3
```

---

## 成功响应

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "device_id": "speaker123",
    "source_plugin": "DirectUrlSourcePlugin",
    "transport": "mina",
    "status": "playing"
  },
  "request_id": "req_123"
}
```

---

## 失败响应

```json
{
  "code": 20002,
  "message": "source resolve failed",
  "data": {
    "plugin": "SiteMediaSourcePlugin"
  },
  "request_id": "req_123"
}
```

---

# 5 控制接口

## POST /api/v1/control/stop

停止播放。

请求：

```json
{
  "device_id": "speaker123"
}
```

---

## POST /api/v1/control/pause

暂停播放。

---

## POST /api/v1/control/resume

恢复播放。

---

## POST /api/v1/control/volume

设置音量。

请求：

```json
{
  "device_id": "speaker123",
  "volume": 50
}
```

---

## POST /api/v1/control/tts

TTS 播报。

请求：

```json
{
  "device_id": "speaker123",
  "text": "Hello world"
}
```

---

# 6 查询接口

## GET /api/v1/devices

获取设备列表。

响应：

```json
{
  "code": 0,
  "data": {
    "devices": [
      {
        "device_id": "speaker123",
        "name": "Living Room",
        "model": "xiaomi_sound_pro",
        "online": true
      }
    ]
  }
}
```

---

## GET /api/v1/system/status

获取 Runtime 状态。

---

# 7 解析接口

## POST /api/v1/resolve

解析媒体来源。

该接口不会播放媒体。

仅用于测试与调试。

---

## 请求

```json
{
  "query": "https://youtube.com/...",
  "source_hint": "auto"
}
```

---

## 响应

```json
{
  "code": 0,
  "data": {
    "source_plugin": "SiteMediaSourcePlugin",
    "resolved": true,
    "media": {
      "title": "Example Video",
      "duration": 200
    }
  }
}
```

---

# 8 WebUI 调用规范

WebUI 只允许调用以下接口：

```
/api/v1/play
/api/v1/control/*
/api/v1/devices
/api/v1/resolve
```

---

WebUI 不得调用：

* Runtime 内部函数
* 插件逻辑
* Transport

---

# 9 兼容策略

旧接口可以暂时保留：

例如：

```
/api/v1/play_url
```

但必须：

* 仅作为 wrapper
* 内部调用 `/api/v1/play`

未来版本将移除旧接口。

---

# 10 错误码

错误码参考：

```
runtime_specification.md
```

模块划分：

```
1xxxx 系统
2xxxx 来源
3xxxx delivery
4xxxx transport
5xxxx API
```
