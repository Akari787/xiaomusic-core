# XiaoMusic Runtime 架构规范

**XiaoMusic Runtime Architecture Specification**

版本：Draft v0.1
状态：草案（Draft）
适用范围：xiaomusic-oauth2 项目 Runtime 核心架构

---

# 1 项目目标（Project Goals）

## 1.1 项目背景

XiaoMusic Runtime 是一个用于控制小米智能音箱播放媒体内容的统一播放框架。

项目的核心目标是：

* 提供统一的播放控制入口
* 支持多种媒体来源
* 支持插件化扩展媒体来源
* 解耦媒体来源解析与设备播放
* 减少对小米云端服务的依赖
* 提供稳定的 API 供 WebUI 与自动化系统调用

系统的设计重点是：

**统一播放链路 + 插件化来源 + 可维护架构**

---

## 1.2 系统目标

Runtime 必须满足以下目标：

1. 提供统一的播放请求入口
2. 支持多种媒体来源
3. 支持来源插件化扩展
4. 解耦来源解析与设备通信
5. 提供稳定 API 接口
6. 提供清晰的架构边界

---

## 1.3 非目标（Non Goals）

XiaoMusic Runtime **不是**以下系统：

* 完整的媒体服务器
* 媒体下载器
* 媒体库管理系统
* 视频转码系统
* 音乐推荐系统

Runtime 只负责：

**媒体解析 + 媒体投递 + 设备播放**

---

# 2 系统总体架构（Architecture Overview）

XiaoMusic Runtime 采用分层架构设计。

核心架构如下：

```
WebUI / API Client
        │
        ▼
      API Layer
        │
        ▼
PlaybackCoordinator
        │
 ┌──────┼──────────────┐
 ▼                     ▼
SourcePlugins      DeliveryAdapter
                        │
                        ▼
                 TransportRouter
                        │
                        ▼
                     Transport
                        │
                        ▼
                 Xiaomi Speaker
```

系统由以下核心组件组成：

| 组件                  | 职责          |
| ------------------- | ----------- |
| API Layer           | 提供 HTTP API |
| PlaybackCoordinator | 播放流程协调      |
| SourcePlugin        | 媒体来源解析      |
| DeliveryAdapter     | 媒体投递准备      |
| TransportRouter     | 选择播放通道      |
| Transport           | 设备通信        |

---

# 3 核心组件说明（Core Components）

## 3.1 PlaybackCoordinator

PlaybackCoordinator 是系统的**播放流程协调器**。

职责：

* 接收播放请求
* 调用 SourcePlugin 解析媒体
* 调用 DeliveryAdapter 准备媒体流
* 调用 TransportRouter 选择设备通信方式
* 调用 Transport 执行播放

PlaybackCoordinator 是 **唯一播放入口**。

系统中的所有播放请求必须经过 PlaybackCoordinator。

---

## 3.2 SourcePlugin

SourcePlugin 用于解析媒体来源。

SourcePlugin 的职责：

* 识别输入媒体来源
* 解析媒体信息
* 生成统一媒体对象

SourcePlugin 输出统一结构：

```
ResolvedMedia
```

SourcePlugin **不得直接调用设备播放逻辑**。

---

## 3.3 DeliveryAdapter

DeliveryAdapter 用于将媒体转换为设备可播放格式。

职责包括：

* 处理直链媒体
* 处理代理流
* 处理设备兼容性
* 生成播放流对象

输出：

```
PreparedStream
```

---

## 3.4 TransportRouter

TransportRouter 用于选择设备通信方式。

不同设备可能支持不同协议，例如：

* Miio
* Mina

TransportRouter 根据设备能力选择正确的 Transport。

---

## 3.5 Transport

Transport 负责设备通信。

Transport 的职责：

* 发送播放命令
* 控制设备状态
* 执行播放控制

Transport 不负责媒体解析。

---

# 4 SourcePlugin 体系

Runtime 使用插件架构支持多种媒体来源。

默认内置来源插件如下：

| 插件                       | 职责             |
| ------------------------ | -------------- |
| DirectUrlSourcePlugin    | 直接媒体 URL       |
| SiteMediaSourcePlugin    | 网站媒体解析         |
| JellyfinSourcePlugin     | Jellyfin 媒体服务器 |
| LocalLibrarySourcePlugin | 本地媒体库          |

---

## 4.1 DirectUrlSourcePlugin

处理直接媒体 URL。

例如：

* MP3 直链
* FLAC 直链
* M3U8 流

该插件不负责页面解析。

---

## 4.2 SiteMediaSourcePlugin

处理网站媒体来源。

例如：

* YouTube
* Bilibili
* 其他媒体站点

