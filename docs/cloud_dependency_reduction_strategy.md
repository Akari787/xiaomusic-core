# IoT 音频系统：减少云依赖架构策略

本文面向“控制小米小爱音箱播放音乐”的系统，目标是降低 Xiaomi Cloud 依赖、提升稳定性与可用性。

---

## 1. 目标与问题

当前痛点：

- 播放命令强依赖 Xiaomi Cloud（MiNA）
- `device_list` 等接口调用频繁
- 云端 token 过期会放大故障影响

重构目标：

- Cloud 只做必要能力（鉴权、必要播放下发）
- 本地能力优先（MiIO 的 `tts/stop/volume/pause`）
- 设备信息与元数据本地缓存，降低云调用频率
- 云不可用时系统可“降级可用”

---

## 2. 总体架构图

```text
                        +---------------------------+
                        |       API / Core          |
                        | queue/scheduler/router    |
                        +-------------+-------------+
                                      |
                          +-----------v-----------+
                          |   Transport Router    |
                          +------+---------+------+
                                 |         |
                       +---------v-+     +-v---------+
                       | Miio Local|     | Mina Cloud |
                       | Transport |     | Transport  |
                       +-----+-----+     +-----+------+
                             |                 |
                     +-------v-------+   +-----v----------------+
                     |  LAN Devices  |   | Cloud Session Manager |
                     | (IP+Token)    |   | token/refresh/backoff |
                     +---------------+   +-----------+-----------+
                                                     |
                                               +-----v-----+
                                               | Xiaomi API |
                                               +-----------+

              +-------------------+    +-------------------+
              | Device Registry   |    | Metadata Cache    |
              | did/device map    |    | media/url/cache   |
              +-------------------+    +-------------------+
```

---

## 3. 组件职责

## 3.1 Cloud Session Manager

职责：

- 管理 cloud token 生命周期（load/refresh/persist）
- 管理 Mina 客户端可用性与熔断状态
- 提供统一 `ensure_ready()` 给上层调用
- 屏蔽 cloud 异常细节（统一错误码）

推荐实现：

- 单例 + 互斥锁（防止并发 refresh）
- 刷新策略：提前刷新 + 失败退避（指数 backoff）
- 持久化 token（沿用 `TokenStore`）
- 暴露状态：`ready/refreshing/circuit_open/last_error`

接口示例：

```python
class CloudSessionManager:
    async def ensure_ready(self, reason: str = "") -> bool: ...
    async def refresh(self, force: bool = False) -> dict: ...
    def status(self) -> dict: ...
    def get_mina_client(self): ...
```

---

## 3.2 Device Registry

职责：

- 维护统一设备视图：`did -> device_id/ip/model/capability`
- 启动优先加载本地快照，不阻塞服务
- 低频从 cloud 同步并增量更新
- 维护设备可达性与最近探测状态

推荐实现：

- 内存 + 本地文件双层（如 `conf/device_registry.json`）
- 数据字段：`did/device_id/name/model/ip/token_ref/last_seen/capability`
- 支持多键查询（did/device_id/name）

接口示例：

```python
class DeviceRegistry:
    def load_snapshot(self) -> int: ...
    async def refresh_from_cloud(self) -> int: ...
    def get(self, did: str): ...
    def resolve(self, key: str): ...
    def save_snapshot(self) -> None: ...
```

---

## 3.3 Metadata Cache

职责：

- 缓存媒体元数据（title/artist/duration）
- 缓存解析后的可播放 URL（短 TTL）
- 缓存失败结果（negative cache）避免击穿

推荐实现：

- L1 进程内缓存（快速）
- L2 持久缓存（Redis/SQLite/文件）
- TTL 分层：
  - metadata: 10m~24h
  - playable URL: 30s~5m
  - negative: 30s~2m

---

## 4. Cloud Bootstrap Strategy（启动策略）

目标：避免“启动必须连 cloud 才可用”。

启动顺序建议：

1. 读取配置与 token 快照
2. 加载 `DeviceRegistry` 快照
3. 启动 API（进入 `degraded-ready`）
4. 后台异步进行 cloud `ensure_ready()`
5. cloud 成功后标记 `cloud-ready`

