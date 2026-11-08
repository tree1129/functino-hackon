# 开发指南

本指南规定在不破坏既有边界的前提下扩展 Galbot SDK Web Console。架构和前端约束分别见 [architecture.md](architecture.md) 与 [frontend-architecture.md](frontend-architecture.md)。

## 开始修改前

1. 阅读 `GET /api/capabilities` 的当前输出和目标型号适配器；不要假定所有型号支持相同 SDK 能力。
2. 明确该需求是查询、连续状态、可逆控制还是实机执行。不同类别决定 HTTP、WebSocket、服务状态和确认语义。
3. 明确数据坐标系、单位、默认值、dry-run 行为、live 行为和失败响应。
4. 先确定测试可以在无实机 SDK 环境运行的部分；实机验证应作为独立记录，不可由 dry-run 替代。

## 新增后端能力

按以下顺序实施，避免从页面或路由直接调用 SDK：

1. 在 `sdk/ports.py` 为控制台所需的能力增加领域端口方法。
2. 在 `sdk/platforms/` 实现该端口。SDK 原生枚举、数组和对象应在此处转换为 `dict`、`list`、`str`、`number` 或 `bool`。
3. 仅将真实型号差异加入 `g1.py`、`s1.py`、`g3.py` 等型号适配器；公共 SDK 调用保留在共享适配器中。
4. 在 `services/` 实现用例，校验业务条件，定义 dry-run 与 live 行为，并返回稳定 JSON。
5. 浏览器需要结构化输入时，在 `api/schemas.py` 增加最小 Pydantic schema。传输 schema 不是 SDK 类型的替代品。
6. 在 `gateway/commands.py` 注册 `service/method`；只有稳定且常用的能力才额外增加专用路由。
7. 在平台 capabilities 和 `sdk/registry.py` 中公开已实现能力。未完成实现前不能在 capability 文档或 UI 中声明支持。
8. 需要持续状态时，在 `services/events.py` 采样并发布事件，并补充 domain store 的消费逻辑。

## 命令设计

命令应使用 `POST /api/commands/{service}/{method}`，请求体是 JSON 对象。每一项新命令必须定义：

- 参数类型、单位、范围和坐标系；
- 成功和失败返回值；
- dry-run 是否接受请求，以及返回什么预览数据；
- live 模式下的连接、能力、确认和超时要求；
- 是否会创建任务、计划或可取消状态；
- 是否需要配套连续事件。

不要为 SDK 的每个方法机械创建路由。面向控制台用例组合 SDK 调用，并保持结果可序列化。服务端必须重新校验危险命令的确认状态、计划标识和运行模式。

## Navigation、Robot 与 Motion 的特殊规则

### Navigation

- 位姿顺序固定为 `[x, y, z, qx, qy, qz, qw]`，所有外部位姿都要携带或明确 `frame_id`。
- 路径快照属于任务，前端必须用 `task_id` 防止旧路径覆盖当前任务。
- STOP 与普通导航命令独立处理；不要把停止实现为等待任务结束。
- 共享 Navigation UI 的单目标入口固定使用 `navigate_to_goal_v2` 和 `map`，不要重新暴露接口或坐标系选择。
- 场景点击或拖动只能更新目标草稿；必须由独立确认按钮下发。多点任务要明确区分途经点与轨迹模式。

### Robot

- 点云应限制采样量，并明确是否在 `map`、`base_link` 或传感器坐标系中表达。
- 关节命令必须使用单一关节组、有限速度和有限超时；参数校验留在服务端。
- 相机读取可能阻塞，连续流必须保持在现有专用执行器和 WebSocket 路径中，不能阻塞事件循环。
- 场景显示选项属于浏览器偏好。底盘位姿、基础电源和分组关节角按需叠加在场景内，不要恢复独立 BMS 或基础状态面板。
- 连续底盘摇杆必须有中心死区、非线性低速区、总速度限幅，并在 pointer release/cancel、窗口失焦和工作区停用时下发零速度。

### Motion

