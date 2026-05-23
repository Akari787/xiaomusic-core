# AI Review Checklist - 提交前检查

> 版本：1.0
> 发布：2026-05-16
> 依据：系统宪法 constraints.md

本文档是提交 PR 前必须完成的检查清单。AI 或人工 Review 时逐项确认。

---

## 边界检查

### [ ] 是否破坏 API contract？

检查点：
- 修改了已有 API 响应字段（新增 OK，修改/删除需要 ADR）
- 修改了 API 请求参数
- 修改了 HTTP 状态码

```bash
# 自检命令
grep -n "@router\." xiaomusic/api/routers/v1.py
# 检查修改的端点是否改变了响应结构
```

### [ ] 是否新增 WebUI 绕过 API 直接访问 runtime？

检查点：
- WebUI/TypeScript 代码是否直接 import 了 `xiaomusic.*` 模块
- 是否通过 `runtime_provider` 获取了 runtime 实例

```bash
# 自检命令
grep -rn "from xiaomusic" webui/src/
grep -rn "runtime_provider" webui/src/
```

### [ ] 是否新增双向依赖？

检查点：
- 模块 A 引用模块 B，模块 B 也引用模块 A
- 常见模式：`xiaomusic` ↔ `device_manager` ↔ `device_player` → `xiaomusic`

```bash
# 自检：绘制 import 依赖图
python -c "
import ast, sys
# 简单检查循环依赖（需安装 depscan 或手动检查）
"
```

---

## 所有权检查

### [ ] 是否新增 runtime god object 行为？

检查点：
- `RelayRuntime` 或 `xiaomusic.xiaomusic` 是否承担了新职责（> 当前职责领域数）
- 是否有方法不属于该类的核心职责

**RelayRuntime 当前职责领域**：音频流服务、会话管理、解析器缓存、健康检查、策略路由（待拆分）

### [ ] 是否修改了某状态的权威归属（未更新 state-authority.md）？

检查点：
- 新增了对某状态的读写
- 跨模块直接修改了其他模块持有的状态

```bash
# 自检：检查直接跨模块状态修改
grep -rn "self\.xiaomusic\." xiaomusic/core/
# 确认这些访问是否通过接口而非直接访问
```

### [ ] 是否引入隐藏生命周期（async task 无明确 owner）？

检查点：
- `asyncio.create_task()` 调用是否有 owner 标注
- task 的取消和清理逻辑是否完整

```bash
# 自检命令
grep -rn "create_task\|ensure_future" xiaomusic/
# 检查每个 task 是否有 ._owner 标注
```

---

## 事件系统检查

### [ ] 是否绕过 event system 直接修改状态？

检查点：
- 是否在应该触发事件的场景中直接修改了状态
- 是否跳过了事件通知直接调用下游方法

```bash
# 自检：检查应该通过事件通知的场景
grep -rn "\.publish\|event_bus\|emit" xiaomusic/
# 确认状态变化都触发了事件
```

### [ ] 是否修改了 event schema（未走 ADR）？

检查点：
- 修改了已有事件的 payload 结构
- 新增了事件字段但没有更新 `_event_version`

---

## Source 系统检查

### [ ] 是否新增 source-specific hack（if source == "xxx"）？

检查点：
- 是否有 `if source == "xxx"` 或 `if source_hint == "xxx"` 判断
- Source 类型判断应通过 `SourceRegistry.get_plugin()` 统一分发

```bash
# 自检命令
grep -rn "if source ==" xiaomusic/
grep -rn "if source_hint ==" xiaomusic/
```

### [ ] 是否新增 hasattr 特判？

检查点：
- 是否有 `hasattr(source, ...)` 探测插件属性

```bash
# 自检命令
grep -rn "hasattr.*source" xiaomusic/
```

---

## 技术债检查

### [ ] 是否新增无标注的 silent except？

检查点：
- `except:` 或 `except Exception: pass` 没有日志记录
- 异常被静默捕获但没有任何处理

```bash
# 自检命令
grep -n "except:" xiaomusic/
grep -n "except Exception:" xiaomusic/ | grep -v "LOG\|log\|raise\|print\|return"
```

### [ ] 是否新增状态双写？

检查点：
- 同一份数据是否被两个模块同时持有
- `device_player._play_list` 和 `music_library.music_list` 的关系

```bash
# 自检：搜索可能的重复状态
grep -rn "_play_list\|music_list" xiaomusic/
```

### [ ] 是否有 TEMP-HACK 未标注？

检查点：
- 临时方案是否有完整的 `# TEMP-HACK:` 注释
- 注释是否包含 `reason`、`remove_after`、`related_issue`、`owner`

---

## 性能检查

### [ ] 是否有 N+1 查询问题？

检查点：
- 循环中是否有独立的数据库或 API 调用

### [ ] 是否有阻塞调用在 async 函数中？

检查点：
- `requests.get()` 而非 `httpx.AsyncClient`
- `time.sleep()` 而非 `asyncio.sleep()`

---

## 安全检查

### [ ] 是否引入了新的注入风险？

检查点：
- 用户输入是否经过验证
- 文件路径是否经过 `pathlib` 处理

### [ ] API 是否正确处理认证？

检查点：
- 需要认证的端点是否检查了用户身份

---

## 文件清单

提交前确认以下文件已更新（如有变更）：

- [ ] `docs/architecture/constraints.md`
- [ ] `docs/architecture/state-authority.md`
- [ ] `docs/adr/00XX-*.md`（如有新增 ADR）

---

## 快速自检命令

```bash
# 一键检查常见违规
echo "=== API contract 检查 ==="
grep -rn "raise ValueError\|raise Exception\|return {.*error" xiaomusic/api/

echo "=== silent except 检查 ==="
grep -rn "except:" xiaomusic/ | grep -v "LOG\|log\|raise\|print\|return\|#"

echo "=== source hack 检查 ==="
grep -rn "if source ==" xiaomusic/
grep -rn "hasattr.*source" xiaomusic/

echo "=== TEMP-HACK 标注检查 ==="
grep -rn "TEMP-HACK" xiaomusic/ | wc -l
```

**PR 通过条件**：所有 `[ ]` 项已确认合规，或有明确的 ADR 豁免记录。