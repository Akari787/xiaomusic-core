# 播放架构说明

## 1. 播放入口

`POST /api/v1/play` 是唯一正式播放入口。

所有播放请求必须通过此接口进入统一播放执行路径。

---

## 2. 核心概念

### 2.1 Source Plugin（来源插件）

每个来源实现统一接口：

- `direct_url`：HTTP/HTTPS 直链
- `site_media`：站点媒体（YouTube/Bilibili 等）
- `jellyfin`：Jellyfin 资源
- `local_library`：本地媒体库

职责：
- 解析请求
- 提供播放事实（不负责策略）

---

### 2.2 Playback Context（播放上下文）

表示当前播放所属的"集合语义"：

- 歌单
- 专辑
- 搜索结果
- 单曲

约束：一个设备只能有一个当前激活上下文。

---

### 2.3 Playback Kind（播放类型）

- `single`：单媒体，无队列
- `queue`：队列播放，支持 next/previous
- `stream`：流式播放，不具备传统队列语义

---

### 2.4 Play Mode（播放模式）

由框架统一定义：

- `one`
- `single`
- `sequence`
- `all`
- `random`

---

## 3. 执行路径

所有播放行为必须遵循：

```
POST /api/v1/play
    ↓
Source Plugin resolve
    ↓
Playback Resolution
    ↓
Build Unified Context
    ↓
Device Player State Machine
```

禁止：
- 直接调用 play_url 绕过状态机
- 不同来源走不同执行链路

---

## 4. 请求模型

```json
{
  "device_id": "...",
  "query": "...",
  "source_hint": "auto",
  "context_hint": {
    "context_type": "...",
    "context_id": "...",
    "context_name": "..."
  }
}
```

规则：
- `context_hint` 为可选字段
- 一旦提供，来源适配器必须优先使用
- 若未提供，来源适配器自行选择上下文

---

## 5. 职责边界

### 5.1 来源适配器负责（事实）

- source_type
- playback_kind
- current_track 信息
- context 信息
- queue_supported / queue_length / current_index

### 5.2 框架负责（策略）

- play_mode
- has_next / has_previous
- 播放结束行为
