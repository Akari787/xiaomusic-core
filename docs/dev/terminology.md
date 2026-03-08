# 播放来源术语统一说明

## 1. 统一命名范围

为减少历史术语混用，项目文档中的播放来源名称统一为以下四类：

- **Local Library**
- **Site Media**
- **Direct URL**
- **Text TTS**

本说明仅用于文档命名统一，不改变现有代码架构与接口行为。

## 2. 四类来源定义

### Local Library

本地媒体库来源，例如本地文件、Jellyfin 媒体项。

### Site Media

站点媒体来源，例如 Bilibili、YouTube 等需要解析的媒体站点。

### Direct URL

可直接播放的媒体链接（HTTP/HTTPS 直链）。

### Text TTS

文本转语音播放来源。

## 3. deprecated 术语

历史术语：`network_audio`。

- 在“来源语义”场景中，`network_audio` 已拆分为 **Site Media / Direct URL**。
- 在历史文档、历史日志、历史接口路径中，如无法精确判定语义，保留为 `network_audio (deprecated)`。
- 旧术语仅用于追溯，不作为新文档推荐命名。