该插件通常使用：

* yt-dlp
* 页面解析

---

## 4.3 JellyfinSourcePlugin

用于从 Jellyfin 媒体服务器获取媒体。

职责：

* 查询媒体信息
* 生成播放 URL

---

## 4.4 LocalLibrarySourcePlugin

用于处理本地媒体库。

来源包括：

* 本地文件路径
* 本地媒体库 ID

---

## 4.5 SourceRegistry

SourceRegistry 用于管理来源插件。

职责：

* 注册 SourcePlugin
* 根据输入选择插件
* 返回可处理插件

选择规则：

```
source_hint 优先
否则自动识别来源
```

---

# 5 播放流程（Playback Pipeline）

Runtime 的播放流程如下：

```
play request
      │
      ▼
source resolve
      │
      ▼
media normalization
      │
      ▼
delivery preparation
      │
      ▼
transport dispatch
      │
      ▼
device playback
```

每一步必须保持明确的输入与输出结构。

---

# 6 API 规范（API Specification）

Runtime 提供 HTTP API。

API 命名空间：

```
/api/v1
```

核心接口包括：

```
POST /api/v1/play
POST /api/v1/control/stop
POST /api/v1/control/pause
POST /api/v1/control/tts
POST /api/v1/control/volume
GET  /api/v1/devices
POST /api/v1/resolve
```

---

## 6.1 统一响应结构

所有 API 必须返回统一结构：

```
{
  "code": 0,
  "message": "ok",
  "data": {},
  "request_id": ""
}
```

错误返回：

```
{
  "code": 10001,
  "message": "resolve failed",
  "data": {},
  "request_id": ""
}
```

---

# 7 WebUI 调用规范

WebUI 必须通过 API 与 Runtime 通信。

调用路径：

```
WebUI → API → Coordinator
```

WebUI **不得直接调用 Runtime 内部逻辑**。

WebUI 不应自行判断媒体来源。

来源判断必须由 Runtime 完成。

---

# 8 插件开发指南（Plugin Development）

开发者可以通过实现 SourcePlugin 扩展来源。

插件必须实现以下接口：

```
can_handle(input)
resolve(input)
```

插件返回统一媒体结构：

```
ResolvedMedia
```

插件不得：

* 调用 Transport
* 修改 Coordinator
* 修改 Runtime 全局状态

---

# 9 架构约束（Architecture Rules）

以下规则为 Runtime 架构约束。

违反这些规则将破坏系统结构。

---

## 规则 1

PlaybackCoordinator 是唯一播放入口。

---

## 规则 2

SourcePlugin 不得调用 Transport。

---

## 规则 3

WebUI 不得绕过 API。

---

## 规则 4

所有媒体必须先 Resolve。

---

## 规则 5

Transport 只负责设备通信。

---

# 10 设计原则（Design Philosophy）

Runtime 的设计遵循以下原则：

### 统一播放链路

所有播放请求走统一流程。

### 来源插件化

媒体来源必须可扩展。

### 设备通信解耦

媒体解析与设备通信分离。

### API 稳定

API 必须保持稳定与一致。

---

# 文档位置建议

该文档建议放置在：

```
docs/spec/runtime_specification.md
```

原因：

* `docs/` 用于项目文档
* `spec/` 用于架构规范
* runtime_specification 是核心架构文档

最终目录建议：

```
docs/
 ├─ spec/
 │   └─ runtime_specification.md
 │
 ├─ api/
 │   └─ api_v1_spec.md
 │
 └─ implementation/
     └─ ...
```

---

# 11 核心数据结构规范（Core Data Structures）

Runtime 使用统一的数据结构在各组件之间传递媒体信息。

核心数据结构包括：

* ResolvedMedia
* PreparedStream

所有组件必须使用这些结构进行通信。

---

# 11.1 ResolvedMedia

ResolvedMedia 表示 **来源解析后的媒体对象**。

该对象由 SourcePlugin 生成。

ResolvedMedia 只描述媒体本身，不包含设备信息。

示例结构：

```json
{
  "id": "media_12345",
  "title": "Example Song",
  "artist": "Unknown",
  "duration": 240,
  "source": "direct_url",
  "stream_url": "https://example.com/song.mp3",
  "mime_type": "audio/mpeg",
  "headers": {},
  "extra": {}
}
```

字段说明：

| 字段         | 说明        |
| ---------- | --------- |
| id         | 媒体唯一标识    |
| title      | 媒体标题      |
| artist     | 作者        |
| duration   | 媒体时长（秒）   |
| source     | 来源插件      |
| stream_url | 可访问媒体 URL |
| mime_type  | 媒体类型      |
| headers    | 请求头       |
| extra      | 插件扩展字段    |

