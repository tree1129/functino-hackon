# 前端架构

本文档定义当前 Web 控制台的前端分层和修改边界。前端使用原生 ES module、HTML、CSS 和本地 Three.js 运行时，不依赖打包步骤。

## 页面和职责

| 入口 | 职责 |
| --- | --- |
| `console.html` | 集成式控制台：Robot、Motion、Navigation 和配置入口，共享一个三维场景。 |
| `robot.html` | 兼容入口，重定向到 `console.html#robot`。 |
| `motion.html` | 兼容入口，重定向到 `console.html#motion`。 |
| `navigation.html` | 兼容入口，重定向到 `console.html#navigation`。 |
| `config.html` | SDK 配置读取和编辑。 |
| `recording.html` | 独立的数据回放入口；配置 Rerun 录制、查看实时数据、管理记录并进行离线回放。 |

页面入口只负责组装 UI、控制器、store 和场景。不要把 SDK 调用、事件协议解析或 Three.js 内部操作复制进页面脚本。

## 目录边界

```text
frontend/
  api/                 HTTP 和 WebSocket 客户端
  domain/              页面无关的状态和数据转换
  features/            Robot、Motion、Navigation 的交互控制器
  pages/               页面启动、路由与控制器组装
  scene/               Three.js 场景和显示偏好
  scene/vendor/        受版本约束的本地 Three.js 运行时与加载器
  styles/              页面和功能样式
  *.html               稳定页面入口或兼容重定向
```

- `api/console-client.js` 中的 `ConsoleClient` 是通用 HTTP JSON、二进制请求和状态事件 WebSocket 的入口。新增业务请求先扩展它，feature 不应自行拼接 `fetch` 或 WebSocket 地址。
- `domain/` 保存普通 JavaScript 状态。`RobotStore` 消费关节、点云和传感器事件；`NavigationStore` 消费 Navigation 状态和路径，并在任务切换时清除旧路径；`pose.js` 处理位姿和四元数转换。
- `features/` 负责 DOM 事件、参数收集、能力过滤、调用 `ConsoleClient` 和渲染 store 状态。它们可以调用场景公开 API，但不能操作其内部 mesh、material 或 group。
- `scene/scene-map.js` 是共享三维场景的唯一实现。`scene-preferences.js` 管理浏览器本地显示偏好。

## 数据流

```text
用户操作 -> feature controller -> ConsoleClient -> gateway command -> service -> SDK
SDK 状态 -> EventBroker -> ConsoleClient WebSocket -> domain store -> feature / SceneMapView
```

命令是一次请求/响应；状态是可能高频变化的最新快照。feature 不应直接把原始 WebSocket 消息写入 DOM：先由对应 store 合并，再由订阅者渲染。对于 `sequence` 较旧或任务标识不匹配的数据，应保留当前较新的显示状态。

## 场景边界

`SceneMapView` 管理机器人模型、关节、静态和实时点云、坐标系、导航目标和路径、Motion 碰撞体、轨迹及测量显示。业务层通过明确的公开方法更新它，例如：

- 机器人和关节：`setRobotPose`、`setJointState`、`setRobotVisible`；
- 点云和图层：`setLocalCloud`、`setGlobalCloud`、`setLayerVisible`、`setCloudViewOptions`；
- Navigation：`setGoal`、`setPath`、`clearPath`；
- Motion：`setMotionObstacles`、`setCollisionRobotState`、`setEndEffectorTrajectory`；
- 视图和交互：`set3dView`、`setTopView`、`fit`、`pickWorldPoint`、`collectWorldPoints`。

需要新视觉表现时，先在 `SceneMapView` 增加表达领域概念的公开方法，再由 feature 调用。不得让 feature 保存 Three.js 对象引用，也不得让场景发送 HTTP 命令。

机器人资产由 `/assets/robots/<model>/manifest.json` 描述。场景根据 manifest 解析 URDF 和 package URI；不要在 JS 中加入 G1 专用的资产路径或关节命名分支。型号能力差异通过后端 capabilities 和 manifest 表达。

`/api/scene/bootstrap` 只公开进程配置的机型名称，供共享场景在实时 SDK 或登录状态异常时仍能加载静态机器人资产。它不返回机器人状态或控制能力；实时状态、capabilities、命令和事件接口仍受访问码保护。

### 单一场景运行时

`console.html` 是 Robot、Motion、Navigation 唯一的场景入口。页面只创建一个 `SharedSceneRuntime`，后者持有一个 `SceneMapView`、Robot/Navigation store、默认机器人与静态地图，以及唯一的 SDK 事件订阅。工作区切换不得重新创建 renderer、重新加载默认 GLB/PCD 或建立第二条事件连接。

`WorkspaceSceneHost` 按“旧工作区 `deactivate` → 场景临时交互兜底清理 → 新工作区 `activate`”切换 profile。每个工作区控制器必须满足：

