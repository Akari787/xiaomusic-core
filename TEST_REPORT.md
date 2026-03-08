# 测试报告

> 说明：本文件主要记录 `v1.0.5` 阶段验收过程，作为历史留档。
> `v1.0.6` 发布前请在测试服务器补充独立验收记录（建议新增日期与 commit 标识）。

## 一、执行信息

- 执行日期：2026-02-28
- 环境：本地测试服务器（地址已脱敏）
- 镜像版本：`akari787/xiaomusic-oauth2:v1.0.5`
- 验证范围：旧版 key/code 鉴权移除（弃用清理）+ OAuth2/HTTP Basic 回归
- 代码提交：`90922d5`、`6e36b7e`、`cd650c6`

## 二、部署步骤

1. 同步 `oauth2-only` 最新代码到测试部署目录
2. 构建镜像：
   - `docker build -t akari787/xiaomusic-oauth2:v1.0.5 <部署目录>`
3. 部署服务：
   - `docker compose -f docker-compose.hardened.yml up -d --force-recreate`
4. 版本确认：
   - `GET /getversion` 返回 `{"version":"1.0.5"}`

## 三、功能验证结果

### 1) Secret 注入与启动校验

- 已注入 `API_SECRET`、`HTTP_AUTH_HASH`
- 缺失必填 secret 时服务无法通过配置校验启动

### 1.1) analytics 禁用时最小配置启动

- 配置 `XIAOMUSIC_ENABLE_ANALYTICS=false`
- 未提供 `API_SECRET` 仍可正常启动服务
- 验证点：应用初始化、API 路由、OAuth2 状态接口均正常

### 2) OAuth 状态与 token 持久化

- 重启前：`/api/oauth2/status` 显示 `token_valid=true`
- 重启后：`/api/oauth2/status` 仍为 `token_valid=true`

### 3) 二维码登录链路

- `GET /api/get_qrcode` 返回 `success=true` 且 `qrcode_url` 非空

### 4) 播放控制主链路

- `POST /api/v1/play_url`：`ok=true, state=streaming`
- `GET /api/v1/status`：`ok=true, stage=stream`
- `POST /api/v1/stop`：`ok=true, state=stopped`

### 4.1) WebUI（前后端分离）

- 在测试环境构建 `xiaomusic/webui`：`npm run build`
- 后端托管构建产物（`/webui/`）可访问
- 页面可读取 `/api/oauth2/status` 并展示登录状态
- 主入口仅保留默认主题入口，并新增 `/webui/` 入口

### 5) HTTP Basic + HTTP_AUTH_HASH 鉴权行为

- 开启 HTTP 认证后：
  - 无认证访问受保护接口：`401`
  - 错误口令：`401`
  - 正确口令：`200`

### 6) 旧版 key/code 鉴权移除行为

- 访问 `/music/*?key=...` 与 `/picture/*?code=...`：
  - 返回 `410`
  - 响应结构：`ok=false, success=false, error_code=E_LEGACY_LINK_AUTH_REMOVED`
  - 日志包含迁移提示：`legacy_link_auth_removed: migrate to HTTP Basic + HTTP_AUTH_HASH or OAuth2`

### 7) 外部服务不可用降级

- 人工注入不可用 MiJia 服务地址
- 返回结构化错误：`E_EXTERNAL_SERVICE_UNAVAILABLE`，并包含 `error_id`

### 8) tag cache 重建

- 调用 `/refreshmusictag`
- 日志出现基准统计：
  - `tag 更新完成 benchmark scanned=... refreshed=... skipped=... cost_ms=...`

## 四、安全检查

- 检索运行日志关键字：`API_SECRET`、`HTTP_AUTH_HASH`、`$2b$`
- 结果：未发现 secret 明文泄露

## 五、稳定性运行

- 持续运行时长：30 分钟
- 日志扫描关键字：`Traceback`、`ERROR`、`Exception`
- 结果：未发现致命异常堆栈，也未观察到鉴权循环异常

## 六、性能对比（Tag Cache）

优化前：

- `refresh_music_tag` 会清空缓存并进入全量重建路径
- 无增量跳过统计指标

优化后：

- 基于 `mtime + size` 的增量刷新
- 输出 `scanned/refreshed/skipped/cost_ms` 基准日志
- 当前测试数据下重建耗时接近 0~1ms

## 七、结论

- 总体结果：通过（PASS）
- 阻塞问题：无

