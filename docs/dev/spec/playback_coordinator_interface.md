# XiaoMusic PlaybackCoordinator 接口规范

**PlaybackCoordinator Interface Specification**

版本：Draft v0.1
状态：草案
适用范围：XiaoMusic Runtime Core

---

# 1 文档目的

本文档定义 XiaoMusic Runtime 中 `PlaybackCoordinator` 的唯一正式接口。

`PlaybackCoordinator` 是 Runtime 的核心协调器，负责统一组织播放请求、来源解析、媒体准备与设备播放。

本文档用于约束：

* API 层如何调用 Runtime Core
* WebUI 如何通过 API 驱动播放
* SourcePlugin / Delivery / Transport 如何参与统一链路
* 后续开发者如何扩展播放流程而不破坏架构边界

---

# 2 核心定位

`PlaybackCoordinator` 是 **系统唯一播放协调入口**。

系统中所有与播放相关的正式业务请求，必须通过 `PlaybackCoordinator` 执行。

包括但不限于：

* 播放媒体
* 停止播放
* 暂停播放
* 恢复播放
* TTS 播报
* 调整音量
* 设备探测
* 来源预解析（仅解析，不播放）

---

# 3 非职责（Out of Scope）

`PlaybackCoordinator` 不负责以下内容：

1. 不直接实现来源解析逻辑
   来源解析必须由 `SourcePlugin` 完成。

2. 不直接实现设备通信逻辑
   设备通信必须由 `Transport` 完成。

3. 不直接处理 HTTP 请求
   HTTP 请求由 API 层负责。

4. 不负责 UI 状态管理
   WebUI 状态由前端负责。

5. 不负责插件注册
   插件注册由 `SourceRegistry` 与 `TransportRegistry`（若存在）负责。

---

# 4 依赖组件

`PlaybackCoordinator` 依赖以下核心组件：

* `SourceRegistry`
* `DeviceRegistry`
* `DeliveryAdapter`
* `TransportRouter`

`PlaybackCoordinator` 不应依赖：

* WebUI
* HTTP Request 对象
* 插件内部实现细节
* 具体 Mina / Miio 客户端

---

# 5 对外接口

`PlaybackCoordinator` 对外只暴露以下正式方法。

---

## 5.1 play()

### 作用

执行统一播放流程。

### 方法签名（逻辑定义）

```python
play(request: PlayRequest) -> PlayResult
```

### 输入

`PlayRequest`

字段建议：

* `device_id: str`
* `query: str`
* `source_hint: str = "auto"`
* `options: dict = {}`
* `request_id: str | None = None`

### 输出

`PlayResult`

字段建议：

* `status: str`
* `device_id: str`
* `source_plugin: str`
* `transport: str`
* `request_id: str`
* `extra: dict`

### 执行流程

```text
PlayRequest
  ↓
SourceRegistry.get_plugin()
  ↓
SourcePlugin.resolve()
  ↓
DeliveryAdapter.prepare()
  ↓
TransportRouter.dispatch_play()
  ↓
PlayResult
```

### 约束

* `play()` 是唯一正式播放入口
* 不允许 API 层绕过 `play()` 直接调用来源插件或 transport
* 不允许 `play()` 内部写来源特判散落逻辑
* 所有来源判断必须经由 `SourceRegistry`

---

## 5.2 resolve()

### 作用

只解析媒体来源，不执行播放。

### 方法签名

```python
resolve(request: ResolveRequest) -> ResolveResult
```

### 输入

`ResolveRequest`

字段建议：

* `query: str`
* `source_hint: str = "auto"`
* `request_id: str | None = None`

### 输出

`ResolveResult`

字段建议：

* `resolved: bool`
* `source_plugin: str`
* `media: dict`
* `request_id: str`
* `extra: dict`

### 用途

* WebUI 测试播放前预解析
* 调试来源插件
* 错误排查

### 约束

* `resolve()` 不得触发设备播放
* `resolve()` 不得调用 `Transport.play_url()`

---

## 5.3 stop()

### 作用

停止设备当前播放。

### 方法签名

```python
stop(device_id: str, request_id: str | None = None) -> ControlResult
```

### 输出

`ControlResult`

字段建议：

* `status: str`
* `device_id: str`
* `transport: str`
* `request_id: str`

### 执行流程

```text
device_id
  ↓
DeviceRegistry.get_device()
  ↓
TransportRouter.dispatch_stop()
  ↓
ControlResult
```

---

## 5.4 pause()

### 作用

暂停当前播放。

### 方法签名

```python
pause(device_id: str, request_id: str | None = None) -> ControlResult
```

---

## 5.5 resume()

### 作用

恢复当前播放。

### 方法签名

```python
resume(device_id: str, request_id: str | None = None) -> ControlResult
```

---

## 5.6 tts()

### 作用

执行 TTS 播报。

### 方法签名

