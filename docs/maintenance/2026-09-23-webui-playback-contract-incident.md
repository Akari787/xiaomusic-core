# WebUI 播放契约故障记录与方案 A 实施记录

> 状态：**已完成 / 测试服务器与设备实播验收通过**
> 记录日期：2026-09-23
> 最后更新：2026-09-28
> 适用场景：修复主页“播放测试”和在线搜索播放，并建立最小前后端契约门禁
> 方案：Akari 已确认方案 A；Manager 已批准窄范围 `online_plugin` SourcePlugin 与进程内 TTL 引用存储。
> 当前结论：代码、CI、测试服务器契约、真实 WebUI 搜索入口与设备实播均已闭合；最终原始设备状态确认 `status=2`。

---

## 实施记录（2026-09-24）

- 已删除主页 `prefer_codec`、`search_key` 请求构造，新增 typed `PlayOptions` 与集中 builder。
- 已增加普通 / 代理链接 payload、在线 opaque 引用 payload、严格未知字段、真实 FastAPI router + exception handler 的回归测试；CI 增加 Python 定点测试、全部 WebUI 测试和 production build 门禁。
- 主页播放错误提示现在包含稳定 `error_code`、`stage`、`request_id`，且请求阶段明确显示“请求契约不匹配”。
- 已新增单一内置 `OnlinePluginSourcePlugin`。在线搜索仅为包含 `platform` 与稳定媒体身份的 raw item 创建高熵 `opq_` token；原始 item 只存进程内、有 TTL、有容量上限的并发安全存储，WebUI 只收到 token、`online_plugin`、media_id 和 title。
- 已验证在线插件通过现有 `OnlineMusicService.get_media_source_url()` 获取真实媒体流并构造 `ResolvedMedia`；不存在、过期和上游失败均返回脱敏稳定错误。
- OpenAPI/Jellyfin 或缺少 `platform` / 稳定身份的结果不生成引用，搜索面板显示“暂无正式播放引用”并禁用确认；禁止标题回退。
- 共享 JSON fixture 作为 TS builder 与 Python router schema 的单一样本；README Roadmap 仍不恢复完成，因为未进行测试服务器/真实入口验收。
- 未增加 `online_plugin` delivery proxy fallback：当前 `DeliveryAdapter` 仅按 source 集合决定 fallback，虽将 `ResolvedMedia.headers` 带入 `PreparedStream`，Mina/relay 投递路径并未证明能以这些 headers 发起代理请求；猜测性加入 fallback 不能解决已证实问题，留待独立 delivery 设计。

## 实施与测试服务器验收记录（2026-09-28）

- 修复 hardened 容器 `read_only` rootfs 下 JS plugin 搜索被 `EROFS` 阻断的问题：删除 runner 对原始搜索结果写入 `00-plugin_debug.log` 的行为及无其他用途的 `fs` 依赖；保留搜索校验、platform 补全、sandbox、网络和其他日志语义。
- Node 回归测试使用内联离线 fixture，验证插件 load/search、platform 补全、只读风格工作目录以及不创建调试文件，并以源码断言防止敏感原始结果落盘回归；按发布 CI 的单测试文件挂载方式在只读容器中 `11/11` 通过。
- 部署前创建可回滚备份 `backups/smoke-backup-20260928_072637.tar.gz`，源码包本地与服务器 SHA-256 一致；测试容器重建后健康，内置 source 列表包含 `online_plugin`。
- 新 YouTube URL `jifImKwD1mw` 在测试服务器解析成功：`source_plugin=site_media`、`is_live=false`、取得音频流。旧 `prefer_codec` payload 返回结构化 HTTP 422；普通与代理新 payload 均通过 schema，并在假设备处返回预期 `E_DEVICE_NOT_FOUND`。
- 使用临时 MusicFree fixture plugin 验证真实 `search → opq_ token → online_plugin resolve` 链路：搜索响应不泄漏原始 URL，两次解析均成功，未知 token 返回 `E_RESOLVE_NONZERO_EXIT/resolve`。浏览器中的搜索结果、无引用标记、确认按钮禁用状态均符合设计。
- 临时插件已卸载，服务器恢复为无启用 JS plugin；日志中没有新的 `EROFS` 或 `00-plugin_debug.log` 错误。
- 容器重启后小米认证曾进入 `manual_login_required`，上游返回 70016；Akari 完成人工登录后运行时恢复 `healthy`，本工作包未修改 auth。
- 使用 `jifImKwD1mw` 完成真实设备验收：`POST /api/v1/play` 返回 `code=0`、`source_plugin=site_media`、`transport=mina`；播放中 probe 原始 `status=1`，权威状态为 `playing/is_playing=true`。随后 stop 成功，最终 probe 原始 `status=2`，权威状态为 `stopped/is_playing=false`，音量保持 27。

