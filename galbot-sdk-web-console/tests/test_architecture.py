import re
from pathlib import Path

from galbot_sdk_web_console.services.runtime import ConsoleRuntime


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "galbot_sdk_web_console"


def test_fastapi_is_confined_to_transport_layer() -> None:
    allowed = {"api", "gateway"}
    violations = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative = path.relative_to(PACKAGE_ROOT)
        if relative.parts[0] in allowed:
            continue
        if "fastapi" in path.read_text(encoding="utf-8"):
            violations.append(str(relative))
    assert violations == []


def test_capabilities_only_advertise_implemented_boundaries() -> None:
    capabilities = ConsoleRuntime(model="g1", live=False).capabilities()["services"]
    assert capabilities["navigation"] == [
        "snapshot", "navigate_to_goal", "navigate_to_goal_v2", "set_navigation_target",
        "navigate_through_waypoints", "navigate_along_trajectory", "navigate_with_velocity",
        "check_path_reachability", "get_navigation_status", "get_navigation_target_status",
        "navigation_path", "stop_navigation", "relocalize", "move_straight_to",
    ]
    assert capabilities["robot"][:5] == ["joint_states", "lidar", "controller_snapshot", "switch_controller", "base_velocity"]
    assert "emergency_stop" in capabilities["robot"]
    assert "stop_controller" in capabilities["motion"]
    assert capabilities["motion"][:2] == ["plan_preview", "execute_plan"]
    assert "cartesian_plan" in capabilities["motion"]


def test_legacy_scene_urls_are_redirect_only() -> None:
    frontend = PACKAGE_ROOT.parents[1] / "frontend"
    for page_name, workspace in (("robot.html", "robot"), ("motion.html", "motion"), ("navigation.html", "navigation")):
        source = (frontend / page_name).read_text(encoding="utf-8")
        assert f"location.replace('/frontend/console.html#{workspace}')" in source
        assert "standalone" not in source
        assert 'type="module"' not in source
        assert 'type="importmap"' not in source


def test_console_is_the_only_shared_scene_runtime_entry() -> None:
    frontend = PACKAGE_ROOT.parents[1] / "frontend"
    console = (frontend / "pages/console.js").read_text(encoding="utf-8")
    runtime = (frontend / "scene/shared-scene-runtime.js").read_text(encoding="utf-8")
    assert "class SharedSceneRuntime" in runtime
    assert "SharedSceneRuntime" in console
    assert "new SceneMapView" not in console
    assert "new NavigationStore" not in console
    assert "new RobotStore" not in console
    assert "client.subscribe" not in console
    assert "client.getCapabilities" not in console
    assert "client.getState" not in console
    assert runtime.count("this.client.subscribe") == 1
    assert runtime.count("this.robot.render") == 1
    assert "this.client.getSceneBootstrap()" in runtime
    assert runtime.index("getSceneBootstrap") < runtime.index("getCapabilities")
    assert "fallbackModel = 'g1'" in runtime
    assert "场景配置查询超时" in runtime
    assert "robot.render" not in console
    for removed in (
        "features/motion/full-tool.js", "features/robot/full-tool.js",
        "features/robot/controller.js", "features/navigation/controller.js",
        "pages/navigation.js", "features/shared/ui.js",
        "styles/robot.css", "styles/navigation.css", "styles/workbench-overrides.css",
    ):
        assert not (frontend / removed).exists()


def test_frontend_root_contains_only_stable_page_entries_and_metadata() -> None:
    frontend = PACKAGE_ROOT.parents[1] / "frontend"
    assert list(frontend.glob("*.js")) == []
    assert list(frontend.glob("*.css")) == []
    assert {path.name for path in frontend.glob("*.html")} == {
        "config.html", "console.html", "motion.html", "navigation.html", "robot.html",
        "recording.html",
    }


