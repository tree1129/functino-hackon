# Galbot SDK Web Console 架构

本文档是本项目的后端和跨层开发基准。它只描述仓库当前实现和必须保持的边界；新增能力必须先满足这些边界，再扩展界面或协议。

## 目标和运行模式

控制台通过 Galbot SDK 提供机器人状态、三维可视化、Navigation、Robot、Motion 和配置功能。浏览器不导入也不直接调用 SDK。

CLI 支持 `g1`、`s1`、`g3` 三种机器人型号，默认型号是 `g1`。未传入 `--live` 时，服务以 dry-run 模式运行：SDK 不初始化，命令返回可预览的结果而不操作机器人。`--live` 才会初始化 SDK；页面必须以 `GET /api/capabilities` 的 `live_mode` 和服务能力为准，不能仅根据界面状态推断实机可用性。

## 分层和依赖方向

```text
Browser pages / features
        | HTTP JSON, WebSocket
gateway + api schemas
        | application calls
services
        | domain ports and one shared session
sdk/session + sdk/ports
        | model adapters
sdk/platforms/<model>
        | native bindings
Galbot SDK
```

依赖只能从上向下：

- `gateway/` 负责 FastAPI、静态资源、路由和进程生命周期；不能包含机器人业务逻辑。
- `api/` 定义浏览器输入的 Pydantic schema，是 HTTP JSON 的校验边界。
- `services/` 实现用例、dry-run/live 分支、任务状态、命令防护和事件采样；不得导入 FastAPI 或前端代码。
- `sdk/ports.py` 定义 Navigation、Robot、Motion 需要的领域端口。
- `sdk/platforms/` 是唯一可以持有、调用或转换 Galbot SDK 原生对象的位置。型号差异只能放在型号适配器中，不能散落到服务、网关或前端。
- `frontend/` 只消费规范化的 HTTP、WebSocket 和静态资源数据；Three.js 场景不发送机器人命令。
- `assets/robots/<model>/` 存放该型号的 URDF、网格和 `manifest.json`，不在前端代码中硬编码某型号的资产路径。

禁止反向导入：适配器不能导入 service、service 不能导入 gateway、场景不能导入 feature 的业务控制器。

## 运行时和生命周期

`ConsoleRuntime` 是应用组合根。它为 Navigation、Robot、Motion 和 Configuration 服务创建并共享一个 `SdkSession`，同时持有 `EventBroker`。

浏览器侧对应一个长生命周期的 `SharedSceneRuntime`。它只创建一个 Three.js 场景、一个 renderer、Robot/Navigation store 和一条 `/api/events` 订阅，并通过 `WorkspaceSceneHost` 在 Robot、Motion、Navigation profile 之间切换。浏览器工作区切换不影响后端 `ConsoleRuntime`，也不得重新创建前端 runtime。旧 `robot.html`、`motion.html`、`navigation.html` 只是兼容重定向，不构成独立运行时。

FastAPI lifespan 的语义为：

```text
application startup -> runtime.initialize() -> serve requests
application shutdown -> stop EventBroker -> close SdkSession
```

仅 live 模式在启动时初始化 SDK。服务必须允许在未安装或未连接 SDK 的开发环境中创建并运行 dry-run 用例；不要在模块导入阶段初始化特定型号 SDK。

## HTTP 和 WebSocket 契约

### 查询和资源

