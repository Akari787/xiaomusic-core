# 测试报告

## 一、执行信息

- 执行日期：2026-02-28
- 环境：本地测试服务器（地址已脱敏）
- 镜像版本：`akari787/xiaomusic-oauth2:v1.0.5`
- 验证范围：安全加固、外部服务降级、tag cache 增量刷新、超大文件拆分兼容

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

### 2) OAuth 状态与 token 持久化

- 重启前：`/api/oauth2/status` 显示 `token_valid=true`
- 重启后：`/api/oauth2/status` 仍为 `token_valid=true`

### 3) 二维码登录链路

- `GET /api/get_qrcode` 返回 `success=true` 且 `qrcode_url` 非空

### 4) 播放控制主链路

- `POST /api/v1/play_url`：`ok=true, state=streaming`
- `GET /api/v1/status`：`ok=true, stage=stream`
- `POST /api/v1/stop`：`ok=true, state=stopped`

### 5) 外部服务不可用降级

- 人工注入不可用 MiJia 服务地址
- 返回结构化错误：`E_EXTERNAL_SERVICE_UNAVAILABLE`，并包含 `error_id`

### 6) tag cache 重建

- 调用 `/refreshmusictag`
- 日志出现基准统计：
  - `tag 更新完成 benchmark scanned=... refreshed=... skipped=... cost_ms=...`

## 四、安全检查

- 检索运行日志关键字：`API_SECRET`、`HTTP_AUTH_HASH`、`$2b$`
- 结果：未发现 secret 明文泄露

## 五、稳定性运行

- 持续运行时长：30 分钟
- 日志扫描关键字：`Traceback`、`ERROR`、`Exception`
- 结果：未发现致命异常堆栈

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
