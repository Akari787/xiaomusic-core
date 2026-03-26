# 认证系统架构

## 1. 认证状态分层

认证状态分为两层：

### 长期态

```
passToken, psecurity, ssecurity, userId, cUserId, deviceId
```

- 生命周期较长
- 持久化到 `conf/auth.json`
- 用于重建短期态

### 短期态

```
serviceToken, yetAnotherServiceToken
```

- 生命周期短
- 直接参与业务 API 调用
- 失效时系统可从长期态重建

---

## 2. auth.json 持久化

`conf/auth.json` 是认证状态的事实来源。

```json
{
  "userId": "...",
  "cUserId": "...",
  "deviceId": "...",
  "passToken": "...",
  "psecurity": "...",
  "ssecurity": "...",
  "serviceToken": "...",
  "yetAnotherServiceToken": "..."
}
```

- 字段说明：
  - `userId` / `cUserId` / `deviceId`：账号与设备关联标识
  - `passToken` / `psecurity` / `ssecurity`：长期认证材料
  - `serviceToken` / `yetAnotherServiceToken`：短期业务会话材料

- 持久化原因：
  - 进程重启后可恢复
  - 短期态失效后可从磁盘重建
  - runtime rebind 依赖完整的认证材料

---

## 3. 恢复链路

短期会话失效时的标准恢复链：

```
verify runtime session
  -> reload disk auth (auth.json)
  -> persistent-auth login
  -> runtime rebind
  -> verify
```

### 3.1 verify runtime session

验证当前 runtime 中的认证状态是否可用：

- 调用 `device_list`
- 判断 `runtime_auth_ready`

### 3.2 reload disk auth

从 `auth.json` 重新加载认证材料。

### 3.3 persistent-auth login

使用长期认证材料重建短期 service cookies / token：

- 依赖 `passToken`、`psecurity`、`ssecurity`
- 针对 `sid=micoapi` 重建会话

### 3.4 runtime rebind

重建运行时服务对象：

- 重新初始化 `mina_service`
- 重新初始化 `miio_service`
- 刷新 device map

### 3.5 verify

验证恢复成功标准：

- `runtime_auth_ready=true`
- 设备列表可用

---

## 4. Locked 策略

`auth locked` 是终态保护状态，仅在长期认证材料缺失或等价硬故障时触发。

以下情况不应直接进入 locked：

- 短期会话失效
- 单次 runtime verify 失败

---

## 5. 调试接口

| 接口 | 用途 |
|------|------|
| `/api/v1/debug/auth_state` | 整体认证模式、是否 locked、最近错误 |
| `/api/v1/debug/auth_recovery_state` | 恢复链关键状态流转 |
| `/api/v1/debug/miaccount_login_trace` | 登录输入快照、响应解析、token 写回 |
| `/api/v1/debug/auth_runtime_reload_state` | 运行时重载各阶段状态 |
| `/api/v1/debug/auth_short_session_rebuild_state` | 短期态重建与 rebind 状态 |

可观测事件：`auth_runtime_reload`，包含各阶段结果。

---

## 6. 当前限制

- Xiaomi 服务端风控无法彻底消除
- 系统目标不是"零失败"，而是"低频失败、可恢复、可预测"
- 长期态（`passToken` 等）缺失时仍需重新扫码
- 网络环境（DNS、代理、风控）可能影响恢复
