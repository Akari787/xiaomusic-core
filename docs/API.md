# API 收口说明

最后更新：2026-03-15

## 唯一正式规范入口

- 当前仓库唯一正式 API 文档：`docs/api/api_v1_spec.md`

## `/cmd` 的地位

- `/cmd` 不再属于正式 API 契约。
- 中文命令字符串不是正式协议输入。
- `match_cmd()`、`exec#...`、自定义 `cmd` 均不进入正式 v1。
- `POST /cmd` 当前服务器行为为：`410 Gone`，仅返回废弃提示与 `/api/v1/*` 推荐迁移接口。

## 当前正式结构化能力

以下接口是后续 WebUI、Home Assistant 与第三方集成应依赖的正式能力：

- `POST /api/v1/control/previous`
- `POST /api/v1/control/next`
- `POST /api/v1/control/play-mode`
- `POST /api/v1/control/shutdown-timer`
- `POST /api/v1/library/favorites/add`
- `POST /api/v1/library/favorites/remove`
- `POST /api/v1/playlist/play`
- `POST /api/v1/playlist/play-index`
- `POST /api/v1/library/refresh`

这些接口与现有 `play / stop / pause / resume / volume / tts / probe / player state` 一起构成当前主线的正式 API 面。

## 使用原则

- 所有新功能设计、自动化编排、AI 代码修改都必须以 `docs/api/api_v1_spec.md` 为最终依据。
- 不得再把 `/cmd`、自然语言命令字符串或命令匹配规则当作正式协议设计来源。
