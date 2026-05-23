# Playback 相关规范（Playback Specifications）

> 目录用途：存放 playback 模块的协议、接口和行为规范文档
> 何时读取：改播放相关逻辑前必须先读
> 是否可作为当前实现依据：是（经审计的规范文档）

## 文档层级说明

| 文档 | 定位 | 权威级别 |
|---|---|---|
| `player_state_projection_spec.md` | 播放状态快照语义规范（字段定义、revision） | **核心权威** |
| `player_stream_sse_spec.md` | SSE 推送协议规范 | **核心权威** |
| `playback_coordinator_interface.md` | PlaybackCoordinator 接口约束 | **核心权威** |
| `webui_playback_state_machine_mapping.md` | WebUI 状态机映射（迁移用，非当前依据） | 迁移参考 |
| `auto_switch_delay_*.md` | 自动切歌延迟分析/验收 | 专项调研 |

## 关键约束

- `PlaybackFacade` 是 api 层调用播放能力的唯一入口
- `build_player_state_snapshot()` 是状态快照的唯一构建入口
- 不得绕过 facade 直接调用 coordinator 或 transport

## 层级说明

- **核心权威**：状态/SSE/接口规范，改动前必须阅读
- **迁移参考**：历史映射文档，用于理解旧实现
- **专项调研**：特定问题的深度分析

## 相关文档

- 状态权威：`docs/architecture/state-authority.md`
- 架构约束：`docs/architecture/constraints.md`
- 系统总览：`docs/architecture/system_overview.md`