# 小米妙播（MiPlay）本地推流实验记录

日期：2026-11-01
目标：验证「不经小米云、在局域网内把音频 URL/流交给音箱」是否可行，用以绕开 passToken 每 ~84 小时失效的问题。

## 结论摘要

| 结论 | 证据强度 | 说明 |
|---|---|---|
| 妙播发现层可用 | 强 | mDNS `_lyra-mdns._udp` / `_mi-connect._udp` 查询带 QU 位（qclass `0x8001`）时，音箱正常应答 PTR/SRV/TXT/A/RR |
| 控制通道 8899 可用 | 强 | TCP 连接后音箱主动下发 DEVICE_ID 帧 |
| 鉴权无证书、无账号 | 强 | `auth_key = md5(map(sip+sp+cip+cp))`，纯 IP/端口推导；HMAC 校验通过即建立安全通道 |
| 发送端可完整推流 | 强 | 旧手机（FusionPlay 接收端）成功播放本实现推送的 60s AAC 正弦波（UI 显示 Test Tone 1:00/1:00） |
| OH2P 接受 wrapper OPEN | 强 | 回 `{"result":"0"}`，但随后不发起 RTSP |
| OH2P 拒绝 raw OPEN | 强 | 直接断开控制连接 |
| OH2P 额外校验内容 | 未知 | `SET_PLAY_SOURCE`/`SET_MEDIA_INFO`/`GET_DEVICE_INFO` 均静默吞掉 |

## 协议结构

### 1. 发现（mDNS）

- 服务类型：`_lyra-mdns._udp.local`（Lyra）、`_mi-connect._udp.local`
- **关键**：查询的 qclass 必须带 QU 位（`0x8001`），否则音箱不应答
- 应答样例（OH2P）：
  - `PTR _lyra-mdns._udp.local → 207A13E0._lyra-mdns._udp.local`
  - `SRV → 207A13E0.local:5353`
  - `TXT AppData=...`（含 device_id / mac / 名称）
  - `A → 192.168.7.125`
- `_mi-connect` SRV 指向 `:56666`（UDP 发现端口），TXT 含 `idHash/dev/sec/version/apps`

### 2. 控制通道（TCP 8899）

帧头 9 字节：

```
'$'(0x24) | outer_type:u8 | command:u8 | value_type:u16(be) | length:u32(be)
```

- `outer_type`：`0x00` = raw，`0x14` = wrapper 命名空间
- 加密帧 body 以 `00 07 01 e0` 开头，后接 `pad:u8 | integrity:u32(be) | ciphertext`

加密：

- AES-128-CBC，key 与初始 IV 均取 `auth_key` 前 16 字节（ASCII）
- 零填充（非 PKCS#7），pad 值写入帧头
- IV 链式：下一帧 IV = 上一帧密文末 16 字节
- integrity = 自定义 CRC32（多项式 `0x04c11db7`，表项字节交换，初值 `0xffffffff`，无最终异或）

`auth_key` 推导：

```
value  = 服务端IP + 服务端端口 + 客户端IP + 客户端端口   （字符串直接拼接）
mapped = 每个 ASCII 数字字符 +0x31（'0'→'a' … '9'→'j'）
auth_key = hex(md5(mapped))    # 32 hex 字符，取前 16 字节作 AES key
```

> 顺序必须是**服务端在前**，反过来密钥不匹配。

wrapper 包装（outer_type=0x14）：

```
key_len:u8 | key | data_type:u32(le, 固定30) | data_len:u8 | data
```

### 3. 鉴权流程（实测通过网络）

```
S→C  DEVICE_ID  (0x28)   body = 16 位数字字符串
C→S  SAFETY_INFO (0x00)  outer=0x14, 明文 wrap("cmd", 加密参数JSON)
S→C  SAFETY_INFO_ACK (0x01) outer=0x14, 明文 wrap("ack", {...aesIvType:2...})
S→C  SAFETY_AUTH (0x02)  outer=0x14, 加密 wrap("cmd", {"authMsg": <challenge>})
C→S  SAFETY_AUTH (0x02)  outer=0x14, 加密 wrap("cmd", 自己的 challenge)
C→S  SAFETY_AUTH_ACK (0x03) outer=0x14, 加密 wrap("ack", {"authMsgAck": hmac})
S→C  SAFETY_AUTH_ACK (0x03) outer=0x14, 加密
```

`hmac = hmac_sha256(auth_key_ascii_bytes, peer_challenge_ascii)`

安全通道建立后**无任何设备证书校验**，`vendor_attestation` 概念在该层不存在。

### 4. 命令表（关键部分）

