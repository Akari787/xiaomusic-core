# 一、实施目标

> 历史实施计划，记录的是 v1 API 收口过程中的阶段性设计。
> 文中如出现旧接口（例如 `/api/v1/play_url`）或已完成/已调整的实现方案，仅用于回溯，不代表当前正式 API。

API 收口阶段的目标只有三个：

### 1️⃣ 统一播放入口

所有播放必须进入：

```
POST /api/v1/play
```

旧接口只能作为 wrapper。

---

### 2️⃣ 统一响应结构

所有 API 返回：

```json
{
  "code": 0,
  "message": "ok",
  "data": {},
  "request_id": ""
}
```

---

### 3️⃣ WebUI 只调用 v1 API

WebUI 禁止调用：

* runtime 内部函数
* 插件逻辑
* transport

---

# 二、实施范围

本阶段允许修改：

```
xiaomusic/api
xiaomusic/core
xiaomusic/playback
```

不允许修改：

```
plugins/source
plugins/transport
```

插件系统在本阶段不动。

---

# 三、实施任务

实施任务分为 6 个步骤。

---

# Step 1

# 新增统一请求模型

新增数据模型：

```
PlayRequest
```

位置建议：

```
xiaomusic/api/models/play_request.py
```

结构：

```python
class PlayRequest:
    device_id: str
    query: str
    source_hint: str = "auto"
    options: dict = {}
```

---

# Step 2

# 新增统一响应模型

新增：

```
ApiResponse
```

位置：

```
xiaomusic/api/models/response.py
```

结构：

```python
class ApiResponse:
    code: int
    message: str
    data: dict
    request_id: str
```

所有 API 必须返回该结构。

---

# Step 3

# 实现 /api/v1/play

该接口必须：

1️⃣ 接收 PlayRequest
2️⃣ 调用 PlaybackCoordinator
3️⃣ 返回 ApiResponse

调用链：

```
API
 ↓
PlaybackCoordinator
 ↓
SourcePlugin
 ↓
DeliveryAdapter
 ↓
Transport
```

---

# Step 4

# 旧接口改为 wrapper

例如：

```
/api/v1/play_url
```

修改为：

```
内部调用 /api/v1/play
```

示例逻辑：

```python
return play(
    device_id=device_id,
    query=url,
    source_hint="direct_url"
)
```

---

# Step 5

# 新增 /api/v1/resolve

该接口：

**只解析来源，不播放**

流程：

```
resolve
 ↓
返回 ResolvedMedia
```

用于：

* WebUI 测试
* 调试插件

---

# Step 6

# API 错误统一处理

新增：

```
ApiError
```

所有异常必须转换为：

```
ApiResponse
```

不允许直接抛出 stack trace。

---

# 四、WebUI 调整

WebUI 必须：

统一调用：

```
POST /api/v1/play
```

测试按钮：

```
POST /api/v1/resolve
```

不允许：

* 调 play_url
* 调内部接口

---

# 五、测试要求

API 收口必须通过：

## 单元测试

测试：

```
play
resolve
control
```

---

## 集成测试

验证：

* Jellyfin 播放
* Direct URL 播放
* SiteMedia 播放

---

## 实机测试

必须在测试服务器验证：

* Xiaomi Speaker 播放成功

测试服务器信息：

```
D:\AI\文档\服务器登录速查.md
```

---

# 六、验收标准

API 收口完成必须满足：

1️⃣ 所有播放入口统一为 `/api/v1/play`

2️⃣ 所有 API 返回统一结构

3️⃣ WebUI 不再调用旧接口

4️⃣ 旧接口全部变为 wrapper

5️⃣ 新增 `/api/v1/resolve`

6️⃣ 测试服务器播放正常

---

# 七、实施顺序

严格按顺序：

```
1 新增请求模型
2 新增响应模型
3 实现 play API
4 旧接口 wrapper
5 实现 resolve API
6 WebUI 调整
7 测试
```