def test_recording_page_and_global_control_have_stable_ownership() -> None:
    frontend = PACKAGE_ROOT.parents[1] / "frontend"
    recording = (frontend / "recording.html").read_text(encoding="utf-8")
    page = (frontend / "pages/recording.js").read_text(encoding="utf-8")
    bar = (frontend / "features/recording/recording-bar.js").read_text(encoding="utf-8")
    console = (frontend / "console.html").read_text(encoding="utf-8")
    config = (frontend / "config.html").read_text(encoding="utf-8")
    assert "数据录制与回放" in recording
    assert 'data-auth-entry="console"' in recording
    assert 'id="authForm"' not in recording
    assert "<span>04</span>数据录制与回放" in console
    assert "<span>05</span>配置可视化" in console
    assert "数据回放" not in console
    assert "new RecordingBar" in page
    assert "recordingAction" in bar
    assert "recording-bar-bootstrap.js" in console
    assert "recording-bar-bootstrap.js" in config
    assert "SharedSceneRuntime" not in page


def test_configuration_waits_for_authentication_before_polling_logs() -> None:
    frontend = PACKAGE_ROOT.parents[1] / "frontend"
    html = (frontend / "config.html").read_text(encoding="utf-8")
    page = (frontend / "pages/config.js").read_text(encoding="utf-8")
    assert 'id="authForm"' in html
    assert 'id="consoleApp" class="app-shell" hidden' in html
    assert "console-bootstrap.js" in html
    assert "window.consoleAuthReady.then(() => init())" in page
    assert "setInterval(pollLogs, 1000)" in page
    assert "error.status === 401" in page
    assert "refreshHistory(true)" not in page


def test_workspace_controller_dom_ids_exist_in_their_own_panels() -> None:
    frontend = PACKAGE_ROOT.parents[1] / "frontend"
    console = (frontend / "console.html").read_text(encoding="utf-8")
    panels = {
        "robot": (frontend / "features/robot/console-controller.js").read_text(encoding="utf-8"),
        "motion": (frontend / "features/motion/controller.js").read_text(encoding="utf-8"),
        "navigation": (frontend / "features/navigation/workspace-controller.js").read_text(encoding="utf-8"),
    }
    for workspace, controller in panels.items():
        panel = console.split(f'data-panel="{workspace}"', 1)[1]
        if workspace != "navigation":
            next_workspace = "motion" if workspace == "robot" else "navigation"
            panel = panel.split(f'data-panel="{next_workspace}"', 1)[0]
        controller_ids = set(re.findall(r"this\.\$\('([^']+)'\)", controller))
        assert not {element_id for element_id in controller_ids if f'id="{element_id}"' not in panel}


def test_unified_navigation_workspace_renders_sdk_planner_paths() -> None:
    console = (PACKAGE_ROOT.parents[1] / "frontend/console.html").read_text(encoding="utf-8")
    page = (PACKAGE_ROOT.parents[1] / "frontend/pages/console.js").read_text(encoding="utf-8")
    controller = (PACKAGE_ROOT.parents[1] / "frontend/features/navigation/workspace-controller.js").read_text(encoding="utf-8")
    scene = (PACKAGE_ROOT.parents[1] / "frontend/scene/scene-map.js").read_text(encoding="utf-8")
    navigation_panel = console.split('data-panel="navigation"', 1)[1]
    for element_id in ("navigationPathTask", "visNavigationPath", "navigationPathWidth", "navigationPathHint"):
        assert f'id="{element_id}"' in navigation_panel
    assert "state.path?.task_id" in controller
    assert "this.client.getNavigationPath(taskId)" in controller
    assert "this.scene.setPath(usable?.available ? usable.poses : [])" in controller
    assert "scene.setLayerVisible('path', event.target.checked)" in page
    assert "scene.setPathWidth" in page
    assert "navigation_path: 'path'" in scene


