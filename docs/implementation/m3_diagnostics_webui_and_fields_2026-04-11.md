# M3 Diagnostics WebUI Consumption & Field Notes (2026-04-11)

## 1. 范围与结论

本文基于当前仓库代码事实，回答三件事：

1. WebUI 当前是否消费统一诊断视图 `/diagnostics`
2. 旧 `/diagnostics` 返回结构是否仍有仓库内调用方依赖
3. 当前统一诊断视图各字段的说明与使用边界

结论先行：

- **WebUI 当前未消费 `/diagnostics`**，只消费 `/api/auth/status`
- **仓库内未发现 `/diagnostics` 的前端或业务调用方**；兼容风险主要在仓库外部调用者
- **按 roadmap，M3 目标包含“WebUI 可展示最小可用诊断页面”**，因此当前后端统一视图虽已落地，但 WebUI 消费链路尚未完成 M3 全量收口

---

## 2. WebUI 消费现状

### 2.1 `/diagnostics`

仓库内搜索结果：

- `xiaomusic/webui/src` 中未检出 `/diagnostics` 调用
- `xiaomusic/webui/src/services/v1Api.ts` 仅封装 `/api/v1/*` 接口，不包含 `/diagnostics`
- 文档中 roadmap 明确 M3 的 WebUI 方向应新增轻量诊断页，但当前 WebUI 代码尚未接入

代码依据：

- `xiaomusic/webui/src/services/v1Api.ts`
  - 文件顶部注明：`Public API only: this module only wraps /api/v1/* interfaces.`
  - 无 `/diagnostics` 封装
- 全仓库 grep：`xiaomusic/webui/src` 下无 `/diagnostics` 调用

结论：

- **当前 WebUI 不消费统一诊断视图**
- **M3 若要按 roadmap 完整验收，仍需补一个最小诊断页或等价消费入口**

### 2.2 `/api/auth/status`

WebUI 当前明确消费 `/api/auth/status`。

代码依据：

- `xiaomusic/webui/src/services/auth.ts`
  - `fetchAuthStatus()` → `apiGet<AuthStatus>("/api/auth/status")`
- `xiaomusic/webui/src/components/AuthStatusCard.tsx`
  - 页面加载调用 `fetchAuthStatus()`
  - 消费字段包括：
    - `auth_locked`
    - `runtime_auth_ready`
    - `token_valid`
    - `token_exists`
    - `cloud_available`
    - `short_session_available`
    - `persistent_auth_available`
    - `status_reason`
    - `status_reason_detail`
    - `rebuild_failed`
    - `rebuild_error_code`
    - `rebuild_failed_reason`
    - `login_in_progress`
    - `auth_lock_until`
    - `auth_lock_reason`
    - `last_error`
- `xiaomusic/webui/src/pages/HomePage.tsx`
  - `loadAuthStatus()` 调用 `fetchAuthStatus()`
  - 设置页打开时每 2500ms 刷新一次 auth 状态
  - 用于二维码登录状态、登录/待恢复提示、设备加载触发等

结论：

- **WebUI 当前已消费 auth startup 健康摘要，但这是 `/api/auth/status`，不是新的统一 `/diagnostics` 视图**
- **这说明 WebUI 已接入 M3 的一部分（auth 摘要），但没有接入完整 startup/self-check 统一视图**

---

## 3. Roadmap 边界判断

代码外依据来自 roadmap：

### 3.1 M3 WebUI 目标

`D:\AI\临时输出\v1.1.0_roadmap.md`

M3 章节明确写到：

- `重点不是接口数量,而是结构稳定、便于 WebUI 与用户消费。`
- `新增轻量诊断页,优先展示 startup / self-check 相关内容`
- `WebUI 可展示最小可用诊断页面`

### 3.2 总体 WebUI 原则

同一 roadmap 的 9.4 节：

- `WebUI 在本版本中不是独立大改对象,而是随核心模型一起完成消费层收口。`

结论：

