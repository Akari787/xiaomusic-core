# ADR-0001: API 作为唯一正式边界

## 状态：已接受

## 上下文

xiaomusic-core 存在多个层次的组件：WebUI、API层（routers）、业务逻辑层（xiaomusic.xiaomusic）、设备控制层（device_player）、播放协调层（PlaybackCoordinator、RelayRuntime）。

当前问题是：**WebUI 是否可以直接访问 runtime 内部状态？**

在审计中发现：
- `SiteMediaSourcePlugin` 通过 `runtime_provider` 间接持有 `RelayRuntime` 引用
- `RelayRuntime` 持有 `xiaomusic` 引用，可访问任意模块
- `device_player` 直接持有 `xiaomusic` 引用
- 部分 debug 端点（`/api/v1/debug/*`）暴露内部状态

如果不加约束，WebUI 可以绕过 API 直接操作内部状态，导致：
1. 状态同步机制失效（API 不知道 WebUI 的直接修改）
2. API 契约被破坏（同样的状态可以通过 API 读、通过内部调用改）
3. 难以追踪状态变化来源
4. 测试难度增加（需要 mock 多个内部模块而非只 mock API）

## 决策

**所有外部访问（WebUI、CLI、外部系统）必须通过 API 进行，不得直接访问 runtime 内部状态。**

具体规定：
1. **WebUI 只通过 `services/` 中的 API 调用访问后端**，不直接 import 任何 `xiaomusic.*` 模块
2. **API 层内部可以使用 runtime_provider**，但 runtime_provider 本身不暴露给 WebUI
3. **所有 API 端点必须使用 `ApiError` 结构化返回**，不得返回裸错误
4. **debug 端点（`/api/v1/debug/*`）标记为 `include_in_schema=False`**，生产环境应添加 admin 认证
5. **`services/` 层只调用正式 API**，不调用 debug 端点

## 后果

### 正面
- 状态变化路径唯一可追踪
- API 契约清晰，边界稳定
- WebUI 和后端解耦，可以独立演进
- 测试时可以 mock API 而不需要 mock 内部模块

### 负面
- 性能开销（通过 HTTP 而非函数调用）
- 某些场景（如内部进程调试）需要额外的调试 API
- `runtime_provider` 的使用范围需要明确界定（API 层内部使用，不外泄）

## 合规检查

提交前自检：
- [ ] WebUI 是否只通过 `services/` 访问后端？
- [ ] 新增 API 是否使用 `ApiError` 返回错误？
- [ ] 是否有代码绕过 API 直接修改 runtime 状态？