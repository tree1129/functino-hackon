"""GalbotNavigation native-object adapter."""
from typing import Any, Sequence


class PythonNavigationAdapter:
    def __init__(self, platform: Any) -> None:
        self._platform = platform

    def capabilities(self) -> list[str]:
        return [
            "snapshot", "navigate_to_goal", "navigate_to_goal_v2", "set_navigation_target",
            "navigate_through_waypoints", "navigate_along_trajectory", "navigate_with_velocity",
            "check_path_reachability", "get_navigation_status", "get_navigation_target_status",
            "navigation_path", "stop_navigation", "relocalize",
            "move_straight_to",
        ]

    def _task_result(self, result: Any) -> dict[str, Any]:
        navigation = self._platform.navigation_instance
        task_id = getattr(result, "task_id", None)
        request_sent = getattr(result, "request_sent", None)
        if request_sent is None and isinstance(result, tuple) and result:
            request_sent = bool(result[0])
        accepted = True if request_sent is None else bool(request_sent)
        if accepted and task_id is None and navigation is not None and hasattr(navigation, "get_active_navigation_task_id"):
            task_id = navigation.get_active_navigation_task_id()
        return {
            "result": result,
            "task_id": str(task_id) if task_id is not None else None,
            "request_sent": accepted,
        }

    def _prepare_navigation(self) -> tuple[Any, Any]:
        robot, navigation, sdk = self._platform.require_navigation()
        controller = getattr(sdk, f"{self._platform.model.upper()}ControllerName").CHASSIS_POSE_CTRL
        result = robot.switch_controller(controller)
        if result != sdk.ControlStatus.SUCCESS:
            raise RuntimeError(f"switch_controller({controller}) failed: {result}")
        return navigation, sdk

    def snapshot(self) -> dict[str, Any]:
        navigation = self._platform.navigation_instance
        if navigation is None:
            return {"localized": False, "pose": None, "status": "IDLE"}
        pose = [float(value) for value in navigation.get_current_pose()]
        status = navigation.get_navigation_status()
        return {
            "localized": bool(navigation.is_localized()),
            "pose": pose,
            "status": getattr(status, "name", str(status)),
        }

    def navigate_to_goal(self, pose: Sequence[float], collision_check: bool, timeout: float) -> Any:
        navigation, _ = self._prepare_navigation()
        if not hasattr(navigation, "get_active_navigation_task_id"):
            raise RuntimeError("installed Galbot SDK does not expose navigation task path support")
        result = navigation.navigate_to_goal(list(pose), collision_check, False, timeout)
        return self._task_result(result)

    def navigate_to_goal_v2(self, pose: Sequence[float], max_velocity: Sequence[float], frame_id: str, collision_check: bool, timeout: float, omni_plan: bool) -> dict[str, Any]:
        navigation, _ = self._prepare_navigation()
        result = navigation.navigate_to_goal_v2(list(pose), list(max_velocity), pose_frame=frame_id, enable_collision_check=collision_check, is_blocking=False, timeout=float(timeout), omni_plan=omni_plan)
        return self._task_result(result)

    def set_navigation_target(self, pose: Sequence[float], frame_id: str, speed_ratio: float, collision_check: bool) -> dict[str, Any]:
        navigation, sdk = self._prepare_navigation()
        result = navigation.set_navigation_target(sdk.Pose(list(pose)), frame_id, float(speed_ratio), collision_check)
        return self._task_result(result)

    def navigate_through_waypoints(self, poses: Sequence[Sequence[float]], frame_id: str, collision_check: bool) -> dict[str, Any]:
        navigation, sdk = self._prepare_navigation()
        waypoints = [sdk.Waypoint(sdk.Pose(list(pose)), sdk.WaypointParams()) for pose in poses]
        return self._task_result(navigation.navigate_through_waypoints(waypoints, frame_id, collision_check))

    def navigate_along_trajectory(self, poses: Sequence[Sequence[float]], frame_id: str, speed_ratio: float, collision_check: bool) -> dict[str, Any]:
        navigation, sdk = self._prepare_navigation()
        trajectory = [sdk.Pose(list(pose)) for pose in poses]
        return self._task_result(navigation.navigate_along_trajectory(trajectory, frame_id, float(speed_ratio), collision_check))

    def navigate_with_velocity(self, vx: float, vy: float, vyaw: float, duration: float, collision_check: bool) -> dict[str, Any]:
        navigation, _ = self._prepare_navigation()
        return self._task_result(navigation.navigate_with_velocity(float(vx), float(vy), float(vyaw), float(duration), collision_check))

    def check_path_reachability(self, goal: Sequence[float], current: Sequence[float]) -> bool:
        _, navigation, _ = self._platform.require_navigation()
        return bool(navigation.check_path_reachability(list(goal), list(current)))

    def navigation_target_status(self, task_id: str) -> str:
        navigation = self._platform.navigation_instance
        if navigation is None:
            return "IDLE"
        snapshot = navigation.get_navigation_target_status(task_id)
        status = getattr(snapshot, "status", snapshot)
        return getattr(status, "name", str(status))

    def navigation_path(self, task_id: str) -> dict[str, Any]:
        navigation = self._platform.navigation_instance
        if navigation is None:
            return {"task_id": task_id, "available": False, "revision": 0, "frame_id": "", "poses": [], "msg": "navigation is not initialized"}
        if not hasattr(navigation, "get_navigation_path"):
            raise RuntimeError("installed Galbot SDK does not expose get_navigation_path(task_id)")
        result = dict(navigation.get_navigation_path(task_id))
        result["poses"] = [
            {
                "x": float(pose[0]), "y": float(pose[1]), "z": float(pose[2]),
                "qx": float(pose[3]), "qy": float(pose[4]), "qz": float(pose[5]), "qw": float(pose[6]),
            }
            for pose in result.get("poses", [])
        ]
        return result

    def stop(self) -> Any:
        _, navigation, _ = self._platform.require_navigation()
        return navigation.stop_navigation()

    def relocalize(self, pose: Sequence[float]) -> Any:
        _, navigation, _ = self._platform.require_navigation()
        return navigation.relocalize(list(pose))

    def move_straight_to(self, pose: Sequence[float], timeout: float) -> Any:
        navigation, _ = self._prepare_navigation()
        return navigation.move_straight_to(list(pose), is_blocking=False, timeout=float(timeout))
