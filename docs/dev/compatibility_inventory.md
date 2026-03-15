# Compatibility Inventory (v1.1.0 Phase 1)

> 最后更新：2026-03-08  
> 对照基线：`xiaomusic-v1-roadmap.md`（WF1/WF2/WF3）

## 1. 目的

本清单用于给 v1.1.0 主干收口提供唯一台账：

- 兼容项是否仍存在
- 当前状态（已移除 / deprecated 保留 / 暂不处理）
- 正式调用链是否仍依赖该兼容项
- 本阶段是否需要继续动作

状态定义（本文件统一使用）：

- **DELETE**：已完成迁移，可删除或已删除。
- **DEPRECATED**：保留兼容，但禁止新代码继续依赖。
- **KEEP**：当前阶段保留（通常为桥接层或范围外项）。

---

## 2. Phase 1 交叉检查结论

### 2.1 后端收口

- `PlaybackFacade.status()` 已显式化为 `status(device_id: str)`，不再使用 `status(target: dict)`。
- `PlayOptions` 已落地：`play/resolve` 的 `options` 已统一进入 `PlayOptions.from_payload()`。
- payload 键名已集中到 `xiaomusic/core/models/payload_keys.py`，主链路不再散落在 facade。
- `MediaRequest.from_payload(...)` 已落地，`payload -> model -> runtime` 路径更清晰。
- `/api/v1/*` 正式链路不再绕回 legacy facade 方法。
- 错误模型已有最小映射：`InvalidRequestError / SourceResolveError / DeliveryPrepareError / TransportError / DeviceNotFoundError` -> v1 统一响应码段。

### 2.2 前端收口

- `v1Api.ts` 已作为正式 `/api/v1/*` 入口。
- 首页核心播放/控制动作已优先走 v1（`play/stop/tts/volume/player-state/devices`）。
- 默认首页正常，API 调试页降级为低显眼度入口（右下角链接）。
- 错误提示已可区分 `resolve / prepare / dispatch` 阶段，并映射关键错误码。

### 2.3 仍需保留观察项（不阻塞 Phase 1）

- 首页部分非主链路交互仍依赖历史接口（如 `/cmd`、`/device_list` 回退）。
- 旧 router wrapper 仍在线（已加 deprecated 日志），用于外部脚本迁移缓冲。

---

## 3. 兼容项台账

### 3.1 API / Router 层

| ID | 兼容项 | 状态 | planned removal | 主链路依赖 | 说明 |
|---|---|---|---|---|---|
| API-C01 | `GET /getplayerstatus` | DEPRECATED | v1.2 | 否 | 已记录迁移目标 `/api/v1/player/state` |
| API-C02 | `POST /device/stop` | DEPRECATED | v1.2 | 否 | 通过 facade legacy bridge 仅做过渡 |
| API-C03 | `POST /setvolume` | DEPRECATED | v1.2 | 否 | 迁移目标 `/api/v1/control/volume` |
| API-C04 | `GET /playtts` | DEPRECATED | v1.2 | 否 | 迁移目标 `/api/v1/control/tts` |
| API-C05 | `/cmd` 与 `/cmdstatus` | KEEP | TBD | 否（不属于 v1 主链） | 属于历史控制面，单独治理 |
| API-C06 | 旧 key/code 链接鉴权 | DELETE | v1.1 已完成 | 否 | 当前返回 410，强制迁移 |

### 3.2 Facade 层

| ID | 兼容项 | 状态 | planned removal | 主链路依赖 | 说明 |
|---|---|---|---|---|---|
| FAC-C01 | `status(device_id)` 显式契约 | DELETE | v1.1 已完成 | 是 | 已替代旧 dict 参数签名 |
| FAC-C02 | `stop_legacy(...)` | DEPRECATED | v1.2 | 否 | 仅旧 router 使用 |
| FAC-C03 | `pause_legacy(...)` | DEPRECATED | v1.2 | 否 | 仅旧 router 使用 |
| FAC-C04 | `tts_legacy(...)` | DEPRECATED | v1.2 | 否 | 仅旧 router 使用 |
| FAC-C05 | `set_volume_legacy(...)` | DEPRECATED | v1.2 | 否 | 仅旧 router 使用 |
| FAC-C06 | `PlayOptions` 契约 | DELETE | v1.1 已完成 | 是 | 已替代 facade 顶层裸 dict 语义 |
| FAC-C07 | payload 键常量化 + `MediaRequest.from_payload(...)` | DELETE | v1.1 已完成 | 是 | 主链路映射已集中 |

### 3.3 Source / Transport / 配置层

| ID | 兼容项 | 状态 | planned removal | 主链路依赖 | 说明 |
|---|---|---|---|---|---|
| SRC-C01 | `LEGACY_HINT_MAP` | KEEP | v1.2 评估 | 低 | 仅承接历史 hint |
| SRC-C02 | `LegacyPayloadSourcePlugin` | DEPRECATED | v1.2 | 低 | compatibility only，不接新功能 |
| SRC-C03 | `DeviceRegistry._hydrate_from_legacy()` | KEEP | TBD | 是 | runtime bridge，后续再抽离 |
| CFG-C01 | 旧配置字段兼容迁移 | KEEP | TBD | 是 | 与播放主链路解耦，不在本轮清理 |
| MOD-C01 | 旧模块 re-export | KEEP | TBD | 低 | 不阻塞 v1.1.0 Phase 1 |

---

## 4. 本阶段结论

1. 后端与前端“前两步收口”已达到可交付主干状态。  
2. 仍存在的兼容项已被明确降级为 deprecated/compatibility only，不再作为正式路径。  
3. 本轮重点从“改代码”转为“补齐正式文档与边界说明”，并保持文档/实现/测试行为一致。  

---

## 5. 后续建议（不在本轮扩张实现）

1. 对 `/cmd` 系列历史入口单独建迁移计划，避免继续承载新交互。  
2. 为 deprecated router wrapper 增加统一迁移窗口说明（版本+下线时间）。  
3. 等外部调用方迁移后，再清理 facade legacy bridge 与 hint alias。  

---

## 6. Stable Login 收口说明（2026-03）

本轮稳定版只收口认证运行时恢复链，不扩展播放控制 API 面。

- `POST /api/auth/refresh` 与 `POST /api/auth/refresh_runtime` 为当前规范路径，语义同为 runtime reload from disk。
- 自动恢复链禁用 `mi_account.login("micoapi")`，仅保留策略级 `disabled_by_policy` 可观测标记。
- 恢复链固定为：`clear_short_session -> rebuild_short_session_from_persistent_auth -> runtime_rebind -> verify`。
- locked 仅在长期态缺失等终态场景触发，不因短期会话问题直接锁死。

延期到下版本（明确不在本轮）：

- playlist API
- queue API
- library/object API
- 其他 API 扩展项