## 1. 问题

主页存在至少两条真实用户路径，其请求体与后端正式契约不一致。前端测试和后端测试分别通过，但组合后的生产请求在进入业务逻辑前返回 HTTP 422。

这不是 YouTube 解析器整体失效。测试服务器已成功解析普通 YouTube 视频和有效直播；本次主页问题发生在 API 请求校验阶段。

### 1.1 已证实故障

| 用户路径 | 前端请求 | 后端契约 | 现场结果 |
|---|---|---|---|
| 主页 → 测试 → 播放链接 / 代理播放 | `options.prefer_codec = "auto"` | `PlayOptionsModel(extra="forbid")` 中无 `prefer_codec` | 测试服务器 HTTP 422 |
| 主页 → 在线搜索 → 选择结果 → 确定 | `options.search_key = <keyword>` | `PlayOptionsModel(extra="forbid")` 中无 `search_key` | 测试服务器 HTTP 422 |

代码位置：

- `xiaomusic/webui/src/pages/HomePage.tsx`
  - `playLink()` 发送 `prefer_codec`
  - `confirmSearch()` 发送 `search_key`
- `xiaomusic/webui/src/services/v1Api.ts`
  - `PlayRequest.options` 当前为 `Record<string, unknown>`，TypeScript 无法发现非法字段
- `xiaomusic/api/models/play_request.py`
  - `PlayOptionsModel` 拒绝所有未声明字段

### 1.2 在线搜索播放还有第二层契约缺口

`GET /api/v1/search/online` 当前只向 WebUI 返回：

- `name`
- `title`
- `artist`

它没有返回可稳定播放所需的来源身份，例如：

- `source_hint`
- 稳定 `media_id`
- 来源插件标识
- 受约束的 `source_payload` 或其他正式播放引用

因此，即使简单删除 `search_key` 使请求不再 422，主页仍只能拿标题调用 `/api/v1/play`，无法保证播放的是用户选中的在线搜索结果。只删除字段会把“明确失败”变成“请求被接受但语义不可靠”，不构成完整修复。

### 1.3 测试为何没有拦住

- WebUI 测试 mock `v1Api.play()`，mock 不校验真实后端 schema。
- 后端测试证明“未知字段应被拒绝”，但没有将主页真实 payload 提交到完整 FastAPI app。
- 前端 `options?: Record<string, unknown>` 放弃了编译期字段约束。
- 当前发布 CI 只构建镜像、验证 Python import 和 Node 安全测试，不运行 Python 功能测试或 WebUI 测试。
- 发布记录仍保留 `106 failed` 的历史测试基线，整体测试结果存在较高噪声。

### 1.4 与本次问题无关但需要区分的现场

以下 URL：

```text
https://www.youtube.com/watch?v=28KRPhVzCus
```

当前被 yt-dlp 判定为：

```text
This live stream recording is not available.
```

这是特定上游直播 ID 已失效或录像不可用，不是主页 422 的根因，不纳入本次代码修复。

---

## 2. 根因

### 2.1 直接原因

2026-07-30 后端将 `options` 从任意字典收紧为 `PlayOptionsModel(extra="forbid")`，但没有迁移或验证所有 WebUI 消费者。主页从更早版本保留下来的 `prefer_codec` 与 `search_key` 因而成为非法字段。

### 2.2 系统性原因

1. API、Core 和 WebUI 各自维护播放参数结构，没有单一可执行契约。
2. 模块已拆分，消费者同步和跨层测试没有同步建立。
3. 功能测试偏向局部 mock，没有覆盖真实用户入口到生产 app 的组合路径。
4. 变更批次长期过宽，契约收紧、路径治理、状态重构和发布工作互相夹带。
5. CI 与验收没有形成足够硬的发布阻断条件。

### 2.3 架构判断

统一播放入口、严格 schema、Source/Transport 分层和权威状态源等方向不应回滚。需要修正的是边界契约的执行方式和验收方式。

---

## 3. Roadmap 对照与范围筛选

Roadmap 权威入口：`README.md` 第 5 节。

