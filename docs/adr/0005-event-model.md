# ADR-0005: 统一事件模型

## 状态：已撤回（2026-09-28）

## 上下文

2026-05 的设计希望把全部状态变化迁入统一事件模型，并引入约二十种标准事件、统一 payload schema、版本管理、`play_id` 与 replay 能力。

该方案没有完成实施。当前 `xiaomusic/events.py` 只有：

- `CONFIG_CHANGED`
- `DEVICE_CONFIG_CHANGED`
- `PLAYER_STATE_CHANGED`

播放、认证、Source 和 relay 的状态仍由各自权威模块维护。仓库中不存在原设计要求的标准事件类、事件版本注册、event replay 或 `play_id` 链路。

## 撤回决定

ADR-0005 不再作为现行架构约束。当前规则为：

1. EventBus 只承担已有的轻量通知，不是业务状态权威；
2. 不要求所有状态变化先发布事件；
3. 不宣称存在未实现的事件 schema、replay、`play_id` 或 snapshot 端点；
4. 如果未来重新引入完整事件模型，必须以新的 ADR 明确范围、迁移路径和可执行测试。

## 后果

- 删除未实现的 `event-model.md`、`correlation-id.md` 与 `observability.md` 设计稿，避免它们继续被误读为当前规范；历史内容仍可从 Git 记录查阅。
- `constraints.md` 与 `state-authority.md` 改为描述当前真实 ownership。
- 现有三个通知事件继续保留，因为配置保存和播放器 SSE 实际依赖它们。

## 判断依据

- 实现：`xiaomusic/events.py`
- 发布与订阅点：`xiaomusic/xiaomusic.py`、`xiaomusic/music_library.py`、`xiaomusic/device_player.py`、`xiaomusic/api/routers/v1.py`
- 全仓库不存在旧方案中的 `PLAY_REQUESTED`、`SOURCE_RESOLVED`、`AUTH_RESTORED` 与 `/api/v1/debug/snapshot`
