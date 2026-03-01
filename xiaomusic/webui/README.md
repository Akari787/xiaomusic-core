# WebUI 开发说明

- 技术栈：Vite + React + TypeScript
- 启动开发：`npm run dev`
- 构建产物：`npm run build`（输出到 `xiaomusic/webui/static`）
- 资源目录：`xiaomusic/webui/static` 同时承载运行时静态资源与构建产物（已不再使用 `public/`）

## 环境变量

- `VITE_API_BASE_URL`：后端 API 根地址（默认空，表示同源）

## 主题说明

- 当前仅提供 `defaultTheme`。