def test_navigation_workspace_only_exposes_supported_sdk_workflows() -> None:
    frontend = PACKAGE_ROOT.parents[1] / "frontend"
    console = (frontend / "console.html").read_text(encoding="utf-8")
    page = (frontend / "pages/console.js").read_text(encoding="utf-8")
    controller = (frontend / "features/navigation/workspace-controller.js").read_text(encoding="utf-8")
    navigation_panel = console.split('data-panel="navigation"', 1)[1]
    for element_id in (
        "btnNavigate", "btnNavPickGoal", "navMaxVx", "navMaxVy", "navMaxVyaw",
        "navCollisionCheck", "navTimeout", "navOmniPlan", "btnNavCheckReachability",
        "btnNavStartWaypoints", "btnNavUndoWaypoint", "btnNavClearWaypoints",
        "btnNavSubmitWaypoints", "navWaypointList", "navMultiPointMode",
        "navTrajectorySpeedRatio", "navigationStatus",
    ):
        assert f'id="{element_id}"' in navigation_panel
    assert "'navigation', 'navigate_to_goal_v2'" in controller
    assert "frame_id: 'map'" in controller
    for removed in (
        "navGoalApi", "navFrame", "navigationInteractionHint",
        "btnNavPickInitialPose", "btnSendInitialPose", "btnMoveStraight",
        "btnNavigateVelocity", "btnNavigateRoute", "navRouteMode", "navRoutePoses", "sharedNavPcdFile",
        "sharedNavUrdfFile", "sharedNavTopoFile", "btnSharedNavTopoGo",
    ):
        assert removed not in navigation_panel
        assert removed not in controller
    for method in (
        "set_navigation_target", "navigate_with_velocity", "move_straight_to",
    ):
        assert method not in controller
    assert "'navigation', 'check_path_reachability'" in controller
    assert "'navigation', 'navigate_through_waypoints'" in controller
    assert "value === 'navigate_along_trajectory'" in controller
    assert "speed_ratio" in controller
    assert "this.scene.setNavigationWaypoints(this.routeWaypoints)" in controller
    assert "this.scene.setMode('pan')" in controller
    scene_commit = controller.split("onSceneGoalCommit(goal)", 1)[1].split("async run(", 1)[0]
    assert "this.sendGoal(pose)" not in scene_commit
    assert "目标已设置，请确认参数后点击“下发导航目标”" in scene_commit
    assert "navigation.bind()" in page
    assert "deactivate: () => navigation.deactivate()" in page
    assert "document.getElementById" not in controller


def test_shared_scene_keeps_only_its_required_local_three_runtime() -> None:
    frontend_root = PACKAGE_ROOT.parents[1] / "frontend"
    vendor_root = frontend_root / "scene" / "vendor" / "three"
    expected_files = {
        "LICENSE",
        "README.md",
        "three.module.js",
        "controls/OrbitControls.js",
        "lines/Line2.js",
        "lines/LineGeometry.js",
        "lines/LineMaterial.js",
        "lines/LineSegments2.js",
        "lines/LineSegmentsGeometry.js",
        "loaders/GLTFLoader.js",
        "utils/BufferGeometryUtils.js",
    }
    actual_files = {
        path.relative_to(vendor_root).as_posix()
        for path in vendor_root.rglob("*")
        if path.is_file()
    }
    assert actual_files == expected_files
    for obsolete in ("map-scene.js", "navigation-layer.js", "pcd-loader.js"):
        assert not (frontend_root / "scene" / obsolete).exists()


def test_event_subscribers_receive_the_active_navigation_path_snapshot() -> None:
    events = (PACKAGE_ROOT / "services" / "events.py").read_text(encoding="utf-8")
    assert 'self._publish_to(queue, "navigation_state", asdict(navigation))' in events
    assert 'self._publish_to(queue, "navigation_path", path)' in events


