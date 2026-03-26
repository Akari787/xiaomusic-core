# 模块边界与职责

## 1. 模块总览

### API
- `xiaomusic/api/routers/*`：HTTP 路由入口，承载 v1、设备、音乐、系统、插件等接口
- `xiaomusic/api/models/*`：请求/响应模型
- `xiaomusic/api/dependencies.py`：全局依赖与运行时注入

### Core
- `xiaomusic/core/*`：播放核心抽象层，包含 `coordinator`、`device`、`delivery`、`source`、`transport`、`errors`、`models`

### Playback
- `xiaomusic/playback/facade.py`：API 到播放核心的适配层
- `xiaomusic/playback/link_strategy.py`：播放链接策略与代理回退决策

### Relay
- `xiaomusic/relay/*`：relay session 管理、流解析与站内流端点（`/relay/stream/{sid}`）

### Services
- `xiaomusic/services/online_music_service.py`：在线音乐搜索、MusicFree JS 插件调用、Jellyfin 搜索/同步

### Managers
- `xiaomusic/managers/js_plugin_manager.py`：MusicFree JS 插件运行时管理器

### Plugins
- `xiaomusic/plugin.py`：`exec#` 命令插件管理器
- `plugins/`：Python exec 插件实现目录

### Utils
- `xiaomusic/utils/*`：文件、网络、系统、文本、音乐相关通用工具

---

## 2. 正式入口

### 插件系统
- JS 插件管理：`xiaomusic/managers/js_plugin_manager.py`
- Python exec 插件管理：`xiaomusic/plugin.py`
- 兼容导出层：`xiaomusic/js_plugin_manager.py`

### 配置系统
- 运行态配置对象：`xiaomusic/config.py`（`Config` 类）
- 配置持久化：`xiaomusic/config_manager.py`
- 字段校验增强：`xiaomusic/config_model.py`

### 设备系统
- 设备生命周期管理：`xiaomusic/device_manager.py`
- 单设备播放控制：`xiaomusic/device_player.py`
- 新播放核心设备适配：`xiaomusic/core/device/device_registry.py`

### 播放控制
- 结构化播放入口：`xiaomusic/playback/facade.py`
- 在线音乐业务：`xiaomusic/services/online_music_service.py`
- JS 插件运行时：`xiaomusic/managers/js_plugin_manager.py`

### 兼容包装层
- `xiaomusic/online_music.py`：在线音乐兼容包装
- `xiaomusic/js_plugin_manager.py`：JS 插件兼容包装
