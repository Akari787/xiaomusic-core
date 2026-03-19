# 目录职责

本目录存放项目范围内共享的字符串常量，避免在多处硬编码相同的字段名或标识符。

# 文件说明

## api_fields.py

API 请求 payload 中的字段名常量，供 API 路由层与核心模型层共享使用。

当前定义：

| 常量 | 值 | 用途 |
|---|---|---|
| `DEVICE_ID` | `"device_id"` | 设备标识符字段 |
| `QUERY` | `"query"` | 播放查询字段 |
| `SOURCE_HINT` | `"source_hint"` | 来源提示字段 |
| `OPTIONS` | `"options"` | 播放选项字段 |
| `REQUEST_ID` | `"request_id"` | 请求追踪 ID 字段 |

注意：`PlayOptions` 内部字段的常量定义在 `xiaomusic/core/models/payload_keys.py`，不在本目录。本目录只存放顶层请求 payload 的公共字段名。

# 不应该放什么

- 业务枚举值（如播放模式、来源类型）——应放在各自业务模块
- 运行时配置默认值——应放在 `config.py`
- 错误码——应放在 `core/errors/` 或 `api/` 对应层

# 新增规则

- 只在"多个不同目录都需要引用同一个字符串字面量"时，才在本目录定义常量。
- 不把只在单一模块内使用的常量放到这里。
