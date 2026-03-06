# Transport Layer 设计（Python 音频控制系统）

本文给出一个可执行的 Transport Layer 方案，用于统一控制小米小爱音箱。  
目标是让 Core 仅调用 transport 抽象，而不直接依赖 MiNA Cloud 或 MiIO 局域网实现。

---

## 0. 设计背景与约束

当前已有两种控制路径：

1. Xiaomi MiNA Cloud API（云端控制）
2. MiIO/MIoT 局域网协议（本地控制）

未来可扩展第三种或更多 transport（例如 MQTT bridge、厂商新协议）。

Transport 层统一提供：

- `play_url`
- `tts`
- `stop`
- `pause`
- `set_volume`
- `probe`

---

## 1) Transport 接口设计

### 1.1 核心对象模型

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TransportName(str, Enum):
    MINA = "mina"
    MIIO = "miio"


class CommandType(str, Enum):
    PLAY_URL = "play_url"
    TTS = "tts"
    STOP = "stop"
    PAUSE = "pause"
    SET_VOLUME = "set_volume"
    PROBE = "probe"


@dataclass
class DeviceRef:
    did: str
    device_id: str = ""
    ip: str = ""
    miio_token: str = ""
    model: str = ""


@dataclass
class TransportRequest:
    command: CommandType
    device: DeviceRef
    payload: dict[str, Any] = field(default_factory=dict)
    timeout_sec: float = 8.0
    request_id: str = ""


@dataclass
class TransportResult:
    ok: bool
    transport: TransportName
    command: CommandType
    error_code: str | None = None
    message: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0


@dataclass
class ProbeResult:
    reachable: bool
    latency_ms: int | None = None
    details: dict[str, Any] = field(default_factory=dict)
```

### 1.2 BaseTransport 抽象接口

```python
from abc import ABC, abstractmethod


class BaseTransport(ABC):
    """所有 transport 的统一抽象。"""

    name: TransportName

    @abstractmethod
    async def play_url(self, req: TransportRequest) -> TransportResult: ...

    @abstractmethod
    async def tts(self, req: TransportRequest) -> TransportResult: ...

    @abstractmethod
    async def stop(self, req: TransportRequest) -> TransportResult: ...

    @abstractmethod
    async def pause(self, req: TransportRequest) -> TransportResult: ...

    @abstractmethod
    async def set_volume(self, req: TransportRequest) -> TransportResult: ...

    @abstractmethod
    async def probe(self, req: TransportRequest) -> ProbeResult: ...

    @abstractmethod
    async def health(self) -> dict[str, Any]: ...
```

---

## 2) Transport 能力声明 + DeviceCapability 模型

### 2.1 能力声明（transport 维度）

```python
@dataclass(frozen=True)
class TransportCapability:
    can_play_url: bool = False
    can_tts: bool = False
    can_stop: bool = False
    can_pause: bool = False
    can_set_volume: bool = False
    needs_cloud_auth: bool = False
    needs_lan_ip_token: bool = False
    supports_live_stream: bool = True
```

### 2.2 DeviceCapability（设备维度）

```python
@dataclass
class DeviceCapability:
    did: str
    # 可用 transport
    mina_available: bool = True
    miio_available: bool = False

    # 命令级能力（用于 router）
    can_play_via_mina: bool = True
    can_play_via_miio: bool = False
    can_tts_via_miio: bool = True
    can_stop_via_miio: bool = True
    can_pause_via_miio: bool = True
    can_set_volume_via_miio: bool = True

    # 运行时健康分（0-100）
    mina_health_score: int = 80
    miio_health_score: int = 50

    # 最近探测结果
    last_probe_ts: int = 0