- 规划、预览和执行必须分离。执行只能使用服务端保存的有效 `plan_id`，并检查 live 模式、显式确认、过期和重复执行。
- 碰撞体在浏览器中的预览与实际加载到 SDK 是两个状态，界面必须清楚区分。
- 新增终端或关节动作时，要说明目标坐标系、速度/加速度限制、碰撞检查和停止语义。
- 普通目标规划固定使用 `base_link`；碰撞体场景选点固定使用共享地图的 `map`，不要提供可能导致误标的坐标系选择。
- 末端偏移先选择各轴正/负方向，再由唯一的按住按钮下发组合指令；松开、失焦或工作区停用必须立即停止循环。
- 候选碰撞体只存在于浏览器场景，必须经过显式操作才能加载到 Motion 服务；服务端碰撞体仅允许单项确认删除。

## 共享场景开发规则

1. Robot、Motion、Navigation 只能挂载到 `console.html` 的同一个 `SharedSceneRuntime`；不得增加 standalone 场景页。
2. 新工作区行为应加入对应 workspace controller，页面 shell 不拼装业务 payload，`SceneMapView` 不发送 HTTP 命令。
3. 稳定场景数据可以跨工作区保留；选点、拖拽、目标编辑、播放和连续下发必须在 `deactivate()` 中终止。
4. 新增控制器 DOM 引用时同步增加架构测试，保证每个 `this.$(...)` ID 位于该控制器自己的 panel 内。
5. 连续控制必须使用单一计时器并避免请求重叠；选择状态不能直接触发真机命令，危险动作仍需服务端确认。
6. 修改场景入口时，旧 URL 最多保留纯重定向，不得加载业务模块、import map 或专用样式。

## 前端实现顺序

1. 扩展 `ConsoleClient`，将请求和事件连接集中管理。
2. 在 `domain/` 建立或扩展 store，定义事件合并、初始值、错误和断流语义。
3. 在 `features/<domain>/` 实现 DOM 绑定、参数校验、能力过滤和结果呈现。
4. 如需三维表现，在 `SceneMapView` 增加领域公开方法，而不是从 feature 访问内部对象。
5. 更新页面 HTML/CSS，确保 desktop 与窄屏布局、缺失能力和无数据状态可用。

## 验证清单

- 为 service、adapter 或 schema 的新分支补充单元测试；SDK 对象转换使用假对象测试。
- 对新增命令测试参数错误、dry-run、live 条件失败和成功结果。
- 对新增事件测试事件封套、初始快照、任务切换和慢客户端语义。
- 对页面功能测试页面入口、关键控件、能力过滤以及场景公开 API 调用。
- 对共享场景测试单 runtime/单订阅、工作区切换清理、控制器 DOM 所有权和旧入口纯重定向。
- 前端提交应对所有第一方 JavaScript 执行 `node --check`，并搜索已删除模块的残余引用。
- 执行 `uv run pytest`；若当前环境无法运行，应在提交说明中记录环境原因和未执行范围。
- 实机测试前确认机器人权限、环境安全、急停可用和速度限制；实机测试结果单独记录 SDK 版本、型号、场地和结果。

## 文档同步

新增或改变以下任一内容时，必须同一提交更新文档：端口方法、命令、事件类型、坐标系、执行防护、页面入口、场景 API、模型资产或能力声明。本文档记录实施流程，架构文档记录事实边界，前端架构文档记录浏览器侧边界；三者不得相互矛盾。

Rerun 机器人模型通过 `scripts/generate_robot_model_recording.py` 生成到 `assets/robots/<model>/rerun/model.rrd`。默认执行 `--representation collision` 生成不透明橙色的轻量碰撞模型；需要精细外观时才执行 `--representation visual`。这些 `.rrd` 是部署生成物，不得加入 Git。任务 `.rrd` 不得再次嵌入 URDF 网格；模型和任务必须使用相同的稳定 application/recording ID，并通过 `/api/playback-files/{id}.rrd` 的单一组合流回放。修改 URDF、碰撞几何、材质或 mesh 后必须重新生成共享模型记录，并验证历史回放中的机器人、地图、LiDAR、路径和障碍物同时可见。
