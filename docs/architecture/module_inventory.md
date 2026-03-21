# 模块盘点与边界梳理

本文档用于盘点当前项目的主要模块边界，识别重复模块、职责重叠模块、正式入口模块与遗留/兼容模块，为后续结构优化提供依据。

约束：本次仅阅读代码并输出分析，不涉及任何运行时行为修改。

## 1. 模块盘点总览

### API
- `xiaomusic/api/routers/*`：HTTP 路由入口，分别承载 v1、设备、音乐、系统、插件、网络音频等接口。
- `xiaomusic/api/models/*`：请求/响应模型。
- `xiaomusic/api/dependencies.py`：全局依赖与运行时注入。

### Core
- `xiaomusic/core/*`：新的播放核心抽象层，包含 `coordinator`、`device`、`delivery`、`source`、`transport`、`errors`、`models`。
- 该层主要服务于结构化 v1 播放接口与新的 PlaybackFacade。

### Playback
- `xiaomusic/playback/facade.py`：API 到新播放核心的薄适配层。
- `xiaomusic/playback/link_strategy.py`：播放链接策略与代理回退决策。

### Network Audio（relay，legacy alias）
- `xiaomusic/relay/*`：relay session 管理、流解析与站内流端点（`/relay/stream/{sid}`）等独立链路。

### Services
- `xiaomusic/services/online_music_service.py`：在线音乐搜索、MusicFree JS 插件调用、Jellyfin 搜索/同步。
- 当前 service 层很薄，只有在线音乐服务形成明确实体。

### Managers
- `xiaomusic/managers/js_plugin_manager.py`：MusicFree JS 插件运行时管理器。
- 当前 manager 层非常窄，事实上只承载 JS 插件管理。

### Plugins
- `xiaomusic/plugin.py`：`exec#` 命令插件管理器。
- `plugins/`：Python exec 插件实现目录。
- `xiaomusic/js_plugin_manager.py`：兼容导出层，转发到 `xiaomusic/managers/js_plugin_manager.py`。

### Utils
- `xiaomusic/utils/*`：文件、网络、系统、文本、音乐相关通用工具。

## 2. 歧义模块清单

### 模块组：插件系统

涉及文件：
- `xiaomusic/plugin.py`
- `xiaomusic/js_plugin_manager.py`
- `xiaomusic/managers/js_plugin_manager.py`
- `xiaomusic/online_music.py`
- `xiaomusic/services/online_music_service.py`
- `plugins/`

问题：
- 存在两个 `js_plugin_manager` 路径：顶层 `xiaomusic/js_plugin_manager.py` 与正式实现 `xiaomusic/managers/js_plugin_manager.py`。
- `xiaomusic/plugin.py` 名称容易被误解成“统一插件入口”，但实际只负责 `exec#` Python 插件，不负责 MusicFree JS 插件。
- `plugins/` 目录是 Python exec 插件代码目录，但 JS 插件并不在这里，而是运行时从 `conf/js_plugins` 读取。
- `online_music.py` 本身只是兼容包装，真正逻辑在 `services/online_music_service.py`，形成一组 service/wrapper 重叠。
- 插件“运行逻辑”和“插件管理逻辑”没有完全混在同一个文件里，但 JS 插件管理器内部同时承担：Node 进程生命周期、消息 IPC、插件配置读取、插件加载、搜索执行，职责偏重。

### 模块组：配置系统

涉及文件：
- `xiaomusic/config.py`
- `xiaomusic/config_manager.py`
- `xiaomusic/config_model.py`

问题：
- `config.py` 同时承载：默认值、dataclass schema、环境变量解析、配置更新逻辑、路径推导，是事实上的核心配置模块。
- `config_model.py` 并不是完整配置 schema，只覆盖了安全/网络/Jellyfin 等一部分字段的 Pydantic 校验，因此 schema 没有完全收敛到这里。
- `config_manager.py` 负责配置文件加载/保存/更新，但运行态配置状态对象仍由 `Config` 实例持有。
- 结果是“配置 schema”和“配置持久化管理”是拆开的，但“单一正式 schema 模块”并未完全建立。

### 模块组：设备系统

涉及文件：
- `xiaomusic/device_manager.py`
- `xiaomusic/device_player.py`
- `xiaomusic/core/device/device_registry.py`

问题：
- 传统设备链与新 core 设备链并存。
- `device_manager.py` 负责设备实例字典、设备分组、设备映射与生命周期更新。
- `device_player.py` 负责单设备播放、计时器、TTS、下载、播放列表与设备状态，是重量级运行时模块。
- 新 `core/device/device_registry.py` 仅为 PlaybackFacade / core coordinator 提供新的设备解析适配，不是完整替代 `DeviceManager`。
- 因此当前有“旧设备运行时主链 + 新播放核心设备适配层”双轨并存的特征。

### 模块组：服务 / manager / facade

涉及文件：
- `xiaomusic/playback/facade.py`
- `xiaomusic/services/online_music_service.py`
- `xiaomusic/managers/js_plugin_manager.py`
- `xiaomusic/online_music.py`
- `xiaomusic/js_plugin_manager.py`