| 接口 | 用途 |
| --- | --- |
| `GET /api/capabilities` | 返回当前型号、运行模式、服务能力和命令/事件协议。 |
| `GET /api/robot/state` | 返回当前 Navigation 快照。 |
| `GET /api/navigation/tasks/{task_id}/path` | 返回任务对应的规划路径快照。 |
| `GET /api/maps/global-cloud` | 返回可用时的静态全局点云文件。 |
| `GET /api/config/services` | 返回可配置服务列表及当前是否为预览模式。 |
| `GET /api/config/schema/{service}` | 返回配置服务的 schema 和值。 |
| `GET /api/robot/camera/{source}` | 获取单帧相机数据。 |
| `GET /api/recording/capabilities`、`/status` | 返回可录制数据源和当前跨页面录制状态。 |
| `POST /api/recording/{arm,start,pause,resume,finish,cancel}` | 驱动受服务端校验的录制状态机。 |
| `GET /api/recordings`、`/api/recordings/{id}/file` | 列出及下载已完成的 Rerun 记录。 |
| `GET /api/recording-files/{id}.rrd` | 使用带格式扩展名的签名 URL 向 Rerun Viewer 提供跨域、Range 可读的数据流。 |
| `GET /api/playback-files/{id}.rrd` | 将共享机器人模型与轻量遥测记录合并为单一、可缓存的 Viewer 回放流。 |
| `GET /api/recording-assets/{model}.rrd` | 提供部署在 `assets/robots/<model>/rerun/model.rrd` 的共享模型记录。 |
| `POST /api/recordings/import` | 将用户明确选择的 `.rrd` 文件流式导入受管录制目录，供后续回放。 |
| `DELETE /api/recordings/{id}` | 显式确认后删除单个记录。 |
| `GET /api/navigation/tasks` | 返回当前服务进程最近创建的导航任务，供历史路径选择。 |
| `GET /api/navigation/tasks/{task_id}/path` | 按稳定任务 ID 查询 SDK 规划路径。 |

根路由提供 `console.html`；`/frontend` 和 `/assets` 是静态资源挂载点。前端资源响应禁止缓存，以避免浏览器继续使用旧页面脚本。

### 命令

通用命令入口为：

```text
POST /api/commands/{service}/{method}
Content-Type: application/json
```

`gateway/commands.py` 是唯一的通用命令注册表。常用 Navigation 操作也提供 `POST /api/navigation/go`、`/stop` 和 `/relocalize` 路由。输入必须由 `api/schemas.py` 的 schema 校验，错误以 HTTP 422 返回；未注册命令返回 HTTP 404。

命令结果必须使用普通 JSON 值，不能泄漏 pybind、NumPy 或 SDK 对象。涉及实机动作的返回值必须明确 `dry_run`；需要用户确认的操作必须在服务端验证 `confirmed`，而不能只依靠前端按钮状态。

### 事件

`WS /api/events` 发送的每条消息固定为：

```json
{
  "type": "navigation_state",
  "timestamp_ns": 1710000000000000000,
  "sequence": 42,
  "data": {}
}
```

当前事件类型为 `navigation_state`、`navigation_path`、`joint_state`、`local_cloud`、`robot_sensors` 和 `service_error`。`EventBroker` 采样服务并将阻塞调用移出 asyncio 事件循环；每个订阅者拥有容量为 8 的队列，队列满时舍弃较旧的状态以保留较新状态。新客户端会立即获得当前 Navigation 状态和路径快照。

相机连续帧使用独立的 `WS /api/robot/camera-stream`。相机读取在专用线程池中执行，不能占用 Navigation、Robot 状态或事件采样所依赖的执行器。

### 数据录制与 Rerun

`RecordingService` 是跨页面录制状态的唯一所有者，状态依次为 `INACTIVE`、`ARMED`、`RECORDING`、`PAUSED` 和收尾状态。只有专用数据回放页面可以建立 `ARMED` 意图；Console、配置页和数据回放页的全局录制条只能控制已经启用的会话。结束或取消后必须返回 `INACTIVE`，不能自动保留下一次录制授权。

录制使用独立的 Rerun file sink 写入 `.partial.rrd`，正常结束后原子改名为 `.rrd`；实时查看使用独立的有限内存 gRPC sink，Viewer 断开不得影响文件写入。录制目录受 `GALBOT_RECORDINGS_DIR` 限制，浏览器只能提交相对目录和安全文件名。用户导入的 `.rrd` 采用临时文件流式写入并原子归档，不在内存中缓存整个大文件。取消和删除都必须显式确认。

录制数据不能来自浏览器 `/api/events`，因为该通道允许降采样和丢弃旧状态。RobotService 对 SDK 状态与相机读取进行串行化，避免显示采样与录制采样并发进入 SDK。底盘 `map` 位姿当前来自 `GalbotNavigation.get_current_pose()`；该结果没有源时间戳，因此记录服务使用 `time.time_ns()` 接收时间，并明确写入 `time_quality: "receive"`，不得伪装为设备采样时间。