```

> 说明：
> - `TransportCapability` 描述“某类 transport 天生能做什么”。
> - `DeviceCapability` 描述“某个设备在当前环境下能通过哪条 transport 做什么”。

---

## 3) MinaTransport 设计

```python
class MinaTransport(BaseTransport):
    name = TransportName.MINA
    capability = TransportCapability(
        can_play_url=True,
        can_tts=True,
        can_stop=True,
        can_pause=True,
        can_set_volume=True,
        needs_cloud_auth=True,
    )

    def __init__(self, auth_manager, mina_client, logger):
        self._auth = auth_manager
        self._client = mina_client
        self._log = logger

    async def play_url(self, req: TransportRequest) -> TransportResult:
        # payload: {"url": "..."}
        ...

    async def tts(self, req: TransportRequest) -> TransportResult:
        # payload: {"text": "..."}
        ...

    async def stop(self, req: TransportRequest) -> TransportResult:
        ...

    async def pause(self, req: TransportRequest) -> TransportResult:
        ...

    async def set_volume(self, req: TransportRequest) -> TransportResult:
        # payload: {"volume": 0~100}
        ...

    async def probe(self, req: TransportRequest) -> ProbeResult:
        # 可用 auth ready + 轻量接口探测
        ...

    async def health(self) -> dict[str, Any]:
        return {"auth_ready": True, "transport": "mina"}
```

要点：
- Mina 对 `play_url` 通常最稳定（尤其公网 URL / 需要云端下发场景）。
- `probe` 不应调用重负载接口，避免放大云依赖。

---

## 4) MiioTransport 设计

```python
class MiioTransport(BaseTransport):
    name = TransportName.MIIO
    capability = TransportCapability(
        can_play_url=False,  # 默认保守，部分设备可能可扩展
        can_tts=True,
        can_stop=True,
        can_pause=True,
        can_set_volume=True,
        needs_lan_ip_token=True,
    )

    def __init__(self, miio_client_factory, logger):
        self._factory = miio_client_factory
        self._log = logger

    async def play_url(self, req: TransportRequest) -> TransportResult:
        return TransportResult(
            ok=False,
            transport=self.name,
            command=req.command,
            error_code="E_TRANSPORT_NOT_SUPPORTED",
            message="miio transport does not support play_url for this model",
        )

    async def tts(self, req: TransportRequest) -> TransportResult:
        ...

    async def stop(self, req: TransportRequest) -> TransportResult:
        ...

    async def pause(self, req: TransportRequest) -> TransportResult:
        ...

    async def set_volume(self, req: TransportRequest) -> TransportResult:
        ...

    async def probe(self, req: TransportRequest) -> ProbeResult:
        # 基于 ip/token 做本地探测，超时要短
        ...

    async def health(self) -> dict[str, Any]:
        return {"transport": "miio"}
```

要点：
- MiIO 更适合本地实时控制（`tts/stop/pause/volume`）。
- 局域网不通时应快速失败并让 router fallback。

---

## 5) Transport Router 设计

### 5.1 Router 职责

- 根据 `CommandType + DeviceCapability + transport 健康状态` 选择主 transport
- 执行 fallback
- 输出统一 `TransportResult`

### 5.2 优先级策略（默认）

| Command | Primary | Fallback |
|---|---|---|
| play_url | Mina | Miio(若设备声明支持) |
| tts | Miio | Mina |
| stop | Miio | Mina |
| pause | Miio | Mina |
| set_volume | Miio | Mina |
| probe | Miio + Mina 并行可选 | 无 |

### 5.3 Router 代码骨架

```python
class TransportRouter:
    def __init__(self, mina: BaseTransport, miio: BaseTransport, state_store, logger):
        self.mina = mina
        self.miio = miio
        self.state = state_store
        self.log = logger

    async def dispatch(self, req: TransportRequest, cap: DeviceCapability) -> TransportResult:
        ordered = self._select_order(req.command, cap)
        last_err: TransportResult | None = None

        for t in ordered:
            if not await self._transport_ready(t, req, cap):
                continue
            result = await self._call(t, req)
            if result.ok:
                self._record_success(t, req.command)
                return result
            self._record_failure(t, req.command, result)
            last_err = result
            if not self._is_retriable(result):
                break

        return last_err or TransportResult(
            ok=False,
            transport=TransportName.MINA,
            command=req.command,
            error_code="E_TRANSPORT_ALL_FAILED",
            message="no available transport",
        )

    def _select_order(self, cmd: CommandType, cap: DeviceCapability) -> list[BaseTransport]:
        if cmd == CommandType.PLAY_URL:
            out = [self.mina]
            if cap.can_play_via_miio:
                out.append(self.miio)
            return out
        # control 命令优先本地
        return [self.miio, self.mina]
