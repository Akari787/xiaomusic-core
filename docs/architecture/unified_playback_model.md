# Unified Playback Model（统一播放模型）

## 1. 背景与问题

当前系统的播放能力存在以下核心问题：

### 1.1 多入口语义不一致
- `/api/v1/play`
- `/api/v1/playlist/*`
- 以及内部不同播放函数（play_url / play_music_list / group_player_play 等）

这些接口和路径在语义上存在重叠，但行为不一致。

---

### 1.2 多执行链路导致状态分裂

不同来源（本地 / Jellyfin / 网络链接）通过不同代码路径触发播放：

- 部分路径未进入统一状态机
- 部分路径不会设置自动下一首
- 部分路径不会更新当前播放上下文

导致：

- WebUI 显示与实际播放不一致
- 自动下一首行为异常
- 刷新后出现“未知音乐”

---

### 1.3 缺乏统一播放上下文模型

当前系统没有一套统一的播放状态描述：

- 不同来源返回不同结构
- 前端无法稳定解析
- HA / 自动化无法可靠判断状态

---

## 2. 设计目标

本次重构的目标：

### 2.1 统一播放入口
- 保留 `/api/v1/play` 作为唯一播放入口

### 2.2 支持多来源扩展
- 本地音乐
- Jellyfin
- 网络链接
- 插件来源

---

### 2.3 建立统一播放上下文模型
- 所有来源统一输出结构化状态
- WebUI / HA / 插件统一消费

---

### 2.4 统一播放模式语义
- `play_mode` 由框架统一解释
- 禁止来源自行定义播放模式语义

---

### 2.5 统一执行路径（关键约束）

所有播放行为必须：

> 最终进入统一的播放状态机执行路径

禁止：
- URL 直接播放绕过状态机
- 本地/Jellyfin走不同执行链路

---

## 3. 核心概念

### 3.1 Playback Source Adapter（播放源适配器）

每个来源实现统一接口：

- local
- jellyfin
- network
- plugin:xxx

职责：
- 解析请求
- 提供播放事实（不负责策略）

---

### 3.2 Playback Context（播放上下文）

表示当前播放所属的“集合语义”：

例如：
- 歌单
- 专辑
- 搜索结果
- 单曲

---

### 3.3 Active Context（激活上下文）

系统在任一时刻只维护一个上下文：

> 一个设备只能有一个当前激活上下文

---

### 3.4 Playback Kind

定义播放结构类型：

- `single`：单媒体
- `queue`：队列播放
- `stream`：流式播放

---

### 3.5 Play Mode

播放策略，由框架统一定义：

- `one`
- `single`
- `sequence`
- `all`
- `random`

---

## 4. `/api/v1/play` 的职责

### 4.1 统一入口

所有播放请求必须通过：

```http
POST /api/v1/play
````

---

### 4.2 职责范围

该接口负责：

1. 接收播放请求
2. 选择来源适配器
3. 解析播放对象
4. 决定播放上下文
5. 构建统一播放上下文
6. 交给统一状态机执行

---

### 4.3 非职责（禁止行为）

`/api/v1/play` 不负责：

* 直接调用不同播放函数
* 分叉执行路径
* 跳过状态机

---

## 5. 请求模型（context_hint）

### 5.1 结构定义

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

---

### 5.2 规则

* `context_hint` 为可选字段
* 一旦提供：

  * 来源适配器 **必须优先使用**
* 若未提供：

  * 来源适配器自行选择上下文

---

## 6. Playback Kind 定义

### 6.1 single

* 单媒体播放
* 无队列
* 只允许 `play_mode = one`

---

### 6.2 queue

* 存在可枚举队列
* 支持 next / previous
* 有 current_index / queue_length

---

### 6.3 stream

* 持续流媒体
* 不具备传统队列语义

---

## 7. 统一播放上下文结构

最小字段定义：

```json
{
  "source_type": "...",
  "playback_kind": "...",

  "current_track_id": "...",
  "current_track_title": "...",
  "current_track_duration": 0,

  "play_mode": "...",

  "context_type": "...",
  "context_id": "...",
  "context_name": "...",

  "queue_supported": true,
  "current_index": 0,
  "queue_length": 0,

  "has_next": true,
  "has_previous": true
}
```

---

## 8. 职责边界

### 8.1 来源适配器负责提供（事实）

* source_type
* playback_kind
* current_track_*
* context_*
* queue_supported
* queue_length
* current_index

---

### 8.2 框架负责提供（策略）

* play_mode
* has_next
* has_previous
* 播放结束行为

---

## 9. 多上下文处理规则

### 9.1 单一激活上下文

系统保证：

> 同一时刻只存在一个 active context

---

### 9.2 上下文选择优先级

1. 使用 `context_hint`
2. 否则来源适配器自行选择

---

### 9.3 禁止行为

* 同时维护多个队列
* 动态切换上下文而不更新状态

---

## 10. 播放模式约束

### 10.1 single 类型限制

* 仅允许 `one`
* 其他模式必须拒绝或规范化

---

### 10.2 queue 类型

由框架统一解释：

* sequence
* all
* random
* one
* single

---

### 10.3 stream 类型

按降级规则处理（无队列）

---

## 11. 统一执行路径（关键约束）

所有播放行为必须遵循：

```
/api/v1/play
    ↓
source resolver
    ↓
playback resolution
    ↓
构建统一播放上下文
    ↓
进入统一 device_player 状态机
```

---

### 禁止：

* play_url 直接调用
* 本地/Jellyfin 各自执行播放
* 绕过状态机

---

## 12. 与现有接口关系

### 12.1 `/api/v1/play`

* 保留
* 重定义为唯一播放入口

---

### 12.2 `/api/v1/playlist/*`

* 后续重构
* 不再作为独立播放入口
* 应转为“上下文构建接口”

---

### 12.3 旧播放路径

* 标记为 deprecated
* 逐步移除

---

## 13. 未来扩展方向

* 插件化播放源
* 多设备同步上下文
* HA 深度集成
* 播放队列持久化

