# 目录职责

本目录提供项目所有安全相关的基础能力，包括认证 token 持久化、出站请求策略、exec 插件调用校验、日志脱敏与安全解压。

所有安全能力均为基础层：被 `api/`、`utils/`、`managers/`、`services/` 等上层模块依赖，不依赖任何业务层。

# 主要内容

## token_store.py

线程安全的认证 token 持久化存储，提供内存镜像 + 原子磁盘写入能力。

职责：
- 从 `auth.json` 加载并保持内存镜像
- 写入时原子替换文件（防止写入中途崩溃导致损坏）
- 读写使用内部锁序列化，不支持多进程并发写

是整个认证系统的磁盘持久化底层，由 `auth.py` 的认证运行时直接使用。

## outbound.py

出站 HTTP 请求的安全策略层，封锁对私网 / 回环 / 链路本地 / 组播地址的请求。

职责：
- `OutboundPolicy`：持有 allowlist，判断目标 URL 是否允许出站
- 私网地址检测：覆盖 IPv4 私网、IPv6 回环、链路本地、组播、保留地址段
- `fetch_text` / `fetch_bytes`：受策略控制的 aiohttp 封装，违规时抛 `OutboundBlockedError`

allowlist 的配置来源为运行时 `Config`，插件 exec 的 HTTP 调用必须经过此层。

## exec_plugin.py

`exec#` 命令插件的调用校验层，保证插件只能执行白名单内的命令并且参数合法。

职责：
- 解析 exec 调用格式（`exec#command(args...)` 语法）
- 校验命令是否在允许列表内（`ExecNotAllowedError`）
- AST 字面量解析参数，禁止注入（`ExecValidationError`）
- HTTP GET 插件动作经过 `OutboundPolicy` 校验

此文件保护系统免受恶意 exec 命令注入，不应被 bypass。

## redaction.py

日志输出中敏感字段的文本脱敏工具。

职责：
- 识别并脱敏 `token / authorization / cookie / api_key / password` 等键值对
- 识别并脱敏 `Bearer <value>` 格式 token
- 输出 `***REDACTED***` 替代原始敏感值

使用方：`security/logging.py` 中的 `RedactingLogFormatter` 在格式化阶段自动调用。

## logging.py

`RedactingLogFormatter`：在 `logging.Formatter` 格式化阶段自动调用 `redact_text()`，确保日志输出中不出现原始 token 或密钥。

挂载位置由应用启动时的日志配置决定。

## tar_safe.py

安全的 `.tar.gz` 解压工具，防止路径穿越攻击。

职责：
- 阻止绝对路径成员（如 `/etc/passwd`）
- 阻止 `..` 路径穿越（如 `../../evil`）
- 阻止符号链接 / 硬链接解压（防链接逃逸）

被 `utils/system_utils.py` 的自更新下载流程调用。

## errors.py

安全层统一错误类型：

| 异常类 | 场景 |
|---|---|
| `SecurityError` | 安全层所有错误基类 |
| `ExecDisabledError` | exec 插件功能被禁用 |
| `ExecNotAllowedError` | 命令不在 allowlist 中 |
| `ExecValidationError` | 参数解析或合法性校验失败 |
| `OutboundBlockedError` | 出站请求被策略封锁 |
| `SelfUpdateDisabledError` | 自更新功能被禁用 |

# 不应该放什么

- 业务认证流程（`auth.py` 负责）
- HTTP 路由与 API 权限校验（`api/dependencies.py` 负责）
- 播放控制、设备管理等任何业务逻辑

# 关键入口

- `xiaomusic/security/token_store.py`：认证持久化底层
- `xiaomusic/security/outbound.py`：出站请求安全策略
- `xiaomusic/security/exec_plugin.py`：exec 插件调用校验
- `xiaomusic/security/errors.py`：统一错误类型

# 与其他目录的关系

- **`xiaomusic/auth.py`**：认证运行时，使用 `TokenStore` 作为持久化后端，使用 `OutboundPolicy` 做 token 刷新出站控制。
- **`xiaomusic/utils/network_utils.py`**：网络下载工具通过 `fetch_text / fetch_bytes` 走 outbound 策略。
- **`xiaomusic/utils/system_utils.py`**：自更新流程使用 `safe_extract_tar_gz` 与 `SelfUpdateDisabledError`。
- **`xiaomusic/plugin.py`**：exec 插件管理器调用 `exec_plugin.py` 做命令校验。
- **`xiaomusic/api/`**：API 层不直接使用 security 模块（权限校验通过 `dependencies.py` + `core/settings.py` 完成）。

# 新增代码规则

- 所有新增的"限制或检测敏感操作"逻辑，应优先放在本目录，不在业务层散落安全判断。
- `errors.py` 新增错误类时继承 `SecurityError`，保持错误层次清晰。
- 不在本目录引入业务层依赖（如 `device_player`、`config` 等），保持基础层地位。