def test_motion_workspace_keeps_planning_preview_and_execution_separate() -> None:
    console = (PACKAGE_ROOT.parents[1] / "frontend/console.html").read_text(encoding="utf-8")
    page = (PACKAGE_ROOT.parents[1] / "frontend/pages/console.js").read_text(encoding="utf-8")
    controller = (PACKAGE_ROOT.parents[1] / "frontend/features/motion/controller.js").read_text(encoding="utf-8")
    motion_panel = console.split('data-panel="motion"', 1)[1].split('data-panel="navigation"', 1)[0]
    for element_id in (
        "motionChain", "motionX", "motionY", "motionZ", "motionCollisionCheck",
        "motionVisualizeTrajectory", "btnPlayMotionPlan", "motionExecuteEnabled", "btnExecuteMotionPlan",
    ):
        assert f'id="{element_id}"' in motion_panel
    assert 'class="workspace-primary-settings motion-simple-only"' in motion_panel
    assert motion_panel.index('id="motionChain"') < motion_panel.index('<section')
    assert "MotionWorkspaceController" in page
    assert "'motion', 'plan_preview'" in controller
    assert "'motion', 'execute_plan'" in controller
    assert "motionExecuteEnabled" in controller
    assert "/frontend/motion.html" not in motion_panel
    assert "/frontend/navigation.html" not in console
    for element_id in (
        "btnRestoreMotionPlan", "motionPlaybackScrubber", "motionPlaybackValue",
        "btnMotionSyncPose", "btnMotionPreviewPose",
        "btnSharedMotionLoadCandidate", "btnSharedMotionPreviewObstacle",
        "btnSharedMotionExitEdit", "motionServiceObstacleCount",
        "motionTrajectoryInfo", "motionTrajectoryDetail", "motionPlaybackProgress",
    ):
        assert f'id="{element_id}"' in motion_panel
    for removed_id in ("btnSharedMotionAddObstacle", "btnSharedMotionBounds", "btnSharedMotionDeleteCandidate"):
        assert removed_id not in motion_panel
        assert removed_id not in controller
    assert "remove.textContent = '×'" in controller
    assert "deactivate: () => motion.deactivate()" in page
    assert "dispose: () => motion.dispose()" in page
    assert "isPreviewing()" in controller
    assert "document.getElementById" not in controller
    controller_ids = set(re.findall(r"this\.\$\('([^']+)'\)", controller))
    assert not {element_id for element_id in controller_ids if f'id="{element_id}"' not in motion_panel}
    for removed_id in (
        "motionOffsetArm", "quickOffsetX", "quickOffsetY", "quickOffsetZ",
        "motionTargetMode", "motionOffsetX", "motionOffsetY", "motionOffsetZ", "motionFrame",
        "motionSelfCollision", "motionEnvCollision", "motionObstacles", "motionObstacleCount",
        "motionShowTool", "motionToolName", "motionToolLink", "motionToolOffset",
        "btnMotionApplyTool", "motionConfig",
    ):
        assert removed_id not in motion_panel
        assert removed_id not in controller
    assert motion_panel.count("<h2>末端偏移</h2>") == 1
    assert motion_panel.count('class="offset-row"') == 6
    for axis in ("X", "Y", "Z", "Roll", "Pitch", "Yaw"):
        assert f'<span>{axis}</span>' in motion_panel
    assert 'id="btnMotionApplyOffset"' in motion_panel
    assert "this.selectedOffsets = new Map()" in controller
    assert "for (const { key, sign } of this.selectedOffsets.values())" in controller
    assert "payload[key] += sign" in controller
    assert "if (!this.offsetExecuting" in controller
    assert "this.stopOffset(true)" in controller
    for key in ("dx", "dy", "dz", "roll", "pitch", "yaw"):
        row = next(line for line in motion_panel.splitlines() if f'data-offset="{key}"' in line)
        assert row.index('data-value="1"') < row.index('data-value="-1"')
    assert motion_panel.count("<h2>目标规划</h2>") == 1
    assert "target_mode: 'absolute'" in controller
    assert "frame_id: 'base_link'" in controller
    assert "collision_check: collisionCheck" in controller
    assert "environment_collision_check: collisionCheck" in controller
    assert "target_frame: 'map'" in controller
    assert "sharedMotionObstacleFrame" not in motion_panel
    assert "sharedMotionObstacleFrame" not in controller
    assert 'id="sharedMotionScaleFieldY"' in motion_panel
    assert 'id="sharedMotionScaleFieldZ"' in motion_panel
    for command in ("'motion', 'plan_preview'", "'motion', 'execute_plan'", "'motion', 'list_obstacles'", "'motion', 'add_obstacle'", "'motion', 'remove_obstacle'", "'motion', 'end_effector_pose'"):
        assert command not in page
        assert command in controller
    assert 'id="baseJoystick"' in console
    assert "setInterval(send, 100)" in controller
    robot_controller = (PACKAGE_ROOT.parents[1] / "frontend/features/robot/console-controller.js").read_text(encoding="utf-8")
    assert "duration: 0.0" in robot_controller
    assert "emergency_stop" in page
    assert "stop_controller" in page
    motion_controller = (PACKAGE_ROOT.parents[1] / "frontend/features/motion/controller.js").read_text(encoding="utf-8")
    assert "确认向真机发送末端偏移" not in motion_controller


