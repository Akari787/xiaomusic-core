# xiaomusic-core 认证系统架构文档

## 1. 文档目的

本文档用于说明 `xiaomusic-core` 当前认证系统的设计目标、运行机制、恢复链路与调试方法。

目标读者：

- 项目维护者
- 想贡献代码的开发者
- 需要理解认证机制的高级用户

本文档重点不是列举接口，而是解释：

- 为什么系统要这样设计
- 为什么项目不再把云端 refresh 作为主恢复机制
- 为什么扫码登录一次后，系统可以长期稳定运行

---

## 2. 项目背景

`xiaomusic-core` 的核心能力之一，是控制小米智能音箱并调用 Xiaomi 云端能力，通过 `miio / micoapi` 实现播放控制、状态获取与设备交互。

这类项目面临的根本问题，不是播放本身，而是认证链路不稳定。

典型历史故障链路如下：

```text
serviceToken 失效
  -> refresh 失败
  -> API 返回“刷新Token失败，请重新登录”
  -> 用户必须重新扫码
```

这会直接导致：

- 自动化系统无法长期运行
- Home Assistant 等集成频繁掉线
- 用户必须重复扫码，体验很差

因此，`xiaomusic-core` 的认证系统不是把“扫码登录”做得更花哨，而是把“扫码之后如何尽量不再扫码”作为核心工程目标。

---

## 3. 认证系统设计目标

### 3.1 稳定性

当短期 token 失效时，系统应优先自动恢复，而不是直接把故障暴露为“请重新扫码”。

目标是：

```text
token 失效时自动恢复
无需人工扫码
```

### 3.2 长期运行

系统必须支持：

```text
7x24 小时运行
```

也就是说，认证系统不能依赖短生命周期内存态，必须有稳定的磁盘持久化与运行时重建能力。

### 3.3 自愈能力

当认证状态部分损坏、短期态过期、运行时服务失效时，系统应优先走标准恢复链，而不是要求人工干预。

目标是：

```text
自动恢复
```

### 3.4 最小用户干预

用户理想情况下只需要：

```text
扫码登录一次
```

之后系统通过持久化认证材料、短期态重建与 runtime rebind 机制维持长期可用。

---

## 4. Xiaomi 认证机制分析

### 4.1 登录流程概览

项目当前依赖的 Xiaomi 认证流程，可抽象为：

```text
二维码登录
  -> 获得 passToken
  -> 获得 ssecurity / psecurity
  -> 生成 serviceToken
```

这些字段在项目里的意义并不完全相同。

#### `passToken`

- 代表登录后取得的关键认证材料之一
- 生命周期相对较长
- 可用于后续重建短期会话

#### `psecurity`

- 与登录态关联的长期安全字段
- 用于后续恢复认证链

#### `ssecurity`

- 同样属于关键安全材料
- 参与后续 session / service token 的构造或恢复

#### `serviceToken`

- 直接参与业务 API 调用
- 是调用 `micoapi` 等服务时最关键的短期会话 token
- 生命周期短，最容易过期或失效

#### `yetAnotherServiceToken`

- 可视为另一个短期 service cookie / token 形态
- 在运行时恢复与服务调用中同样重要

### 4.2 长期态与短期态划分

在 `xiaomusic-core` 中，认证状态被明确分成两层：

#### 长期态

```text
passToken
psecurity
ssecurity
userId
cUserId
deviceId
```

特点：

- 生命周期相对较长
- 可持久化保存
- 可用于重新生成短期态

#### 短期态

```text
serviceToken
yetAnotherServiceToken
```

特点：

- 生命周期短
- 直接决定 API 是否还能正常调用
- 一旦失效，运行时虽然“看起来还在线”，但实际业务请求会失败

---

## 5. token 生命周期与 refresh 机制问题

### 5.1 为什么 `serviceToken` 是核心风险点

`serviceToken` 是最直接影响业务可用性的字段。

它有几个明显特点：

- 生命周期较短
- 对云端接口调用高度敏感
- 一旦失效，设备控制、状态查询、播放投递等能力会迅速异常

所以在实际运行中，更常见的不是“长期态丢失”，而是：

```text
长期态仍在
短期态已失效
```

这意味着理论上系统应该能恢复，但前提是恢复策略设计正确。

### 5.2 为什么 refresh 不可靠

传统思路通常是：

```text
serviceToken 失效
  -> 调用 refresh
  -> 生成新 token
```