Rerun 记录包括腿、头和双臂共 21 个真实关节角、底盘位姿、所选 RGB/原始深度、LiDAR、传感器、静态 PCD、已知 Motion 碰撞体以及导航状态/路径。体积较大的机器人几何只写入部署时生成的 `assets/robots/<model>/rerun/model.rrd`；该生成产物不进入 Git，每次任务记录只保留模型标识、仓库内 manifest/URDF 路径、上游版本来源和动态数据。历史回放由服务端使用 Rerun 官方合并器生成临时缓存流，避免 Web Viewer 将两个相同 Store ID 的 URL 判定为重复记录。只有名称在当前机型 URDF 中严格匹配的真实关节角才可驱动对应模型关节，不允许按序号或相似名称猜测映射；关节标量实体统一命名为 `position`，弧度单位记录在元数据中。

项目录制格式通过 `/metadata/recording_format` 声明名称、版本、application ID、`capture_time` 时间轴、`map` 世界坐标系和稳定的机器人模型 Store ID。所有空间数据必须显式声明坐标系，录制端与共享模型必须使用相同 Store ID。默认共享模型是由 collision URDF 生成的轻量、不透明橙色实体；精细 visual 模型仅按需生成。默认 Rerun Blueprint 将机器人、底盘、地图、雷达、导航路径和碰撞体组织到同一个 `/world/**` 三维视图，关节曲线与相机图像保持独立视图类型。外部 Motion 碰撞体只有 ID 时必须保留“几何未知”事实。

## 服务职责和安全语义

### Navigation

`NavigationService` 在进程生命周期内保留最近 50 个由控制台创建且具有任务 ID 的导航任务，记录模式、目标、创建时间与最近状态。任务历史只用于选择 SDK 任务及查询对应路径，不替代 SDK 的任务状态，也不承诺服务重启后持久化。前端默认自动跟随当前任务；用户显式选择历史任务后，当前任务事件不得覆盖所选路径。

`NavigationService` 管理定位、目标、任务状态和路径快照。目标位姿在传入 SDK 前后均使用 `[x, y, z, qx, qy, qz, qw]` 约定；浏览器数据中必须同时携带 `frame_id`。dry-run 可以生成任务标识和目标快照，但不声明存在规划路径。

共享 UI 的单目标导航使用 `navigate_to_goal_v2`，坐标系固定为 `map`；同时提供 `check_path_reachability`、`navigate_through_waypoints` 和 `navigate_along_trajectory`。SDK 适配层可以保留其他已实现接口，但不因此自动扩大共享 UI 范围。场景选点与命令下发是两个动作，目标或多点草稿不能在 pointer release 时自动发送。

### Robot

`RobotService` 提供关节、雷达、传感器、相机、控制器、底盘速度、急停和关节/夹爪操作。雷达配置包括启用状态、点数上限和裁剪参数。关节指令必须只涉及一个预定义关节组，并在服务端限制最大速度和超时。

### Motion

`MotionService` 管理碰撞体、运动规划预览和执行。规划和执行是两个不同命令：规划成功后产生临时 `plan_id`；执行只允许 live 模式、显式确认、未执行且未过期的规划。新增 Motion 行为不得绕开这一流程。

普通规划目标使用 `base_link`；共享场景拾取和碰撞体使用 `map`。浏览器中的候选碰撞体与 Motion 服务实际持有的碰撞体是不同状态，加载和删除必须显式触发。碰撞检查是规划请求的单一可选项，轨迹可视化、播放和同步属于规划结果生命周期。

### Configuration

`ConfigurationService` 将 SDK 的配置服务以 schema 和变更集合形式公开。配置写入必须在服务层完成，不允许前端直接保存 SDK 原生配置对象。

## 不变规则

1. 新的 SDK 能力先扩展端口和适配器，再扩展 service、schema、命令和页面。
2. 每个页面只显示 `capabilities` 声明的能力；缺少能力时要禁用或隐藏操作，并给出明确状态。
3. STOP/急停路径保持独立、可见且不依赖普通任务完成。
4. 浏览器的场景显示设置属于本地 UI 状态，不能作为机器人命令发送。
5. 点云、相机和连续状态应走事件或专用流；不要为每一帧创建新的命令路由。
6. 新增协议字段、事件类型、目录职责或执行语义时，必须同步更新本文档、前端架构文档和相关测试。