问题：
- `facade` 已经形成新播放控制的稳定入口，但只覆盖结构化播放 API，不覆盖全项目所有控制面。
- `service` 当前几乎只有 `OnlineMusicService` 一个主要实体，职责是在线音乐搜索与外部源整合。
- `manager` 当前实际只剩 `JSPluginManager`，说明 manager 层并未形成统一抽象体系。
- 顶层 wrapper（`online_music.py`、`js_plugin_manager.py`）与正式实现（`services/...`、`managers/...`）并存，边界有兼容性痕迹。

## 3. 模块结论

### 3.1 插件系统

正式入口：
- JS 插件管理：`xiaomusic/managers/js_plugin_manager.py`
- Python exec 插件管理：`xiaomusic/plugin.py`

辅助模块：
- `xiaomusic/js_plugin_manager.py`：兼容导出层
- `plugins/`：exec 插件函数集合

遗留/兼容模块：
- `xiaomusic/js_plugin_manager.py`（兼容包装）

建议方向：
- 保留“JS 插件”和“exec 插件”两条链，但需要在命名上进一步显式区分。
- 后续可考虑把 `plugin.py` 更明确标记为 `exec plugin` 体系，避免与 JS 插件混淆。

### 3.2 配置系统

正式入口：
- 运行态配置对象：`xiaomusic/config.py` 中的 `Config`
- 配置持久化与文件入口：`xiaomusic/config_manager.py`

辅助模块：
- `xiaomusic/config_model.py`：部分字段的 Pydantic 校验增强

遗留/兼容模块：
- 无明显废弃文件，但 `config_model.py` 属于“未完全接管 schema 的半收敛层”

建议方向：
- 现阶段正式配置入口应认定为 `Config + ConfigManager` 组合。
- 若未来继续优化，优先考虑把 schema 校验逐步集中到 `config_model.py` 或统一 schema 层。

### 3.3 设备系统

正式入口：
- 设备生命周期管理：`xiaomusic/device_manager.py`
- 单设备播放控制：`xiaomusic/device_player.py`

辅助模块：
- `xiaomusic/core/device/device_registry.py`：新播放核心对设备的读取适配

遗留/兼容模块：
- 无明确遗留模块，但存在“旧运行时设备链 + 新 core 适配层”并行

建议方向：
- 当前 API 与运行态主要依赖 `DeviceManager` / `XiaoMusicDevice`。
- `core/device/device_registry.py` 目前更适合视为新播放架构的局部适配层，而不是正式设备主入口。

### 3.4 服务 / manager / facade

正式入口：
- 结构化播放 API 正式入口：`xiaomusic/playback/facade.py`
- 在线音乐业务正式入口：`xiaomusic/services/online_music_service.py`
- JS 插件运行时正式入口：`xiaomusic/managers/js_plugin_manager.py`

辅助模块：
- `xiaomusic/online_music.py`：兼容包装
- `xiaomusic/js_plugin_manager.py`：兼容包装

遗留/兼容模块：
- `xiaomusic/online_music.py`
- `xiaomusic/js_plugin_manager.py`

建议方向：
- `facade`、`service`、`manager` 的职责方向已初步分开，但目录层面的统一性还不强。
- 现阶段应承认 `PlaybackFacade` 是播放控制的新正式入口；`services`/`managers` 尚未形成全面统一的中层架构。

## 4. 后续重构优先级建议

### 优先级高

1. 插件系统命名与边界收敛
- 明确区分 JS 插件管理器与 exec 插件管理器。
- 降低 `plugin.py` / `js_plugin_manager.py` 命名歧义。

2. 配置 schema 收敛
- 逐步让 `config_model.py` 接管更多字段校验。
- 避免 `Config` dataclass 与 Pydantic schema 长期双轨半重叠。

3. wrapper 模块清理策略
- `xiaomusic/online_music.py`
- `xiaomusic/js_plugin_manager.py`

这两个兼容层应在后续确定保留期限与迁移计划。

### 优先级中

1. 设备旧链与 core 新链的边界说明
- 当前双轨并存是可接受状态，但需要更明确的架构说明，防止后续开发误判入口。

2. JSPluginManager 拆分内部职责
- 进程管理
- IPC/消息处理
- 插件配置与状态管理
- 搜索执行入口

目前都在一个类中，维护成本较高。

### 暂时保持稳定

1. `PlaybackFacade` + `core/*`
- 当前已经形成比较清晰的新播放入口，不建议在没有明确迁移计划前再次大改。

2. `DeviceManager` + `XiaoMusicDevice`
- 虽然较重，但仍是当前事实上的正式运行时入口，短期内更适合稳定使用而非大改。

## 5. 总结

当前项目已经出现较明确的“新正式入口”趋势：

- 播放控制：`PlaybackFacade`
- 在线音乐：`OnlineMusicService`
- JS 插件：`managers/js_plugin_manager.py`
- 设备运行时：`DeviceManager` + `XiaoMusicDevice`
- 配置运行时：`Config` + `ConfigManager`

但同时也保留了一批兼容包装与双轨模块：

- `xiaomusic/js_plugin_manager.py`
- `xiaomusic/online_music.py`
- `core/device/*` 与传统设备链并存
- `config_model.py` 与 `Config` 的部分重叠

因此，后续结构优化最值得优先推进的不是大范围重写，而是：

1. 明确正式入口  
2. 收敛 wrapper / legacy 层  
3. 梳理命名与 schema 归属  

这会比直接重构业务逻辑更稳妥。
