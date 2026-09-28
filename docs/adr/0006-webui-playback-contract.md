# ADR-0006：WebUI 播放请求与在线搜索引用边界

- **状态**：Accepted（代码级实现与定点验证通过；服务器/实机验收不在本包）
- **日期**：2026-09-24

## 上下文

主页曾通过任意 options 字典发送未声明的 `prefer_codec` 与 `search_key`，在严格的 `PlayOptionsModel(extra="forbid")` 下产生 422。在线搜索插件实际返回 MusicFree 私有 `IMusicItem`，不能直接暴露给 WebUI。

## 决策

1. WebUI 使用与 `PlayOptionsModel` 对齐的 typed `PlayOptions`，主页请求由 typed builder 构造；后端继续拒绝未知字段。
2. 在线搜索结果只有携带正式 `play_reference`（已声明的 query、source_hint 和严格 options）时才允许进入 `/api/v1/play`。
3. 缺少正式引用时，WebUI 不以标题、搜索词或插件私有 item 冒充播放身份，也不向 WebUI 暴露媒体 URL、cookie、token 或原始插件错误。
4. 本工作包只新增一个内置 `OnlinePluginSourcePlugin` 与进程内有 TTL/容量上限的 opaque 引用存储；不新增通用插件框架、不引入 play/session 全链路。

## 后果

链接播放和在线搜索播放路径均由前后端定点契约测试和 CI 门禁保护。当前不新增 `online_plugin` delivery proxy fallback：`DeliveryAdapter`/Mina 路径没有证明能以解析 headers 完成代理取流；该问题留待独立 delivery 设计。测试服务器、真实入口及实机播放仍需独立验收，不能由代码级测试替代。