- DOM 监听只在 `bind()` 中注册一次；`activate()`、`deactivate()` 和 `dispose()` 可重复调用。
- 选点、导航目标草稿、Motion gizmo、轨迹播放计时器和 Robot 关节预览属于临时状态，离开工作区时必须停止或清理。
- 异步请求使用 epoch 或同等机制阻止停用后的过期结果回写。
- 控制器通过构造参数接收 `client`、`scene`、`runtime` 和自身 `panelRoot`，不得自行创建场景、store 或订阅。

`robot.html`、`motion.html`、`navigation.html` 只保留到对应 Console hash 的静态重定向。仓库不得重新加入 standalone 参数、独立场景脚本或页面专用 Three.js runtime。

### 工作区职责

| 工作区 | 浏览器职责 |
| --- | --- |
| Robot | 关节预览与受控下发、夹爪、非线性底盘摇杆、场景显示、基础电源/位姿/分组关节角叠加、本地 PCD/URDF、静态与实时点云裁剪。相机录制与回放不属于该工作区。 |
| Motion | 固定运动链上下文、组合末端偏移、`base_link` 目标规划、单一碰撞检查开关、轨迹预览/播放/同步、候选碰撞体与 Motion 服务碰撞体的显式两阶段加载。场景选取碰撞体使用 `map`。 |
| Navigation | 单目标使用 Navigation V2，内部固定 `map`；场景选点只生成草稿，必须点击按钮下发。支持路径可达性、路径显示，以及途经点和轨迹两种多点模式。 |

场景视图在所有工作区保持一致：3D 左键旋转、`Shift + 左键` 或右键平移；俯视左键或右键平移、`Shift + 左键` 绕地图 Z 轴旋转；滚轮缩放；`F` 从任意视图切换为 45° 机器人聚焦。快捷键显示在场景左下角，状态与错误提示不得覆盖它。

Navigation 顶层路径设置通过下拉框选择“当前任务（自动跟随）”或当前服务进程内的历史导航任务。历史任务切换必须使用任务 ID 调用路径接口并替换场景路径；选中历史任务期间，新到达的当前任务路径事件不得覆盖用户选择。路径显隐与线宽仍是同一顶层设置的一部分。

### 数据回放页面

`recording.html` 不属于共享 Three.js 工作区，不创建 `SharedSceneRuntime`。页面采用三栏结构：左侧是可独立滚动的录制配置与历史记录，中间是带明确文件载入入口的 Rerun 场景/时间轴，右侧是实时相机监看区。左右侧栏都可以独立折叠，折叠按钮固定在 Viewer 对应边界的垂直中央；侧栏折叠后不得保留空白网格列，中间 Viewer 应占满释放的空间。该行为只属于数据回放页面，不改变共享 Console 的侧栏状态。

页面使用本地部署的 Rerun Web Viewer 展示实时 gRPC 数据、历史记录或用户导入的 `.rrd`；实时相机窗口可调整尺寸，并可从右侧拖出为浮窗，历史相机数据仍由中间的 Rerun 时间轴统一驱动。历史记录不能把共享模型和遥测作为两个相同 Store ID 的 `url` 参数直接交给 Viewer，而应加载后端生成的单一组合回放 URL。Rerun 内部的 Blueprint 名称采用 ASCII，避免 Web Viewer 字体包不完整时出现乱码。

进入页面时不得自动播放历史记录，必须由用户选择记录或文件；存在活动录制时可以显示实时录制流。未认证的直接访问必须跳转到控制台统一登录界面，登录成功后再安全回跳，数据回放页不维护独立登录表单。

`RecordingBar` 是跨页面轻量控件，只读取服务端录制状态并发送状态转换请求。它在 `INACTIVE` 时隐藏，不能自行保存录制配置或通过页面生命周期推断录制是否仍在进行。记录页面关闭后，活动录制继续由服务端持有。

## 实时显示规则

1. 实时数据必须显示连接或时间状态；断流后不能把旧数据标记为实时。
2. 点云设置（点数上限、裁剪、点大小和可见性）要区分服务端采样配置与浏览器显示偏好，不能混用。
3. 相机按需模块仅在窗口可见且 Robot 工作区处于活动状态时订阅；关闭、切换工作区或页面不可见时释放订阅和 Object URL。当前 Robot 工作区不展示相机 UI，后续由独立页面承接录制与回放。
4. 规划轨迹的本地播放仅用于预览，不能被表示为已经下发或已经执行。
5. Motion 执行、底盘速度、关节和夹爪操作必须在 UI 明确体现 live/dry-run 状态与服务端返回结果。

## 前端修改准则

- 新增领域功能：先定义后端能力和事件/命令语义，再新增 store、feature、UI 和场景公开 API。
- 新增事件：在对应 store 中实现合并规则，处理空值、断流、过期序列和不匹配任务；不能让每个页面各自解析同一事件。
- 新增场景图层：提供显式的显示开关、清除方法和资源释放路径，并在页面销毁或切换时调用。
- 新增模型显示：先补齐资产 manifest，确认 URDF、mesh 和坐标系，再接入场景。
- 不要修改 `scene/vendor/` 中的第三方文件来实现业务需求；业务代码应放在 `scene/` 或 feature。

任何改变页面入口、事件消费、场景公开 API 或资产 manifest 的提交，都必须更新本文件和覆盖相应行为的测试。