## 八、覆盖率口径说明

- 本次为最小改动稳定性补丁，覆盖率以“新增/改动模块 + 回归用例”作为验收口径。
- 全仓 `--cov=xiaomusic` 为历史基线，不作为本次补丁阻塞条件。

## 九、2026-03-01 补充验收：修掉 API_SECRET 强制依赖

### 变更提交

- `9c82965`：`fix: analytics secret optional + lazy init (no API_SECRET required when disabled)`
- `3ec6759`：`test/docs: add tests + update docs + update TEST_REPORT`

### 目标与结论

- 在 `XIAOMUSIC_ENABLE_ANALYTICS=false` 且 `API_SECRET` 为空时，服务可正常启动。
- analytics 初始化保持 lazy：禁用时不读取 analytics secret；启用时若缺失 `API_SECRET`，在 analytics 初始化阶段抛出明确错误（测试覆盖）。
- HTTP Basic 校验路径仅依赖 `HTTP_AUTH_HASH`（测试覆盖）。

### 本地自动化测试

- 执行：
  - `pytest tests/test_app_init_without_api_secret.py`
  - `pytest tests/test_analytics_lazy_init.py`
  - `pytest tests/test_httpauth_hash.py`
  - `pytest tests/test_secret_settings.py`
  - 以及核心回归集合（system/api/play/session）
- 结果：`24 passed`

### 测试服务器实机验收（192.168.7.178）

- 部署方式：同步本次改动后，使用
  - `API_SECRET=`
  - `XIAOMUSIC_ENABLE_ANALYTICS=false`
  进行重建启动。
- 核心 API 抽检（契约保持不变）：
  - `GET /getversion` -> `{"version":"1.0.5"}`
  - `GET /api/oauth2/status` -> 保持 `success/token_valid/runtime_auth_ready/...` 结构
  - `POST /api/v1/set_play_mode` -> 保持 `ok/success/error_code/message` 结构
- WebUI：`GET /webui/` 返回 `200`，页面可打开。
- 日志安全检查：未发现 `API_SECRET`、`HTTP_AUTH_HASH`、bcrypt 明文片段泄露。
- 稳定性观察：连续运行约 15 分钟（20:12:26 ~ 20:27:32）无异常日志。

## 十、2026-03-01 补充验收：公共访问地址 UI 迁移 + 默认自动化

### 变更提交

- `1d205e0`：`refactor(ui): move public base url config to security page (collapsed)`
- `f1e530c`：`feat: auto-detect base url (origin/forwarded) + compatibility read`

### 验收环境

- 测试服务器：`192.168.7.178:58090`
- 部署目录：`/root/xiaomusic_oauth2_smoke`
- WebUI 构建产物：`/assets/index-DcQRMKxG.js`

### 验收步骤与结果

1. **默认自动地址（无手动填写）**
   - 通过 `GET /api/v1/detect_base_url` 获取自动地址：`http://192.168.7.178:58090`
   - WebUI 已迁移：原“基础设置”不再显示“NAS 的 IP/域名 + 本地端口”输入；该配置移至“安全访问 -> 公共访问地址（高级）”，且默认折叠。

2. **生成 URL 使用自动值**
   - 开启 `web_music_proxy=true` 后，调用 `GET /musicinfo?name=831143-nero`
   - 返回 `url` 前缀为：`http://192.168.7.178:58090/proxy/...`（与自动值一致）

3. **手动覆盖后生效**
   - 设置 `public_base_url=http://manual.example:12345`
   - 再次调用 `GET /musicinfo?name=831143-nero`
   - 返回 `url` 前缀为：`http://manual.example:12345/proxy/...`（覆盖生效）

4. **重置为自动后恢复**
   - 清空 `public_base_url` 并恢复兼容字段 `hostname/public_port` 为自动值
   - 再次调用 `GET /musicinfo?name=831143-nero`
   - 返回 `url` 前缀恢复为：`http://192.168.7.178:58090/proxy/...`

5. **日志安全检查**
   - 检索关键字：`API_SECRET`、`HTTP_AUTH_HASH`、`$2b$`
   - 结果：未发现敏感信息泄露。

### 结论

- 方案 A 验收通过：
  - 公共访问地址已迁移到“安全访问”的高级折叠区；
  - 默认场景无需手动填写；
  - 手动覆盖与重置自动均按预期生效；
  - 兼容旧配置 `hostname/public_port`。