---

## ResolvedMedia 设计原则

1. 必须包含 `stream_url`
2. 不包含设备信息
3. 不包含 transport 信息
4. 插件可在 `extra` 中存储扩展信息

---

# 11.2 PreparedStream

PreparedStream 表示 **准备投递到设备的媒体流**。

该对象由 DeliveryAdapter 生成。

示例结构：

```json
{
  "url": "http://runtime-proxy/stream/123",
  "mime_type": "audio/mpeg",
  "headers": {},
  "is_proxy": true
}
```

字段说明：

| 字段        | 说明       |
| --------- | -------- |
| url       | 最终播放 URL |
| mime_type | 媒体类型     |
| headers   | 请求头      |
| is_proxy  | 是否代理流    |

---

## PreparedStream 设计原则

1. PreparedStream 必须可被设备直接播放
2. 不包含来源信息
3. DeliveryAdapter 可以修改 URL
4. 代理流必须明确标识

---

# 11.3 数据结构流转

数据流转如下：

```
SourcePlugin
   │
   ▼
ResolvedMedia
   │
   ▼
DeliveryAdapter
   │
   ▼
PreparedStream
   │
   ▼
Transport
```

任何组件不得绕过该结构。

---

# 12 错误码规范（Error Code Specification）

Runtime 必须使用统一错误码。

错误码格式：

```
ABCDE
```

含义：

| 位  | 含义   |
| -- | ---- |
| A  | 模块   |
| BC | 子模块  |
| DE | 错误类型 |

---

## 12.1 模块编号

| 模块        | 编号 |
| --------- | -- |
| 系统        | 1  |
| Source    | 2  |
| Delivery  | 3  |
| Transport | 4  |
| API       | 5  |

---

## 12.2 常见错误码

| 错误码   | 含义            |
| ----- | ------------- |
| 10000 | 系统未知错误        |
| 20001 | 来源无法识别        |
| 20002 | 来源解析失败        |
| 20003 | 媒体不存在         |
| 30001 | Delivery 准备失败 |
| 40001 | Transport 不支持 |
| 40002 | 设备通信失败        |
| 50001 | API 参数错误      |

---

## 12.3 错误返回示例

```json
{
  "code": 20002,
  "message": "source resolve failed",
  "data": {
    "plugin": "SiteMediaSourcePlugin"
  }
}
```

---

# 13 插件生命周期规范（Plugin Lifecycle）

Runtime 插件遵循统一生命周期。

插件类型包括：

* SourcePlugin
* TransportPlugin
* DeliveryAdapter

---

## 13.1 插件加载

Runtime 启动时加载插件。

加载流程：

```
扫描插件目录
↓
加载插件类
↓
注册插件
↓
初始化插件
```

---

## 13.2 插件注册

插件必须通过注册器注册。

例如：

```
SourceRegistry.register(plugin)
```

插件必须声明：

* name
* version
* capability

---

## 13.3 插件能力声明

插件必须声明能力：

示例：

```
plugin.capabilities = [
    "direct_url",
    "site_media"
]
```

Registry 根据能力选择插件。

---

## 13.4 插件初始化

插件初始化阶段允许：

* 加载配置
* 初始化客户端
* 初始化缓存

禁止：

* 网络长连接
* 启动线程

---

## 13.5 插件执行

插件执行阶段：

```
can_handle()
resolve()
```

插件必须是：

**无副作用函数**

---

## 13.6 插件卸载

Runtime 关闭时插件必须：

* 关闭连接
* 清理缓存
* 释放资源

---

# 14 插件目录结构规范

插件推荐结构：

```
plugins/
   source/
       direct_url/
       site_media/
       jellyfin/
       local_library/

   transport/
       miio/
       mina/
```

每个插件目录包含：

```
plugin.py
manifest.json
```

---

# 15 日志规范（Logging Specification）

Runtime 必须使用统一日志结构。

日志必须包含：

* module
* request_id
* stage
* message

示例：

```
[PlaybackCoordinator] request_id=123 resolve success
```

---

# 16 可扩展性原则

Runtime 必须支持未来扩展：

* 新 SourcePlugin
* 新 Transport
* 新媒体来源

扩展不得修改核心架构。

---

# 最终文档结构

最终 runtime_specification.md 建议结构：

```
1 项目目标
2 架构概览
3 核心组件
4 SourcePlugin体系
5 播放流程
6 API规范
7 WebUI调用规范
8 插件开发指南
9 架构约束
10 设计原则
11 核心数据结构
12 错误码规范
13 插件生命周期
14 插件目录结构
15 日志规范
16 可扩展性
```