| Roadmap 项 | 与本次关系 | 本计划处理方式 |
|---|---|---|
| WebUI 主流程围绕 v1 正式控制面持续收敛（当前标记完成） | 两条主页真实路径仍违反 v1 契约，完成状态需要重新核验 | **纳入本次**：修复并通过真实契约验收后，再确认该项是否仍可标记完成 |
| 核心能力与来源扩展边界继续收敛 | 在线搜索结果缺少正式播放引用，属于来源边界渗漏 | **仅纳入窄切片**：定义“搜索结果 → 播放请求”所需的最小正式契约；不重构整个 Source/Runtime |
| 来源能力整理为更清晰的插件范式 | 搜索结果应携带来源身份，但插件体系整体治理范围过大 | **只记录后续**：本次仅补可播放引用，不做插件管理器或能力声明大改 |
| 播放稳定性与认证恢复可观测性继续加强 | 主页失败提示缺少足够的请求关联信息；SourceResolveError 过度压缩上游原因 | **纳入最小可观测性**：失败提示保留 `request_id`、阶段和稳定错误码；不改认证状态机，不直接暴露 yt-dlp 原始 stderr |
| 自托管部署体验与安全默认配置持续打磨 | 与当前 WebUI 播放契约没有共享修改面 | **明确排除** |

其他历史跟进项：

- `disabled_plugins` 持久化：排除。
- auth P2（并发、TTL、87001/70016 分类等）：排除，另立工作包。
- 全量插件范式和 Source/Runtime 防腐层整改：排除，另立工作包。
- `play_id/session_id` 全链路：排除；本次只利用现有 `request_id`。

---

## 4. 方案 A 实施计划

### 步骤 0：冻结边界

在 Akari 确认前：

- 不修改业务代码；
- 不构建候选镜像；
- 不部署测试服务器；
- 不顺手修改 auth、relay、transport、设备状态机或插件管理。

### 步骤 1：先建立失败测试

新增对旧代码必失败的测试，至少覆盖：

1. `playLink(false)` 生成的真实请求体能被 `PlayRequest` 接受；
2. `playLink(true)` 的 `prefer_proxy=true` 请求能被接受；
3. `confirmSearch()` 不再发送未声明字段；
4. 在线搜索结果包含足够信息，可构造确定的正式播放请求；
5. 未声明 options 字段仍应返回结构化 HTTP 422，不能为了兼容旧前端而放宽 `extra="forbid"`。

> 注意：不得把后端改回任意字典，也不得只修改 mock 使测试变绿。

### 步骤 2：恢复主页播放测试

建议的最小实现：

1. 删除无实现依据的 `prefer_codec`；
2. 在前端声明与后端一致的 `PlayOptions` 类型，替换 `Record<string, unknown>`；
3. 将主页请求构造从 `HomePage.tsx` 抽到可单测的 typed builder/service；
4. 保留 `no_cache`、`prefer_proxy` 等正式字段；
5. 普通链接、移动端 YouTube 链接及带 Mix 参数的链接均继续由 `source_hint="auto"` 进入统一播放入口。

### 步骤 3：闭合在线搜索到播放的正式契约

实际实现：

```text
MusicFree raw item（platform + 稳定身份）
  → OnlinePluginReferenceStore.put() → 高熵 opq_ token
  → WebUI: query + source_hint=online_plugin + media_id/title
  → POST /api/v1/play
  → OnlinePluginSourcePlugin.get(token)
  → OnlineMusicService.get_media_source_url(raw item)
  → ResolvedMedia
```

约束：

- WebUI 不接收 raw item、媒体 URL、cookie、token、source_payload 或 context_hint。
- token 只在进程内存中存在，TTL、容量上限、并发安全；在同一 TTL 内允许播放链路安全重试。
- 只有服务端判定具备 `platform`、稳定身份且属于当前启用 MusicFree JS plugin 集合的结果生成引用；`OpenAPI-*`、`Jellyfin` 和未知 platform 明确不可播放。
- 不信任上游 raw item 的 `play_reference`，也不允许标题、搜索词回退。

### 步骤 4：补最小可观测性

同一修复包内仅做以下窄改动：

- 主页失败提示显示稳定 `error_code`、`stage` 和 `request_id`；
- HTTP 422 明确提示“请求契约不匹配”，避免误判为媒体解析失败；
- Source 解析失败继续返回脱敏稳定错误码，不把完整媒体 URL、cookie、token 或 yt-dlp stderr 暴露给 WebUI。

不在本次实现：

- 新增完整 `play_id/session_id` 追踪体系；
- 重写 resolver 错误体系；
- 记录 ffmpeg 全量 stderr；
- 修改认证诊断。

### 步骤 5：加入发布门禁

最小门禁：

1. WebUI 对主页“播放链接”“代理播放”“在线搜索后播放”三条路径做 payload 断言；
2. 将这些真实 payload 提交到完整 FastAPI app，证明不会因 schema 返回 422；
3. 后端严格字段测试继续存在；
4. WebUI production build 通过；
5. CI 至少运行本次相关 Python 测试、WebUI 测试与 build，而不是只验证 import；
6. 全量测试结果单独记录，本次不得新增失败。