- **WebUI 补充 `/diagnostics` 的最小消费页，属于 M3 scope 内工作**
- 但它应是“轻量消费层收口”，不应演变成重型诊断前端平台

---

## 4. `/diagnostics` 兼容影响分析

### 4.1 当前变化事实

`/diagnostics` 已从旧结构：

```json
{
  "startup": {...},
  "keyword_override_mode": "...",
  "keyword_conflicts": [],
  "last_download_result": ...
}
```

改为统一结构：

```json
{
  "generated_at_ms": 0,
  "overall_status": "ok|degraded|failed|unknown",
  "summary": "...",
  "areas": {
    "startup": {...},
    "auth": {...},
    "sources": {...},
    "devices": {...},
    "playback_readiness": {...}
  }
}
```

### 4.2 仓库内调用方检查结果

仓库内检索结果：

- 未发现 WebUI 调用 `/diagnostics`
- 未发现生产代码中其它模块直接消费 `/diagnostics`
- 当前与 `/diagnostics` 直接相关的仓库内引用主要是：
  - `xiaomusic/api/routers/system.py`
  - `tests/test_system_diagnostics.py`

结论：

- **仓库内未发现旧 `/diagnostics` 结构的直接消费方**
- **因此仓库内暂无已知未处理兼容风险**

### 4.3 外部兼容风险

仍需明确一个边界：

- `/diagnostics` 是已有路径
- 其返回结构已发生非兼容变化
- 虽然仓库内无调用方，但**仓库外脚本、运维面板、用户自建集成**可能依赖旧字段

因此当前兼容风险判断应表述为：

- **仓库内：未发现未处理风险**
- **仓库外：存在潜在破坏性变更风险，需要在文档或 changelog 中明确说明**

### 4.4 兼容建议

建议二选一：

#### 方案 A：接受结构升级，文档显式说明破坏性变化

适用条件：
- `/diagnostics` 尚未形成广泛外部依赖
- 当前更重视 M3 收口而非保留旧形状

建议动作：
- 在 docs/changelog 中明确：`/diagnostics` 已升级为统一 startup/self-check 视图
- 标注旧顶层字段不再直接返回

#### 方案 B：短期加兼容层

适用条件：
- 需要降低外部调用方迁移成本

可选兼容方式：
- 在新结构下额外挂一层 deprecated 顶层字段：
  - `startup`
  - `keyword_override_mode`
  - `keyword_conflicts`
  - `last_download_result`
- 或新增旧结构兼容端点，而 `/diagnostics` 保持新结构

当前仓库事实下，**优先建议方案 A**。理由：
- 仓库内未发现旧调用方
- M3 当前目标是建立统一诊断视图
- 继续保留旧形状会削弱统一收口效果

---

## 5. `/diagnostics` 字段说明

以下说明基于当前实现：

- `xiaomusic/diagnostics.py::build_runtime_diagnostics_view()`
- `xiaomusic/api/routers/system.py::diagnostics()`

### 5.1 顶层字段

#### `generated_at_ms`
- 类型：`number`
- 含义：本次统一诊断视图生成时间（毫秒时间戳）
- 用途：前端展示“数据更新时间”，或判断诊断结果是否过旧

#### `overall_status`
- 类型：`string`
- 可选值：`ok | degraded | failed | unknown`
- 含义：统一诊断视图的总体状态
- 聚合规则：
  - `startup/auth` 任一为 `failed` → `overall_status = failed`
  - 否则只要任一 area 为 `degraded` → `degraded`
  - 所有 area 都为 `ok` → `ok`
  - 其余情况 → `unknown`

#### `summary`
- 类型：`string`
- 含义：面向用户/界面的总体摘要
- 用途：列表页、诊断首页、顶部摘要提示

#### `areas`
- 类型：`object`
- 含义：各诊断维度的具体结果集合
- 当前固定包含：
  - `startup`
  - `auth`
  - `sources`
  - `devices`
  - `playback_readiness`

---

## 6. Area 通用字段说明

