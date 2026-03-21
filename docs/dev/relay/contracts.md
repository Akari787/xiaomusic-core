# Relay Contracts

> 本文属于开发期契约说明，面向 relay 模块与测试工具，不作为当前正式 API 文档。

本文定义 relay 模块之间允许传递的数据结构。

## 数据模型

- `UrlInfo`：输入 URL 的分类与规范化结果
- `ResolveResult`：`yt-dlp` 等解析阶段输出
- `Session`：流会话生命周期状态
- `Event`：可观测运行时事件

标准示例位于 `docs/dev/relay/contracts.examples.json`，并由单元测试校验。

## 错误码

- `E_URL_UNSUPPORTED`
- `E_RESOLVE_TIMEOUT`
- `E_RESOLVE_NONZERO_EXIT`
- `E_STREAM_START_FAILED`
- `E_STREAM_NOT_FOUND`
- `E_STREAM_SINGLE_CLIENT_ONLY`
- `E_XIAOMI_PLAY_FAILED`
- `E_INTERNAL`