| 命令 | 值 | 方向 |
|---|---|---|
| DEVICE_ID | 0x28 | S→C |
| AUTH_ACK | 0x29 | C→S |
| SAFETY_INFO / ACK | 0x00 / 0x01 | C→S / S→C |
| SAFETY_AUTH / ACK | 0x02 / 0x03 | 双向 |
| GET_DEVICE_INFO / ACK | 0x1e / 0x1f | C→S / S→C |
| SET_DEVICE_INFO / ACK | 0x58 / 0x59 | C→S / S→C |
| OPEN / ACK | 0x00 / 0x01 | C→S / S→C |
| SET_MEDIA_INFO / ACK | 0x12 / 0x13 | C→S / S→C |
| SET_VOLUME / ACK | 0x0c / 0x0d | 双向 |
| PAUSE/RESUME | 0x04/0x06 | 双向 |
| NOTIFY | 0x22 | S→C |
| SET_PLAY_SOURCE / ACK | 0x40 / 0x41 | C→S / S→C |
| SET_MIRROR_KEY / ACK | 0x6c / 0x6d | C→S / S→C |

### 5. 媒体会话

`OPEN` 的 body（加密后直接是字符串，**raw，不带 wrapper**）：

```
wfd://<发送端IP>:<RTSP端口>?mirrorMode=1
```

接收端收到后**主动连接该地址**，建立 3 条 TCP（control / image / multi）。

RTSP 交互（发送端是 server）：

```
C→S? 发送端先发: OPTIONS * RTSP/1.0 (CSeq 1)
接收端回 200 并发 OPTIONS *
发送端回 200，然后发 GET_PARAMETER（wfd_audio_codecs_v2）
接收端回 200 + 能力:
    wfd_audio_codecs_v2: 15 3 3
    wfd_client_rtp_ports: RTP/AVP/TCP;interleaved mode=play
收到 SET_PARAMETER(wfd_trigger_method: SETUP) 后接收端发 SETUP（带 MultiPort: image_port=..;multi_port=..）
发送端回 200 + Session + Transport
接收端发 PLAY，发送端回 200，开始推流
```

> 实测 FusionPlay 会跳过 SETUP 直接 PLAY；媒体端口可用"最后建立的数据连接"兜底。

媒体格式：

- RTP over TCP，独立数据连接上发**裸 RTP**（无 interleaved 前缀）
- RTP 头 12 字节，PT=33（MP2T），时钟 90000 Hz
- payload = MPEG-TS（每包 7×188 = 1316 字节）
- 内含 AAC-LC 48000 Hz 立体声，每帧 1024 样本 → 时间戳步进 1920

## 实测记录

### 对 FusionPlay（旧手机，192.168.7.164）

```
SET_PLAY_SOURCE → 0x41 ACK
SET_MEDIA_INFO  → 0x13 ACK
OPEN (raw)      → 0x01 OPEN_ACK [0,0,0,0,0]
RTSP            3 条连接建立，OPTIONS/GET_PARAMETER/PLAY 全通
推流            RTP/MPEG-TS 1316B × 921 包，60s
结果            旧手机播放出「Test Tone」，进度 1:00/1:00
```

### 对 OH2P（192.168.7.125）

```
SET_PLAY_SOURCE → 无响应
SET_MEDIA_INFO  → 无响应
GET_DEVICE_INFO → 无响应
OPEN (raw)      → 连接被断开
OPEN (wrapper)  → 0x01 ACK {"result":"0"}，随后无 RTSP 连接，40s 后断开
```

## 未解决的问题

音箱 OH2P 在 OPEN 之后还有一层校验，FusionPlay 没有。可能方向：

1. 要求 SET_DEVICE_INFO 用 TLV 格式（`00 01 55` 开头 + key/value 表）而非 JSON
2. 要求先 SET_MIRROR_KEY 下发媒体密钥
3. 发送端身份需与米家账号关联（通过云端预登记）
4. OPEN 的 URL 参数有其他要求

**下一步**：抓一次小米 12S → OH2P 的真实投送流量（需 ARP 欺骗，将音箱到手机的流量引至 192.168.7.178 抓取），对照本实现逐帧比较。

## 复现资产

- `send1.py`：发送端完整实现（控制通道 + RTSP server + RTP 推流）
  - 用法：`python3 send1.py <接收端IP>`
  - 依赖：`cryptography`
  - 测试音频：`/tmp/test.ts`（60s AAC/TS，由容器内 ffmpeg 生成）
- 关键探针：`probe_lyra_exact.py`（QU 位发现）、`play10.py`（鉴权 + 帧解析）