```

---

## 6) Fallback 策略（细化）

### 6.1 可重试错误（触发 fallback）

- `E_TIMEOUT`
- `E_NETWORK_UNREACHABLE`
- `E_CLOUD_TEMP_UNAVAILABLE`
- `E_DEVICE_OFFLINE_TRANSIENT`

### 6.2 不可重试错误（直接失败）

- `E_BAD_REQUEST`
- `E_INVALID_URL`
- `E_UNAUTHORIZED_PERMANENT`
- `E_DEVICE_NOT_FOUND`

### 6.3 命令级 fallback 规则

- `play_url`：默认 Mina -> Miio（仅设备明确支持）
- `tts/stop/pause/set_volume`：Miio -> Mina
- `probe`：可并行探测，结果合并为 `data={"mina":...,"miio":...}`

---

## 7) Transport 健康检查

### 7.1 全局健康

- `MinaTransport.health()`：认证状态、最近失败率、熔断状态
- `MiioTransport.health()`：局域网探测成功率、平均延迟、token 缺失数

### 7.2 设备级健康

- `probe(device)` 返回 reachability
- 更新 `DeviceCapability.mina_health_score / miio_health_score`
- Router 选择时可动态降权高失败 transport

### 7.3 建议阈值

- 连续失败 >= 5 次：开启短熔断（30s）
- 熔断期间仅允许每 10s 半开探测 1 次

---

## 8) Transport 错误处理

### 8.1 标准错误码

```text
E_TRANSPORT_NOT_SUPPORTED
E_TRANSPORT_ALL_FAILED
E_TIMEOUT
E_NETWORK_UNREACHABLE
E_CLOUD_TEMP_UNAVAILABLE
E_DEVICE_OFFLINE
E_UNAUTHORIZED
E_BAD_REQUEST
```

### 8.2 统一异常包装

```python
class TransportError(Exception):
    def __init__(self, code: str, message: str, retriable: bool = False):
        self.code = code
        self.message = message
        self.retriable = retriable
        super().__init__(message)
```

每个 transport 内部捕获底层 SDK 异常并转换为统一错误码；Router 只基于统一错误做策略。

---

## 9) Transport 优先级策略

建议使用“静态策略 + 动态健康权重”混合模型。

### 9.1 静态策略

- `play_url`：Mina 优先（云端播放链路更稳定）
- 控制类（tts/stop/pause/volume）：Miio 优先（低延迟、低云依赖）

### 9.2 动态权重

计算分值：

```text
score = base_priority + health_score - recent_error_penalty - timeout_penalty
```

选择分值最高 transport 作为 primary，次高作为 fallback。

---

## 10) 调用流程示例

### 10.1 play_url

```text
Core.play_url()
  -> build TransportRequest(command=PLAY_URL)
  -> TransportRouter.dispatch(req, device_cap)
      -> select [Mina, Miio?]
      -> Mina.play_url(req)
         -> success -> return
         -> fail(retriable) -> try Miio if supported
      -> all fail -> E_TRANSPORT_ALL_FAILED
```

### 10.2 set_volume

```text
Core.set_volume()
  -> Router select [Miio, Mina]
  -> Miio.set_volume(req)
      -> success
      -> timeout -> fallback Mina.set_volume(req)
```

### 10.3 probe

```text
Core.probe_device()
  -> parallel: Mina.probe + Miio.probe
  -> merge result to device health
  -> update DeviceCapability
```

---

## 11) 建议文件结构

```text
xiaomusic/
  transport/
    contracts.py        # BaseTransport / models / error codes
    base.py             # shared helper, timeout/retry wrapper
    mina.py             # MinaTransport
    miio.py             # MiioTransport
    router.py           # TransportRouter
    health.py           # circuit breaker / health score
    registry.py         # future transport registry
```

---

## 12) 参考资料（用于参数与约束判断）

- python-miio 文档：`https://python-miio.readthedocs.io/en/latest/`
- python-miio 项目：`https://github.com/rytilahti/python-miio`
- miIO 协议说明（python-miio README 引用）：`https://github.com/OpenMiHome/mihome-binary-protocol/blob/master/doc/PROTOCOL.md`

> 说明：小爱音箱不同型号在 MiIO/MIoT 命令支持上存在差异，建议在 `probe` 阶段动态学习能力并写入 `DeviceCapability`，不要写死型号矩阵。