历史 `106 failed` 不要求在本修复包内一次清零，但必须另建分域台账；不能继续以一个总数字长期豁免。

### 步骤 6：测试服务器与用户入口验收

#### 无声、无设备风险的契约验收

- 使用不存在的测试 device ID 提交主页真实 payload；
- 预期进入业务层并返回 `E_DEVICE_NOT_FOUND`，而非 HTTP 422；
- 普通 YouTube 移动端链接通过 `/api/v1/resolve`；
- 无效直播 ID 返回 Source resolve 类错误，不能被误报为请求契约错误。

#### 实机验收

只在满足安全条件时执行：

- 夜间或 Akari 睡觉期间，物理音量必须设置并回读为 0；否则立即 stop，不播放；
- 当前 OH2P 的物理最低回读为 4，因此夜间实机播放验收默认阻断；
- 日间验收或 Akari 另行明确授权后，按当时确认的安全音量执行；
- 验收结束必须 stop，并主动 probe 确认原始设备 `status=2`。

用户入口至少覆盖：

1. 主页“播放链接”；
2. 主页“代理播放”；
3. 在线搜索、选择结果并播放；
4. 失败信息包含可关联的 request_id；
5. stop 后无复播。

### 步骤 7：Roadmap 与文档收口

已完成代码级文档收口：

- API spec 描述 `online_plugin`、opaque token、TTL 存储与脱敏边界；
- WebUI 架构文档描述 typed options、builder 和不可播放结果行为；
- ADR-0006 已在实现与定点验证通过后改为 Accepted；
- README Roadmap 在设备实播、最终 stop 与原始 `status=2` 验收通过后恢复完成；
- 发布说明不得把代码级测试扩大成服务器或实机稳定性结论。

---

## 5. 验收标准

全部满足后才能判定修复完成：

- [x] `prefer_codec` 不再出现在主页正式播放请求中；
- [x] `search_key` 不再作为未声明 options 字段发送；
- [x] `PlayRequest.options` 在前端为明确类型，不再是 `Record<string, unknown>`；
- [x] 播放测试和在线搜索播放均有对旧代码必失败的回归测试；
- [x] 前端真实 payload 通过真实 FastAPI router 与 exception handler 校验；
- [x] 未声明字段仍被严格拒绝；
- [x] 在线搜索结果具有确定、正式、可测试的 opaque token 播放语义；
- [x] 失败提示包含稳定错误码、阶段和 request_id；
- [x] WebUI 测试、build 与相关 Python 测试进入 CI 门禁；
- [x] 测试服务器契约与真实 WebUI 搜索入口验收通过；
- [x] 实机播放成功，完成 stop 且 probe 原始 `status=2`；
- [x] 没有夹带 auth、relay、transport、部署安全或插件体系重构；
- [x] API/WebUI/ADR 与 README Roadmap 的当前状态按真实实现和阻断同步更新。

---

## 6. 风险与回滚

| 风险 | 控制方式 |
|---|---|
| 简单删除 `search_key` 后选中结果仍无法确定播放 | 将搜索结果播放引用作为本次必要契约，不以“请求不再 422”冒充完成 |
| 为兼容旧前端而放宽后端 schema | 明确禁止；保留 `extra="forbid"` |
| 借机重构插件体系或播放运行时 | 通过“不做事项”和变更文件白名单约束 |
| 测试再次只覆盖 mock | 强制真实 payload → 完整 FastAPI app 契约测试 |
| 实机验收产生声音 | 遵守音量回读与 stop/status=2 安全规则 |
| 新 CI 被历史失败淹没 | 先设置本次定向硬门禁，历史失败另建分域台账，不混为一项 |
| hardened rootfs 阻断 JS plugin 搜索并可能泄漏原始 URL/token | 禁止原始搜索结果文件落盘；runner 在只读风格工作目录下由离线 fixture 回归测试验证 |

回滚原则：

- 修复采用独立、可回退提交；
- 部署前保留当前 v1.1.7 镜像与源码坐标；
- WebUI 与后端必须来自同一候选提交，禁止散件覆盖；
- 任一入口验收失败即回滚整个候选，不在服务器上追补文件。

---

## 7. 批准结论与剩余验收

Manager 已批准方案 A 的窄实现：新增一个内置 `online_plugin` SourcePlugin 与进程内 TTL opaque 引用存储，不扩展通用插件框架。

代码级契约、脱敏、引用存储、SourcePlugin、共享 fixture、WebUI、production build、容器 Node 门禁、测试服务器契约、真实 WebUI 搜索入口与设备实播均已通过。最终 stop 后主动 probe 确认原始 `status=2`；方案 A 验收闭合，README Roadmap 恢复完成。