```python
tts(device_id: str, text: str, request_id: str | None = None) -> ControlResult
```

### 约束

* `tts()` 不属于来源插件流程
* `tts()` 直接走设备控制链路
* `tts()` 不得借道 `play()` 实现

---

## 5.7 set_volume()

### 作用

设置设备音量。

### 方法签名

```python
set_volume(device_id: str, volume: int, request_id: str | None = None) -> ControlResult
```

### 约束

* `volume` 必须由 API 层或 Coordinator 进行范围校验
* 不得把非法值直接传给 Transport

---

## 5.8 probe()

### 作用

探测设备可用性与能力状态。

### 方法签名

```python
probe(device_id: str, request_id: str | None = None) -> ProbeResult
```

### 输出

`ProbeResult`

字段建议：

* `device_id: str`
* `reachable: bool`
* `transport_used: str`
* `capabilities: dict`
* `request_id: str`

### 约束

* `probe()` 的结果必须最终写回 `DeviceRegistry`
* `Transport` 只返回探测结果，不直接改设备状态

---

# 6 输入输出模型规范

本章节只定义 Coordinator 层使用的逻辑模型，不定义 HTTP JSON 结构。

---

## 6.1 PlayRequest

`PlayRequest` 是播放请求模型。

必须至少包含：

* `device_id`
* `query`
* `source_hint`
* `options`
* `request_id`

---

## 6.2 ResolveRequest

`ResolveRequest` 是来源解析请求模型。

必须至少包含：

* `query`
* `source_hint`
* `request_id`

---

## 6.3 PlayResult

`PlayResult` 是播放动作结果模型。

必须至少包含：

* `status`
* `device_id`
* `source_plugin`
* `transport`
* `request_id`

---

## 6.4 ControlResult

`ControlResult` 是控制动作结果模型。

适用于：

* `stop`
* `pause`
* `resume`
* `tts`
* `set_volume`

必须至少包含：

* `status`
* `device_id`
* `transport`
* `request_id`

---

## 6.5 ProbeResult

`ProbeResult` 是设备探测结果模型。

必须至少包含：

* `device_id`
* `reachable`
* `transport_used`
* `request_id`

---

# 7 错误处理规范

`PlaybackCoordinator` 是 Runtime Core 的统一错误汇总点。

允许抛出的核心异常类型包括：

* `SourceResolveError`
* `DeliveryPrepareError`
* `TransportError`
* `DeviceNotFoundError`
* `InvalidRequestError`

约束：

1. `PlaybackCoordinator` 内部可以抛业务异常
2. API 层必须将这些异常转换为统一 `ApiResponse`
3. `PlaybackCoordinator` 不返回 HTTP 风格错误对象

---

# 8 日志规范

`PlaybackCoordinator` 必须输出结构化日志。

最少包含：

* `request_id`
* `device_id`
* `action`
* `source_plugin`（若适用）
* `transport`（若适用）
* `stage`
* `result`

示例：

```text
[PlaybackCoordinator] request_id=req_123 action=play device_id=speaker01 source_plugin=DirectUrlSourcePlugin transport=mina result=success
```

---

# 9 架构约束

以下规则为强约束。

## 规则 1

`PlaybackCoordinator` 是 Runtime Core 唯一正式协调入口。

## 规则 2

API 层不得直接调用 `SourcePlugin.resolve()`。

## 规则 3

API 层不得直接调用 `Transport.play_url()`。

## 规则 4

WebUI 不得绕过 API 层直接触达 Coordinator。

## 规则 5

所有播放相关输入，必须先转换为 `PlayRequest`。

## 规则 6

所有来源解析相关输入，必须先转换为 `ResolveRequest`。

## 规则 7

Coordinator 不得感知具体插件内部实现细节。

---

# 10 与 API v1 的关系

HTTP API 与 Coordinator 的映射关系如下：

| API                                | Coordinator    |
| ---------------------------------- | -------------- |
| `POST /api/v1/play`                | `play()`       |
| `POST /api/v1/resolve`             | `resolve()`    |
| `POST /api/v1/control/stop`        | `stop()`       |
| `POST /api/v1/control/pause`       | `pause()`      |
| `POST /api/v1/control/resume`      | `resume()`     |
| `POST /api/v1/control/tts`         | `tts()`        |
| `POST /api/v1/control/volume`      | `set_volume()` |
| `POST /api/v1/control/probe` 或等效接口 | `probe()`      |

API 层只负责：

* 参数校验
* 请求模型转换
* 异常转 `ApiResponse`

业务逻辑必须进入 `PlaybackCoordinator`。

---

# 11 与插件系统的关系

`PlaybackCoordinator` 与插件系统的关系如下：

* 通过 `SourceRegistry` 选择来源插件
* 通过 `DeliveryAdapter` 处理媒体准备
* 通过 `TransportRouter` 调度设备通信

插件不得反向依赖 `PlaybackCoordinator`。