def test_navigation_path_settings_are_top_level_workspace_context() -> None:
    console = (PACKAGE_ROOT.parents[1] / "frontend/console.html").read_text(encoding="utf-8")
    navigation_panel = console.split('data-panel="navigation"', 1)[1]
    assert 'class="workspace-primary-settings navigation-path-settings"' in navigation_panel
    assert navigation_panel.index('id="visNavigationPath"') < navigation_panel.index('<section')
    assert navigation_panel.index('id="navigationPathWidth"') < navigation_panel.index('<section')


def test_joint_preview_lists_only_21_core_urdf_joints() -> None:
    source = (PACKAGE_ROOT.parents[1] / "frontend/features/robot/joint-preview-controller.js").read_text(encoding="utf-8")
    declaration = source.split("];", 1)[0]
    names = re.findall(r"'(leg_joint\d|head_joint\d|(?:left|right)_arm_joint\d)'", declaration)
    assert len(names) == 21
    assert len(set(names)) == 21
    assert "gripper" not in declaration
    assert "joint.lower" in source and "joint.upper" in source


def test_joint_preview_upright_leg_mode_couples_three_urdf_limited_joints() -> None:
    source = (PACKAGE_ROOT.parents[1] / "frontend/features/robot/joint-preview-controller.js").read_text(encoding="utf-8")
    assert "const UPRIGHT_LEG_JOINT_NAMES = ['leg_joint1', 'leg_joint2', 'leg_joint3']" in source
    assert "joint1.lower * 3" in source
    assert "joint3.lower * 3 / 2" in source
    assert "leg2Position / 3" in source
    assert "leg2Position * 2 / 3" in source


def test_robot_workspace_owns_shared_scene_visibility_controls() -> None:
    console = (PACKAGE_ROOT.parents[1] / "frontend/console.html").read_text(encoding="utf-8")
    page = (PACKAGE_ROOT.parents[1] / "frontend/pages/console.js").read_text(encoding="utf-8")
    controller = (PACKAGE_ROOT.parents[1] / "frontend/features/robot/console-controller.js").read_text(encoding="utf-8")
    robot_panel = console.split('data-panel="robot"', 1)[1].split('data-panel="motion"', 1)[0]
    for control in ("visRobotModel", "visGroundGrid", "visStaticMap", "visLiveLidar"):
        assert f'id="{control}"' in robot_panel
        assert control in controller
    assert "setRobotVisible" in controller and "setGroundGridVisible" in controller
    assert "setRobotVisible" not in page and "setGroundGridVisible" not in page
    for control in ("visPowerStatus", "visRobotPose", "visJointLeg", "visJointHead", "visJointLeftArm", "visJointRightArm"):
        assert f'id="{control}"' in robot_panel
        assert control in controller
    assert "robotOverlays" in controller
    assert "updateOverlayVisibility" in page