但在 Xiaomi 生态中，这条路并不稳定，常见失败原因包括：

- 风控策略触发
- cookie 链不完整
- session 已经部分过期
- Xiaomi 云端策略变化
- 请求环境（网络、UA、上下文）与官方客户端不一致

实际表现通常是：

```text
refresh API 返回失败
```

或者：

- 返回 401 / 认证失败
- 返回风控相关错误（如 `70016`）
- 没有生成新的可用短期 token

### 5.3 为什么 refresh 不能作为主恢复机制

`xiaomusic-core` 的设计结论是：

```text
refresh 不适合作为主恢复机制
```

原因不是它“完全不能用”，而是它不满足工程上的稳定性要求：

- 成功率不稳定
- 出错时往往直接把系统推进“必须重新扫码”状态
- 与长期运行目标冲突

因此，项目把 refresh 降级为辅助路径，而把“从持久化长期态重建短期态”作为主恢复方案。

---

## 6. 当前认证系统总体架构

当前系统采用的是“持久化长期态 + 短期态重建 + 运行时重绑定”的恢复架构。

核心恢复链可概括为：

```text
verify runtime session
  -> reload disk auth
  -> long-auth login
  -> refresh fallback
  -> runtime rebind
  -> verify
```

结合项目实现，实际语义更准确地写成：

```text
verify runtime session
  -> reload disk auth
  -> persistent-auth login
  -> refresh fallback
  -> runtime rebind
  -> verify
```

### 6.1 `verify runtime session`

先验证当前 runtime 中的认证状态是否还能用。

典型验证方式：

- 调用 `device_list`
- 判断 `runtime_auth_ready`
- 检查短期态是否仍能支撑 `micoapi` / `miio` 服务访问

### 6.2 `reload disk auth`

如果 runtime session 已不可用，第一步不是扫码，而是重新从磁盘加载 `auth.json`。

这样做的原因是：

- 内存态可能过期，但磁盘态仍然完整
- 登录后的长期态是可以持久化并重复利用的
- runtime 本身可能只是服务对象失效，而不是认证材料真的丢失

### 6.3 `long-auth login` / `persistent-auth login`

这里的含义不是重新扫码登录，而是：

- 利用已经保存的长期认证材料
- 重新构建短期 service cookies / token
- 尝试恢复可用的业务会话

### 6.4 `refresh fallback`

当主恢复路径无法直接完成时，可以尝试 refresh 作为辅助 fallback。

但它不是主链路，也不应被视为最可靠路径。

### 6.5 `runtime rebind`

即使磁盘 token 已恢复，也必须重建运行时服务对象，否则业务层仍会使用旧的失效 session。

这一步会重新绑定：

- `mina_service`
- `miio_service`
- device map

### 6.6 `verify`

最后必须重新验证，而不是假设恢复成功。

验证成功的标准包括：

- `runtime_auth_ready=true`
- 设备列表可用
- 核心 API 可恢复工作

---

## 7. 长期态与短期态设计

### 7.1 长期态

项目把以下字段定义为长期态：

```text
passToken
psecurity
ssecurity
userId
cUserId
deviceId
```

这些字段的特点是：

- 生命周期长于 `serviceToken`
- 可以持久化到磁盘
- 可用于在不扫码的情况下重建短期态

从系统设计上看，长期态是整个认证系统的“恢复资本”。

只要长期态还在，系统通常就不应该直接退化到“必须重新扫码”。

### 7.2 短期态

项目把以下字段定义为短期态：

```text
serviceToken
yetAnotherServiceToken
```

这些字段的特点是：

- 生命周期短
- 与云端接口调用强绑定
- 失效频率高
- 需要被周期性重建

因此，系统的核心恢复目标并不是“保护短期态永远不失效”，而是：

```text
短期态失效后，能否快速从长期态重建
```

---

## 8. `auth.json` 持久化设计

### 8.1 为什么必须持久化

项目使用：

```text
auth.json
```

保存认证状态。

原因非常直接：

- 进程内存不适合做长期认证状态唯一来源
- 容器重启、服务重启后必须能恢复
- 自动恢复链必须能以“磁盘状态”为起点重建 runtime

如果没有 `auth.json`，系统每次进程丢失 runtime session 后都可能退化为重新扫码。

### 8.2 为什么要写回 `serviceToken`

表面上看，`serviceToken` 是短期态，似乎“不值得持久化”。