设计要点：

- 启动失败不应由 cloud 阻塞（除非显式 strict 模式）
- 提供 `/healthz` 区分 `service_up` 与 `cloud_ready`

---

## 5. Local-first 能力策略

默认优先策略：

- `tts/stop/pause/set_volume`：优先 MiIO（局域网）
- `play_url`：优先 Mina（若设备明确支持本地播放再尝试 MiIO）
- `probe`：优先本地探测，云探测仅兜底

落地方式：

- 在 `DeviceCapability` 中记录命令级能力
- Router 按命令 + 设备能力选 primary/fallback

---

## 6. Cloud fallback 策略

当 cloud 不可用或过载时：

1. 控制类命令自动切本地（MiIO）
2. 播放命令返回明确降级错误（可选择排队重试）
3. 若本地也失败，返回标准错误码并记录恢复建议

错误级别：

- 可重试：`E_CLOUD_TEMP_UNAVAILABLE`、`E_TIMEOUT`
- 不可重试：`E_UNAUTHORIZED_PERMANENT`、`E_BAD_REQUEST`

---

## 7. 云端调用降频策略

## 7.1 device_list 降频

- 启动时 1 次
- 定时低频同步（建议 10~30 分钟）
- 手动触发同步接口（运维使用）
- 禁止每次播放前调用 `device_list`

## 7.2 去重与节流

- 同类请求并发去重（single-flight）
- 速率限制（token bucket）
- 缓存命中优先返回

## 7.3 熔断

- 连续失败阈值触发熔断（如 5 次）
- 熔断窗口内拒绝非必要 cloud 调用
- 半开探测恢复

---

## 8. 离线模式策略

定义离线模式：`cloud_unavailable && local_transport_available`。

行为建议：

- 可用：`tts/stop/pause/set_volume/probe`
- 受限：依赖云下发的 `play_url`（可返回队列待执行）
- UI/API 明确标记模式：`mode=offline_degraded`

运行策略：

- 周期性尝试恢复 cloud（低频）
- 离线期间不进行高频 cloud 重试

---

## 9. 错误恢复机制

## 9.1 Token 恢复

- token 读取失败：尝试 backup 文件
- refresh 失败：指数退避 + 熔断
- 长时间失败：触发人工登录提示（不阻塞本地能力）

## 9.2 设备恢复

- 设备控制失败时更新设备健康分
- 定时 `probe` 恢复 `online/capability`
- `DeviceRegistry` 成功同步后自动修复映射

## 9.3 会话恢复

- `PlaySession` 记录 `stage/error/retriable`
- 可重试错误进入重试队列（上限 + 抖动）
- 不可重试立即失败并返回可读错误

---

## 10. 推荐实现方式（工程落地）

目录建议：

```text
xiaomusic/
  cloud/
    session_manager.py
    backoff.py
  registry/
    device_registry.py
  cache/
    metadata_cache.py
  transport/
    router.py
    mina.py
    miio.py
  core/
    coordinator.py
    session_store.py
```

关键实践：

- 所有 cloud 调用统一经过 `CloudSessionManager`
- 所有设备查询统一经过 `DeviceRegistry`
- 所有 URL/metadata 统一经过 `MetadataCache`
- 所有控制命令统一经过 `TransportRouter`

---

## 11. 观测与告警建议

核心指标：

- `cloud_ready`（0/1）
- `cloud_call_qps`, `cloud_call_error_rate`
- `device_registry_hit_rate`
- `miio_success_rate`, `mina_success_rate`
- `offline_mode_duration_sec`

关键日志字段：

- `request_id`, `session_id`, `did`, `transport`, `error_code`, `stage`

---

## 12. 外部资料参考

- `micloud` 项目：用于 Xiaomi cloud 登录与设备信息获取思路  
  `https://github.com/Squachen/micloud`
- `python-miio` 文档：局域网发现、token、设备控制能力  
  `https://python-miio.readthedocs.io/en/latest/`

> 备注：不同小爱型号对 MiIO/MIoT 控制能力差异较大，建议在 `probe` 中动态学习能力并写回 `DeviceCapability`，避免静态写死型号矩阵。