每个 area 当前都遵循统一形状：

```json
{
  "status": "ok|degraded|failed|unknown",
  "summary": "...",
  "last_failure": "...",
  "data": { ... }
}
```

### `status`
- 类型：`string`
- 可选值：`ok | degraded | failed | unknown`
- 含义：该维度的健康结论

### `summary`
- 类型：`string`
- 含义：该维度的一句话摘要

### `last_failure`
- 类型：`string`
- 含义：最近一次失败或最主要失败摘要
- 为空字符串表示当前无明确失败摘要

### `data`
- 类型：`object`
- 含义：该维度的结构化细节数据
- 注意：这是正式摘要数据，不等于 debug drill-down 全量信息

---

## 7. `areas.startup`

### 含义
startup/self-check 的统一摘要，聚合：
- 启动自检结果
- keyword conflict 摘要
- 最近下载结果

### 字段

#### `areas.startup.data.ok`
- 类型：`boolean | null`
- 含义：startup 自检整体是否通过

#### `areas.startup.data.checked_at`
- 类型：`number | null`
- 含义：startup 自检执行时间（秒级时间戳）

#### `areas.startup.data.notes`
- 类型：`string[]`
- 含义：启动自检附带说明或建议

#### `areas.startup.data.keyword_override_mode`
- 类型：`string`
- 含义：关键词冲突处理模式

#### `areas.startup.data.keyword_conflicts`
- 类型：`string[]`
- 含义：已识别的关键词冲突列表

#### `areas.startup.data.last_download_result`
- 类型：`object | null`
- 含义：最近下载结果摘要

### 使用边界
- 面向 startup 健康展示
- 不等于完整启动日志

---

## 8. `areas.auth`

### 含义
认证启动健康摘要。其数据来自当前 `/api/auth/status` 的聚合结果，而不是直接暴露 debug state。

### 字段

#### `areas.auth.data.runtime_auth_ready`
- 类型：`boolean | null`
- 含义：运行时认证是否已就绪

#### `areas.auth.data.persistent_auth_available`
- 类型：`boolean | null`
- 含义：长期认证字段是否齐全

#### `areas.auth.data.short_session_available`
- 类型：`boolean | null`
- 含义：短期 session 是否存在

#### `areas.auth.data.auth_mode`
- 类型：`string`
- 含义：当前 auth manager 模式，如 `healthy/degraded/locked`

#### `areas.auth.data.auth_locked`
- 类型：`boolean | null`
- 含义：认证是否处于锁定态

#### `areas.auth.data.status_reason`
- 类型：`string`
- 含义：业务结论原因码
- 当前可能值至少包括：
  - `healthy`
  - `persistent_auth_missing`
  - `short_session_missing`
  - `short_session_rebuild_failed`
  - `runtime_not_ready`
  - `manual_login_required`
  - `temporarily_locked`

#### `areas.auth.data.status_reason_detail`
- 类型：`string`
- 含义：最近错误或状态细节

### 使用边界
- 用于“认证是否可用”的正式摘要展示
- 深层恢复细节仍应查看：
  - `/api/v1/debug/auth_state`
  - `/api/v1/debug/auth_recovery_state`
  - `/api/v1/debug/auth_runtime_reload_state`
  - `/api/v1/debug/auth_short_session_rebuild_state`
  - `/api/v1/debug/miaccount_login_trace`

---

## 9. `areas.sources`

### 含义
内置来源的静态就绪摘要，不是运行期来源监控平台。

### 字段

#### `areas.sources.data.ready_count`
- 类型：`number`
- 含义：状态为 `ok` 的来源数量

#### `areas.sources.data.degraded_count`
- 类型：`number`
- 含义：状态为 `degraded` 的来源数量

#### `areas.sources.data.failed_count`
- 类型：`number`
- 含义：状态为 `failed` 的来源数量

#### `areas.sources.data.unknown_count`
- 类型：`number`
- 含义：状态为 `unknown` 的来源数量