但工程上仍然必须写回，原因是：

- 进程重启后可直接尝试复用已有短期态
- 若短期态仍可用，可避免额外恢复动作
- 若短期态已失效，系统仍能从同一个文件继续走长期态恢复链

因此 `auth.json` 不是只存长期态，而是存“当前可恢复所需的完整认证材料”。

### 8.3 为什么重启后仍可恢复

因为系统把 `auth.json` 作为 source of truth：

```text
runtime 丢失
  -> 从 auth.json 重载认证材料
  -> 重建短期态 / runtime 服务
  -> 恢复业务能力
```

### 8.4 `auth.json` 示例结构

```json
{
  "userId": "123456789",
  "cUserId": "abcdefg",
  "deviceId": "hijklmn",
  "passToken": "pass_token_value",
  "psecurity": "psecurity_value",
  "ssecurity": "ssecurity_value",
  "serviceToken": "service_token_value",
  "yetAnotherServiceToken": "another_service_token_value"
}
```

字段说明：

- `userId` / `cUserId` / `deviceId`：账号与设备关联标识
- `passToken`：长期认证关键字段
- `psecurity` / `ssecurity`：长期安全材料
- `serviceToken` / `yetAnotherServiceToken`：短期业务会话材料

---

## 9. 自动恢复机制

这是整个系统的核心。

恢复链可以概括为：

```text
serviceToken 失效
  -> verify 失败
  -> 尝试 reload disk auth
  -> 尝试 persistent_auth_login
  -> 生成新 serviceToken
  -> 写回 auth.json
  -> runtime session 恢复
```

在当前项目实现语义下，更准确地说是：

```text
serviceToken 失效
  -> verify 失败
  -> reload disk auth
  -> persistent_auth_login
  -> 生成新 serviceToken
  -> 写回 auth.json
  -> runtime rebind
  -> verify
```

### 9.1 恢复触发点

恢复通常由以下事件触发：

- `device_list` 验证失败
- 认证调用出现 401 / 未授权
- 运行时状态判断为 degraded / locked / not ready

### 9.2 为什么无需扫码

因为扫码只负责第一次获得长期态。

系统后续恢复依赖的是：

- `auth.json` 中保存的长期认证材料
- 自动重建短期态
- 自动 runtime rebind

所以只要长期态未彻底损坏，通常不需要再次扫码。

---

## 10. `persistent_auth_login` 原理

### 10.1 它本质上在做什么

`miaccount_persistent_auth_login` 这类路径，本质上不是“重新完整登录一次”，而是：

- 使用长期 cookie / 长期认证材料
- 重新构造 service cookies
- 针对 `sid=micoapi` 重建会话

在当前主线语义中，这类能力被归入：

```text
persistent_auth_login
```

### 10.2 为什么它比 refresh 更稳定

因为它依赖的是已经持久化并验证过的长期态，而不是高度依赖云端当前 session 条件的 refresh 交换。

可以理解为：

- refresh：尝试在“当前云端会话”上继续续命
- persistent_auth_login：用“已持久化的长期材料”重新建一条短期会话

后者更符合可恢复系统设计。

### 10.3 为什么要指定 `sid=micoapi`

因为项目核心业务控制面主要依赖 `micoapi` 对应的小米服务会话。

恢复时直接围绕该 sid 重建 service cookies，更符合业务目标，也减少无关链路干扰。

---

## 11. Runtime Rebind

### 11.1 为什么恢复 token 后还不够

恢复 token 只是恢复了“数据状态”，但业务代码使用的是运行时服务对象。

如果这些对象仍然持有旧 session，那么：

- 即使 `auth.json` 已更新
- 即使短期 token 已恢复
- 业务调用仍可能继续失败

所以恢复链必须包含：

```text
runtime rebind
```

### 11.2 rebind 包含什么

至少包括：

- 重新初始化 `mina_service`
- 重新初始化 `miio_service`
- 刷新 device map

### 11.3 设计原因

认证恢复是“状态恢复 + 对象恢复”的组合问题。

只恢复磁盘 token，不恢复 runtime service，是不完整的。

---

## 12. 调试接口与可观测性

为了让维护者能观察恢复链是否按预期执行，项目提供了多组调试接口。

### 12.1 认证状态

```text
/api/v1/debug/auth_state
```

用于查看整体认证模式、是否 locked、最近错误等高层状态。

