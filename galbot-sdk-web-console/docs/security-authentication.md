# Web Console 轻量安全校验设计

## 目标与边界

本设计解决的是机器人控制服务被其他 PC 意外打开并操作的问题。它提供一个启动级访问门槛，不试图实现多用户、企业身份认证、细粒度角色管理或抗局域网窃听能力。

服务仍然使用 HTTP 时，访问码登录过程可能被能够主动抓包的设备观察到。因此生产网络仍应使用隔离的控制网段，后续如有需要可在不改变本认证流程的前提下增加 HTTPS/WSS。

## 设计原则

1. **每次启动重新生成**：服务进程启动时生成 8 位短访问码。访问码只存在于进程内存，服务重启后旧访问码和旧会话全部失效。
2. **只在建立会话时校验**：访问码只用于登录；成功后使用内存 Session Cookie。HTTP 请求读取 Cookie，WebSocket 只在握手时校验 Cookie。
3. **不进入实时数据路径**：事件流、相机流和控制请求建立连接后不重复计算或传输访问码，不对帧做额外加密，因此不会给实时传输增加持续开销。
4. **默认拒绝控制面**：除登录页面、静态资源和认证接口外，所有 `/api` 路由都要求有效会话；两个机器人 WebSocket 也必须先认证。
5. **访问码便于人工输入**：字符集排除了 `0/O` 和 `1/I` 等易混淆字符，支持输入空格和 `-` 分组。
6. **进程级而非持久化凭据**：不写入磁盘、不放到 URL query、不由前端保存长期密钥。

## 运行流程

```text
服务启动
  -> 生成本次访问码并在机器人终端打印一次
浏览器打开控制台
  -> GET /api/auth/status
  -> 未认证时显示访问码输入框
  -> POST /api/auth/login
  -> 设置 HttpOnly Session Cookie
  -> 初始化状态、控制和 WebSocket
服务重启
  -> 新访问码、新进程会话
  -> 旧 Cookie 自动失效
```

## 接口行为

允许匿名访问：

- `/`
- `/frontend/*`
- `/assets/*`
- `GET /api/auth/status`
- `POST /api/auth/login`

必须登录：

- 其他所有 `/api/*` HTTP 接口；
- `WS /api/events`；
- `WS /api/robot/camera-stream`。

认证失败统一返回 HTTP `401`；未认证 WebSocket 在 `accept()` 前以策略错误关闭。

## 访问码与会话

- 访问码由 Python `secrets` 模块生成，长度为 8 个字符；
- 校验使用常量时间比较；
- 会话 ID 使用随机值生成，保存在进程内存集合中；
- Cookie 名称为 `galbot_console_session`，带 `HttpOnly`、`SameSite=Lax` 和有限有效期；
- 注销会从内存集合删除当前会话并清除 Cookie；
- 服务进程结束后，所有会话自然失效。

## 实现位置

- `src/galbot_sdk_web_console/gateway/auth.py`：访问码和内存会话管理；
- `src/galbot_sdk_web_console/gateway/app.py`：HTTP 中间件、认证接口和 WebSocket 握手校验；
- `frontend/pages/console-bootstrap.js`：登录门控；
- `frontend/pages/console.js`：认证完成后才初始化机器人控制台；
- `frontend/console.html`、`frontend/styles/console.css`：访问码输入界面；
- `tests/test_gateway.py`：匿名拒绝、登录、会话和访问码格式测试。

## Rerun 记录访问

录制配置、状态转换、记录列表、删除和普通下载均使用现有会话认证。Rerun Web Viewer 运行在独立本地端口，读取历史 `.rrd` 时不能携带控制台的同源会话 Cookie，因此记录列表为单个文件生成进程内 HMAC 播放令牌。该令牌只授权读取对应 `.rrd`，不授权列表、删除或任何机器人操作；服务重启后令牌失效。

录制输出只能位于 `GALBOT_RECORDINGS_DIR` 下。服务端必须解析并验证最终路径，拒绝绝对路径、`..`、符号链接越界和覆盖现有文件。取消只能删除当前会话创建的 `.partial.rrd`，历史记录删除必须经过认证和显式确认。

Rerun 实时 gRPC server 当前不提供应用层认证，默认使用操作系统分配的临时端口以减少冲突和长期暴露。生产部署不得直接将该端口暴露到不受信网络；应通过防火墙限制访问，或在带认证的反向代理后转发。完成这一部署约束验证前，不能把公网实时查看标记为安全支持。

## 性能效果

认证只发生在以下低频位置：

- 页面首次检查会话；
- 登录请求；
- 每个 HTTP 请求的一次内存集合查找；
- 每个 WebSocket 的一次握手检查。

相机二进制帧、事件消息和 SDK 控制执行不会重复携带访问码，也不会经过额外的加密层。对当前服务而言，性能影响只相当于常规 Cookie 认证和一次内存查找。

## 操作说明

启动服务后，从机器人运行终端读取：

```text
Galbot SDK Web Console access code: ABCD2345
```

打开启动输出中的网页地址，输入该访问码即可连接。重新启动服务后必须使用新访问码；浏览器中的旧会话不能继续控制机器人。
