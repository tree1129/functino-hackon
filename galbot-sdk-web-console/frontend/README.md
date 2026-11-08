# 前端

前端页面通过本项目的 HTTP 和 WebSocket 接口使用 Galbot SDK 服务。页面入口脚本位于 `pages/`，领域交互位于 `features/`，页面状态位于 `domain/`，共享三维场景位于 `scene/`，样式统一位于 `styles/`。

正式页面入口为 `console.html`、`config.html` 和 `recording.html`。`recording.html` 是 Rerun 数据录制与回放页面；`robot.html`、`motion.html`、`navigation.html` 仅作为兼容 URL，分别重定向到共享 Console 工作区，不再包含独立场景或业务逻辑。

目录职责：

- `api/`：HTTP 和 WebSocket 客户端。
- `pages/`：页面启动、页面级控件绑定和组合逻辑。
- `features/`：Robot、Motion、Navigation 等业务功能。
- `domain/`：与页面无关的状态存储和领域数据处理。
- `scene/`：共享三维场景、显示偏好和本地 Three.js 运行时。
- `styles/`：基础、页面和功能样式。

根目录只保留稳定的 HTML 访问入口、说明文档和静态元数据，避免页面实现再次平铺到根目录。
