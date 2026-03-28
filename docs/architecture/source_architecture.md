# Source 架构（Source Architecture）

版本：v1.0
状态：正式架构文档
最后更新：2026-03-28

本文档定义 source 边界的职责、当前来源类型、与 playback / transport 的关系，以及插件在 source 体系中的位置。字段级规范见 `docs/spec/runtime_specification.md`。

---

## 1. Source 的统一定义

**source** 是"媒体来源解析层"。其职责是：给定一个播放请求（`MediaRequest`），解析并返回一个可播放的媒体流描述（`ResolvedMedia`）。

source 提供**事实**，不做**策略决策**：

- source 提供：流 URL、标题、来源标识、是否直播、是否需要代理
- source 不决定：是否使用代理、投递方式、队列位置、播放模式

所有 source 实现必须遵守统一接口：`SourcePlugin.resolve(request: MediaRequest) -> ResolvedMedia`。

---

## 2. 当前四种正式 source 类型

| 来源标识 | 职责 | 代码位置 |
|---|---|---|
| `direct_url` | 解析 HTTP/HTTPS 直链，不做任何转换 | `adapters/sources/direct_url_source_plugin.py` |
| `site_media` | 解析站点媒体（YouTube、Bilibili 等），通过 yt-dlp 或 MusicFree JS 插件提取流 URL | `adapters/sources/site_media_source_plugin.py` |
| `jellyfin` | 解析 Jellyfin 资源，通过 Jellyfin API 获取流 URL | `adapters/sources/jellyfin_source_plugin.py` |
| `local_library` | 解析本地媒体库中的曲目，返回本地文件或内置 HTTP 服务 URL | `adapters/sources/local_library_source_plugin.py` |

这四种类型是当前正式 source 类型的完整集合。新增 source 类型必须：

1. 实现 `SourcePlugin` 接口
2. 在 `default_registry.py` 中注册
3. 在 `api_v1_spec.md` 的 `source_hint` 允许值中声明

`source_hint = "auto"` 时，由 `SourceRegistry` 按注册顺序依次尝试匹配。

---

## 3. Source 与 Playback 的边界

```
播放请求 (MediaRequest)
    ↓
[source 边界]
    SourceRegistry.get_plugin()  ← 选择匹配的插件
    SourcePlugin.resolve()       ← 解析，返回 ResolvedMedia
    ↓
[playback 边界]
    DeliveryAdapter.prepare_plan()  ← 决定投递方式（直连/代理/relay）
    TransportRouter.dispatch()      ← 下发到设备
```

**source 不得越过此边界：**

- source 不得访问 `DeviceRegistry` 或任何设备对象
- source 不得做"用代理还是直连"的投递策略决定（`prefer_proxy` 是建议，不是命令）
- source 不得修改播放队列或上下文
- source 不得触发任何播放状态变更

**playback 对 source 的约束：**

- playback 调用 source 的唯一入口是 `SourcePlugin.resolve()`
- playback 不得绕过 source 层直接拼装流 URL

---

## 4. Source 与 Transport 的关系

source 的输出（`ResolvedMedia`）经过 `DeliveryAdapter` 处理后，才能进入 transport 层。source 不直接与 transport 交互。

```
ResolvedMedia (source 输出)
    → DeliveryAdapter → PreparedStream / DeliveryPlan
    → TransportRouter → mina / miio transport
```

source 提供的 `stream_url` 必须是 `http://` 或 `https://` 格式。非法 URL 会在 `DeliveryAdapter` 处被拒绝并抛出 `UndeliverableStreamError`，不会传入 transport 层。

---

## 5. 插件在 Source 体系中的位置

系统中存在两类插件概念，必须明确区分：

### 5.1 Source Plugin（来源插件，正式体系）

- 接口：`xiaomusic/core/source/source_plugin.py` 中的 `SourcePlugin` 抽象类
- 注册：`SourceRegistry`，通过 `default_registry.py` 注册内置四种
- 职责：实现 `resolve()` 方法，返回 `ResolvedMedia`
- 这是 source 架构的正式扩展点

### 5.2 MusicFree JS 插件（外部媒体提供方，属于 site_media source 的内部机制）

- 管理：`xiaomusic/managers/js_plugin_manager.py`
- 职责：在 `site_media` source 插件内部，调用第三方 MusicFree JS 脚本解析特定站点
- **这不是 source 体系的扩展点**，是 `site_media` source 的内部实现细节

### 5.3 Python exec 插件（命令扩展，不属于 source 体系）

- 管理：`xiaomusic/plugin.py`
- 职责：扩展自然语言命令处理，不涉及媒体来源解析
- **不属于 source 体系**

---

## 6. Source 不允许承担的职责

以下职责明确不属于 source 边界，不得在 source 实现中出现：

- 播放模式决策（随机、循环等）
- 队列管理（上一首、下一首计算）
- 设备状态读取
- 播放上下文（context）的创建或修改
- revision 生成或 play_session_id 分配
- relay session 的创建
- 对 transport 层的直接调用

---

## 7. 新增 Source 的约束

新增 source 类型时，必须满足：

1. 继承 `SourcePlugin` 抽象类，实现 `name` 属性和 `resolve()` 方法
2. `resolve()` 只返回 `ResolvedMedia`，不产生副作用
3. 在 `default_registry.py` 中以确定性顺序注册
4. 在 `api_v1_spec.md` 的 `source_hint` 允许值列表中声明
5. 来源标识（`source` 字段）必须属于四种正式类型之一，或在 API 契约中新增声明

---

## 8. 相关文档

| 文档 | 职责 |
|---|---|
| `docs/spec/runtime_specification.md` | `MediaRequest`、`ResolvedMedia` 等数据模型的字段定义 |
| `docs/architecture/system_overview.md` | source 在系统九个一级边界中的位置 |
| `docs/api/api_v1_spec.md` | `source_hint` 允许值、`POST /api/v1/play` 契约 |
| `docs/architecture/unified_playback_model.md` | 播放上下文、队列、播放模式的概念定义 |