def test_robot_workspace_exposes_only_map_and_base_coordinate_frame_switches() -> None:
    console = (PACKAGE_ROOT.parents[1] / "frontend/console.html").read_text(encoding="utf-8")
    controller = (PACKAGE_ROOT.parents[1] / "frontend/features/robot/console-controller.js").read_text(encoding="utf-8")
    workspace = (PACKAGE_ROOT.parents[1] / "frontend/features/robot/workspace-controller.js").read_text(encoding="utf-8")
    scene = (PACKAGE_ROOT.parents[1] / "frontend/scene/scene-map.js").read_text(encoding="utf-8")
    robot_panel = console.split('data-panel="robot"', 1)[1].split('data-panel="motion"', 1)[0]
    for control in ("visMapFrame", "visBaseFrame"):
        assert f'id="{control}"' in robot_panel
        assert f"this.$('{control}')" in controller
    assert 'id="visLidarFrame"' not in robot_panel
    assert "this.$('visLidarFrame')" not in controller
    assert "setCoordinateFrameVisible" in scene
    assert "setLidarFrame" not in scene
    assert "coordinateFrames.lidar" not in scene
    assert ".setLidarFrame(" not in workspace


def test_scene_coordinate_frames_use_colored_arrows_and_labels() -> None:
    scene = (PACKAGE_ROOT.parents[1] / "frontend/scene/scene-map.js").read_text(encoding="utf-8")
    assert "new THREE.ArrowHelper" in scene
    assert "x: 0xff3b30" in scene
    assert "y: 0x34c759" in scene
    assert "z: 0x0a84ff" in scene
    assert "sprite.position.set(COORDINATE_AXIS_LENGTH + 0.14" in scene
    assert "this._setCoordinateFrameLabel(group, label)" in scene


def test_shared_scene_exposes_view_shortcuts_and_planar_top_rotation() -> None:
    frontend = PACKAGE_ROOT.parents[1] / "frontend"
    console = (frontend / "console.html").read_text(encoding="utf-8")
    page = (frontend / "pages/console.js").read_text(encoding="utf-8")
    scene = (frontend / "scene/scene-map.js").read_text(encoding="utf-8")
    assert 'id="sceneShortcuts"' in console
    assert 'id="scenePointerHint"' in console
    assert "左键或右键平移 · Shift + 左键平面旋转" in page
    assert "this.viewMode !== 'top'" in scene
    assert "|| !e.shiftKey" in scene
    assert "camera.up.applyAxisAngle(new THREE.Vector3(0, 0, 1)" in scene
    assert "new THREE.Vector3(3.8, -3.8, 3.8)" in scene
    assert "scene.onFocusRobot = focusRobotView" in page
    assert "if (this.viewMode !== '3d') this.setViewMode('3d')" in scene
    assert 'id="btnFit"' not in console
    assert "btnFit" not in page
    assert "已从 45° 方向聚焦机器人" not in page


def test_robot_joystick_uses_radial_exponential_velocity_mapping() -> None:
    frontend = PACKAGE_ROOT.parents[1] / "frontend"
    console = (frontend / "console.html").read_text(encoding="utf-8")
    controller = (frontend / "features/robot/console-controller.js").read_text(encoding="utf-8")
    assert "轻推可精细调整" in console
    assert "低灵敏度非线性映射" not in console
    assert "const JOYSTICK_DEADZONE = 0.04" in controller
    assert "const JOYSTICK_EXPO = 0.75" in controller
    assert "normalized ** 2" in controller
    assert "Math.hypot(rawX, rawY)" in controller
    assert "shapeJoystickMagnitude(magnitude) * 0.75" in controller