#### `areas.sources.data.items`
- 类型：`array`
- 含义：各来源条目列表

每个 item 当前形状：

```json
{
  "source": "local_library|jellyfin|direct_url|site_media",
  "status": "ok|degraded|failed|unknown",
  "summary": "...",
  "last_failure": "..."
}
```

### 当前判定逻辑
- `local_library`
  - 看 `music_path` 可读性
  - 看 `music_library.all_music` 是否已初始化
- `jellyfin`
  - 看 `jellyfin_enabled/jellyfin_base_url/jellyfin_api_key`
- `direct_url`
  - 固定视为 `ok`
- `site_media`
  - 看 `online_music_service/js_plugin_manager` 是否初始化

### 使用边界
- 只回答“来源是否基本就绪”
- 不回答最近 N 次错误、成功率、运行期耗时

---

## 10. `areas.devices`

### 含义
设备 startup 可达性摘要，基于已知设备列表与最近一次 reachability 缓存；不会主动全量 probe。

### 字段

#### `areas.devices.data.total`
- 类型：`number`
- 含义：已知设备总数

#### `areas.devices.data.reachable`
- 类型：`number`
- 含义：最近 reachability 结果显示可达的设备数量

#### `areas.devices.data.unreachable`
- 类型：`number`
- 含义：最近 reachability 结果显示不可达的设备数量

#### `areas.devices.data.unknown`
- 类型：`number`
- 含义：没有 probe 缓存的设备数量

#### `areas.devices.data.items`
- 类型：`array`
- 含义：各设备摘要条目

每个 item 当前形状：

```json
{
  "device_id": "...",
  "name": "...",
  "status": "ok|failed|unknown",
  "summary": "...",
  "last_failure": "...",
  "reachability": {
    "ip": "...",
    "local_reachable": true,
    "cloud_reachable": false,
    "last_probe_ts": 0
  }
}
```

### 使用边界
- 这是“最近 reachability 摘要”，不是主动巡检系统
- 若从未 probe，则允许 `unknown`

---

## 11. `areas.playback_readiness`

### 含义
播放链 readiness 预留位。当前版本按 M3 scope 保持保守实现。

### 当前字段

#### `areas.playback_readiness.data.can_resolve_source`
- 类型：`boolean | null`
- 当前值：`null`

#### `areas.playback_readiness.data.can_dispatch_transport`
- 类型：`boolean | null`
- 当前值：`null`

#### `areas.playback_readiness.data.requires_auth`
- 类型：`boolean | null`
- 当前值：`null`

#### `areas.playback_readiness.data.notes`
- 类型：`string[]`

### 使用边界
- 当前仅表示“该维度已预留，正式检查尚未实现”
- 不应被前端解释为“播放链已健康”

---

## 12. 使用建议

### 12.1 适合 WebUI 首页/诊断页展示的字段

建议优先展示：

- 顶层：
  - `overall_status`
  - `summary`
  - `generated_at_ms`
- area 摘要：
  - `areas.*.status`
  - `areas.*.summary`
  - `areas.*.last_failure`
- 细项统计：
  - `areas.sources.data.ready_count/failed_count/unknown_count`
  - `areas.devices.data.reachable/unreachable/unknown`

### 12.2 不建议直接作为用户主界面长期堆满展示的字段

- `areas.auth.data.status_reason_detail`
- `areas.devices.data.items[*].reachability.ip`
- 全量 item 列表

这些更适合诊断页二级展开。

---

## 13. 当前结论

基于当前仓库事实：

1. **WebUI 仅消费 `/api/auth/status`，尚未消费统一 `/diagnostics` 视图**
2. **按 roadmap，补一个最小诊断页属于 M3 scope 内工作**
3. **仓库内未发现旧 `/diagnostics` 返回结构的调用方**
4. **外部调用方仍存在潜在兼容风险，建议通过文档/changelog 显式说明**
5. **当前字段文档已足够支持后续补 WebUI 轻量诊断页**