### 12.2 认证恢复状态

```text
/api/v1/debug/auth_recovery_state
```

用于观察恢复链中的关键状态流转。

### 12.3 登录链路跟踪

```text
/api/v1/debug/miaccount_login_trace
```

用于查看登录输入快照、登录响应解析、token 写回、运行时 seed 等细粒度阶段。

### 12.4 运行时重载状态

```text
/api/v1/debug/auth_runtime_reload_state
```

重点观察：

- `token_store_reloaded`
- `disk_has_serviceToken`
- `disk_has_yetAnotherServiceToken`
- `runtime_seed_has_serviceToken`
- `mina_service_rebuilt`
- `miio_service_rebuilt`
- `device_map_refreshed`
- `verify_result`
- `error_code`
- `error_message`

### 12.5 短期态重建状态

```text
/api/v1/debug/auth_short_session_rebuild_state
```

用于观察：

- 最近一次短期态清理
- 最近一次短期态重建
- 最近一次 runtime rebind
- 最近一次 verify
- 最近一次认证恢复流决策

这些接口的意义不只是“排错”，而是把认证恢复设计显式化、可审计化。

---

## 13. 自动恢复验证方法

可以通过以下方式验证系统是否具备自动恢复能力。

### 13.1 手动破坏短期态

例如删除：

```text
serviceToken
yetAnotherServiceToken
```

### 13.2 重启服务

```text
重启服务
```

### 13.3 预期行为

系统应该：

```text
自动恢复
```

更具体地说，应表现为：

- runtime 从 `auth.json` 重新加载认证材料
- 尝试重建短期态
- 写回新的 `serviceToken`
- 完成 runtime rebind
- verify 成功
- 用户无需重新扫码

### 13.4 建议观察点

验证时建议同时观察：

- `auth.json` 内容是否被正确写回
- `/api/auth/status` 是否恢复正常
- `/api/v1/debug/auth_runtime_reload_state` 是否显示成功
- 日志中是否出现 `auth_runtime_reload` 与恢复链阶段信息

---

## 14. 已知限制

当前方案虽然显著改善了稳定性，但并不意味着完全没有边界。

### 14.1 Xiaomi API 可能变更

项目依赖的认证与 service cookie 行为来自对实际客户端行为的工程适配。

如果 Xiaomi 云端策略变化，恢复逻辑可能需要同步调整。

### 14.2 `persistent_auth_login` 依赖长期材料完整性

如果 `passToken`、`psecurity`、`ssecurity` 等长期态字段本身损坏或缺失，系统仍可能无法恢复，只能重新扫码。

### 14.3 网络环境会影响恢复

某些网络环境可能影响：

- 登录会话建立
- service token 重建
- 云端验证

例如：

- DNS 不稳定
- 代理链路干扰
- 被风控识别为异常来源

### 14.4 目标不是“零失败”，而是“可恢复”

该系统设计的目标不是让认证永不失败，而是：

```text
低频失败
可预测
可恢复
尽量不需要重新扫码
```

---

## 15. 未来改进方向

### 15.1 更完善的认证状态监控

例如增加：

- 认证状态面板
- 恢复次数统计
- 最近失败原因聚合

### 15.2 更完善的 debug 信息

目前已有较完整的 debug API，但仍可继续增强：

- 更清晰的阶段耗时
- 更细的错误分类
- 更明确的“为何进入 locked”解释

### 15.3 auth metrics

未来可以考虑加入认证相关指标：

- runtime reload 次数
- verify 失败率
- 短期态重建成功率
- 需要人工扫码的频率

### 15.4 更清晰的运维告警

例如：

- 连续恢复失败告警
- 长期态缺失告警
- locked 状态告警

---

## 16. 总结

`xiaomusic-core` 之所以不再需要频繁扫码登录，核心原因不是“云端 refresh 终于稳定了”，而是项目不再把 refresh 作为唯一希望。

系统的关键设计转变是：

```text
把扫码得到的长期认证材料持久化
把短期 token 视为可重建状态
把 runtime 服务视为可重绑定对象
```

最终形成一条可维护、可观测、可恢复的认证链路：

```text
短期态失效
  -> 从 auth.json 重载
  -> 用长期态重建短期态
  -> runtime rebind
  -> verify
```

这就是为什么该项目能够在大多数情况下做到：

```text
扫码登录一次
之后长期运行
尽量不再频繁扫码
```
