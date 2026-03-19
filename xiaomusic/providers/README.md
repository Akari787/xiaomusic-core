# 目录职责

本目录提供特定业务领域的轻量数据处理工具，当前聚焦在线音乐关键词的构造与解析。

"providers"在本项目中的定位：不是运行时状态持有者，而是为 services 层提供纯函数形态的数据处理能力。

# 文件说明

## online_music_keywords.py

在线音乐搜索关键词的构造与解析工具。

提供两个纯函数：

- `build_keyword(song_name, artist) -> str`：将歌名与歌手拼接为 `"歌名-歌手"` 格式的搜索关键词；若仅有其一，则直接返回非空部分。
- `parse_keyword_by_dash(keyword) -> (song_name, artist)`：将 `"歌名-歌手"` 格式拆分为元组；若无 `-`，则整体作为歌名返回。

典型使用方：`services/online_music_service.py`（构造 Jellyfin / MusicFree 搜索关键词）

# 不应该放什么

- 持有网络连接或运行时状态的对象
- 业务编排逻辑（属于 `services/`）
- 通用工具函数（属于 `utils/`）

# 定位说明

`providers/` 目录当前内容较少。它的存在是为了给"特定业务数据格式的纯函数工具"提供一个明确的归属，防止这类函数散落在 services 或 utils 中。如果未来在线音乐搜索增加更多格式规则（如标签解析、元数据标准化），应集中放在本目录。