def test_robot_workspace_exposes_lidar_point_limit_and_xyz_crop() -> None:
    console = (PACKAGE_ROOT.parents[1] / "frontend/console.html").read_text(encoding="utf-8")
    scene = (PACKAGE_ROOT.parents[1] / "frontend/scene/scene-map.js").read_text(encoding="utf-8")
    for control in ("staticCropEnabled", "staticCropX", "staticCropY", "staticCropZ", "lidarMaxPoints", "lidarCropEnabled", "lidarCropX", "lidarCropY", "lidarCropZ"):
        assert f'id="{control}"' in console
    assert "setStaticCloudViewOptions" in scene
    assert "localMaxPoints" in scene
    assert "_localCloudCropCenter" in scene
    robot_panel = console.split('data-panel="robot"', 1)[1].split('data-panel="motion"', 1)[0]
    assert robot_panel.count('<h2>场景资产与点云设置</h2>') == 1
    for subsection in ("本地场景资产", "静态地图裁剪", "实时雷达显示"):
        assert f'<h3>{subsection}</h3>' in robot_panel


def test_robot_status_overlays_are_grouped_and_controller_management_is_absent_from_shared_workspace() -> None:
    console = (PACKAGE_ROOT.parents[1] / "frontend/console.html").read_text(encoding="utf-8")
    page = (PACKAGE_ROOT.parents[1] / "frontend/pages/console.js").read_text(encoding="utf-8")
    controller = (PACKAGE_ROOT.parents[1] / "frontend/features/robot/console-controller.js").read_text(encoding="utf-8")
    robot_panel = console.split('data-panel="robot"', 1)[1].split('data-panel="motion"', 1)[0]
    for element_id in ("sceneJointLegReadout", "sceneJointHeadReadout", "sceneJointLeftArmReadout", "sceneJointRightArmReadout"):
        assert f'id="{element_id}"' in console
        assert element_id in controller
    for removed_id in ("btnRefreshRobot", "robotRunState"):
        assert removed_id not in robot_panel
        assert removed_id not in page
    for removed_id in (
        "robotControllerGroup", "robotControllerSelect", "btnRobotGetControllers",
        "btnRobotSetController", "robotControllerStatus",
    ):
        assert removed_id not in robot_panel
        assert removed_id not in controller
    for command in ("controller_snapshot", "switch_controller"):
        assert command not in page
        assert command not in controller
    assert "['x', 'y', 'z', 'qx', 'qy', 'qz', 'qw']" in controller
    for removed_id in ("btnCopyRobotPose", "robotPose", "robotJointCount"):
        assert removed_id not in robot_panel


def test_robot_non_camera_full_tool_is_owned_by_workspace_controller() -> None:
    frontend = PACKAGE_ROOT.parents[1] / "frontend"
    console = (frontend / "console.html").read_text(encoding="utf-8")
    page = (frontend / "pages/console.js").read_text(encoding="utf-8")
    controller = (frontend / "features/robot/console-controller.js").read_text(encoding="utf-8")
    scene_controller = (frontend / "features/robot/workspace-controller.js").read_text(encoding="utf-8")
    robot_panel = console.split('data-panel="robot"', 1)[1].split('data-panel="motion"', 1)[0]
    for element_id in (
        "visPowerStatus", "visRobotPose", "sceneRobotPoseReadout", "scenePowerElectrical", "scenePowerTemperature", "scenePowerCharging",
        "robotLocalPcdFile", "btnRobotRestoreStaticMap", "robotLocalUrdfFile", "btnRobotRestoreModel",
    ):
        assert f'id="{element_id}"' in console
        assert element_id in controller
    for removed_id in ("robotBmsBatteryFill", "robotBmsStatus", "robotBmsHealth"):
        assert removed_id not in robot_panel
        assert removed_id not in controller
    for command in ("set_joint_positions", "set_gripper_command", "base_velocity", "configure_lidar_stream"):
        assert command in controller
        assert command not in page
    assert "loadLocalPcd" in scene_controller
    assert "loadLocalUrdf" in scene_controller
    assert "activate: () => robotWorkspace.activate()" in page
    assert "deactivate: () => robotWorkspace.deactivate()" in page
    assert "dispose: () => robotWorkspace.dispose()" in page
    assert "document.getElementById" not in controller
