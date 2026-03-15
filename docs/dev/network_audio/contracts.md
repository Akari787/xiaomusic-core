# Network Audio Unified Contracts

> 术语说明：本文标题中的 `Network Audio` 为历史模块名；来源语义请使用 `Site Media` / `Direct URL`。
> 本文属于开发期契约说明，面向历史模块与测试工具，不作为当前正式 API 文档。

本文定义历史 `network_audio` 模块之间允许传递的数据结构。

说明：runtime 包路径仍使用 `xiaomusic/network_audio`，这里只描述模块边界，不改变代码目录结构。

## 数据模型

- `UrlInfo`：输入 URL 的分类与规范化结果
- `ResolveResult`：`yt-dlp` 等解析阶段输出
- `Session`：流会话生命周期状态
- `Event`：可观测运行时事件

标准示例位于 `docs/dev/network_audio/contracts.examples.json`，并由单元测试校验。

## 错误码

- `E_URL_UNSUPPORTED`
- `E_RESOLVE_TIMEOUT`
- `E_RESOLVE_NONZERO_EXIT`
- `E_STREAM_START_FAILED`
- `E_STREAM_NOT_FOUND`
- `E_STREAM_SINGLE_CLIENT_ONLY`
- `E_XIAOMI_PLAY_FAILED`
- `E_INTERNAL`
