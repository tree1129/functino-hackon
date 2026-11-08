"""Command registry for the generic HTTP command endpoint."""
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from ..api.schemas import BaseVelocityRequest, ControllerSwitchRequest, LidarStreamConfigRequest, MotionExecuteRequest, MotionObstacleRemoveRequest, MotionObstacleRequest, MotionOffsetRequest, MotionPlanPreviewRequest, MoveStraightRequest, NavigationGoalRequest, NavigationGoalV2Request, NavigationReachabilityRequest, NavigationRouteRequest, NavigationTargetRequest, NavigationVelocityRequest, PoseRequest, RobotSnapshotRequest
from ..services.runtime import ConsoleRuntime

CommandHandler = Callable[[ConsoleRuntime, dict[str, Any]], dict[str, Any]]


def _navigate(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = NavigationGoalRequest.model_validate(payload)
    return runtime.navigation.go(
        request.to_sdk_array(),
        frame_id=request.frame_id,
        collision_check=request.collision_check,
        timeout=request.timeout,
    )


def _stop(runtime: ConsoleRuntime, _: dict[str, Any]) -> dict[str, Any]:
    return runtime.navigation.stop()


def _navigate_v2(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = NavigationGoalV2Request.model_validate(payload)
    return runtime.navigation.go_v2(
        request.to_sdk_array(), max_velocity=request.max_velocity, frame_id=request.frame_id,
        collision_check=request.collision_check, timeout=request.timeout, omni_plan=request.omni_plan,
    )


def _set_navigation_target(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = NavigationTargetRequest.model_validate(payload)
    return runtime.navigation.set_target(
        request.to_sdk_array(), frame_id=request.frame_id,
        speed_ratio=request.speed_ratio, collision_check=request.collision_check,
    )


def _navigate_route(runtime: ConsoleRuntime, payload: dict[str, Any], *, trajectory: bool) -> dict[str, Any]:
    request = NavigationRouteRequest.model_validate(payload)
    return runtime.navigation.navigate_route(
        request.poses, frame_id=request.frame_id, speed_ratio=request.speed_ratio,
        collision_check=request.collision_check, trajectory=trajectory, confirmed=request.confirmed,
    )


def _navigate_waypoints(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    return _navigate_route(runtime, payload, trajectory=False)


def _navigate_trajectory(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    return _navigate_route(runtime, payload, trajectory=True)


def _navigate_velocity(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = NavigationVelocityRequest.model_validate(payload)
    return runtime.navigation.navigate_velocity(**request.model_dump())


def _check_reachability(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = NavigationReachabilityRequest.model_validate(payload)
    return runtime.navigation.check_reachability(request.goal, request.current)


def _relocalize(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = PoseRequest.model_validate(payload)
    return runtime.navigation.relocalize(request.to_sdk_array(), frame_id=request.frame_id)


def _controller_snapshot(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    return runtime.robot.controller_snapshot(str(payload.get("group", "chassis")))


def _switch_controller(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = ControllerSwitchRequest.model_validate(payload)
    if not request.confirmed:
        raise ValueError("controller switch requires explicit confirmation")
    return runtime.robot.switch_controller(request.controller)


def _robot_snapshot(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = RobotSnapshotRequest.model_validate(payload)
    return runtime.robot.snapshot(include_lidar=request.include_lidar)


def _configure_lidar_stream(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = LidarStreamConfigRequest.model_validate(payload)
    return runtime.robot.configure_lidar_stream(request.model_dump())


def _plan_preview(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = MotionPlanPreviewRequest.model_validate(payload)
    return runtime.motion.plan_preview(**request.model_dump())


def _execute_motion_plan(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = MotionExecuteRequest.model_validate(payload)
    return runtime.motion.execute_plan(**request.model_dump())


def _motion_offset(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = MotionOffsetRequest.model_validate(payload)
    return runtime.motion.move_end_effector_offset(**request.model_dump())


def _base_velocity(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = BaseVelocityRequest.model_validate(payload)
    return runtime.robot.set_base_velocity(request.linear, request.angular, request.duration)


def _robot_emergency_stop(runtime: ConsoleRuntime, _: dict[str, Any]) -> dict[str, Any]:
    return runtime.robot.emergency_stop()

def _robot_resume_emergency_stop(runtime: ConsoleRuntime, _: dict[str, Any]) -> dict[str, Any]:
    return runtime.robot.resume_emergency_stop()

def _set_joint_positions(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    return runtime.robot.set_joint_positions([float(value) for value in payload.get("positions", [])], [str(value) for value in payload.get("joint_names", [])], float(payload.get("max_speed", 0.1)), float(payload.get("timeout", 10)))

def _set_gripper_command(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    return runtime.robot.set_gripper_command(str(payload.get("side", "left")), float(payload.get("width", 0.05)), float(payload.get("speed", 0.05)), float(payload.get("effort", 10)))


def _motion_stop_controller(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    return runtime.motion.stop_controller(group=str(payload.get("group", "all")))


def _motion_end_effector_pose(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    return runtime.motion.end_effector_pose(arm=str(payload.get("arm", "left_arm")))


def _list_motion_obstacles(runtime: ConsoleRuntime, _: dict[str, Any]) -> dict[str, Any]:
    return runtime.motion.list_obstacles()


def _add_motion_obstacle(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    return runtime.motion.add_obstacle(MotionObstacleRequest.model_validate(payload).model_dump())


def _remove_motion_obstacle(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    return runtime.motion.remove_obstacle(MotionObstacleRemoveRequest.model_validate(payload).obstacle_id)


def _move_straight_to(runtime: ConsoleRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    request = MoveStraightRequest.model_validate(payload)
    return runtime.navigation.move_straight_to(**request.model_dump())


_COMMANDS: dict[tuple[str, str], CommandHandler] = {
    ("navigation", "go"): _navigate,
    ("navigation", "navigate_to_goal"): _navigate,
    ("navigation", "navigate_to_goal_v2"): _navigate_v2,
    ("navigation", "set_navigation_target"): _set_navigation_target,
    ("navigation", "navigate_through_waypoints"): _navigate_waypoints,
    ("navigation", "navigate_along_trajectory"): _navigate_trajectory,
    ("navigation", "navigate_with_velocity"): _navigate_velocity,
    ("navigation", "check_path_reachability"): _check_reachability,
    ("navigation", "stop"): _stop,
    ("navigation", "relocalize"): _relocalize,
    ("navigation", "set_initial_pose"): _relocalize,
    ("robot", "controller_snapshot"): _controller_snapshot,
    ("robot", "switch_controller"): _switch_controller,
    ("robot", "snapshot"): _robot_snapshot,
    ("robot", "configure_lidar_stream"): _configure_lidar_stream,
    ("robot", "base_velocity"): _base_velocity,
    ("robot", "emergency_stop"): _robot_emergency_stop,
    ("robot", "resume_emergency_stop"): _robot_resume_emergency_stop,
    ("robot", "set_joint_positions"): _set_joint_positions,
    ("robot", "set_gripper_command"): _set_gripper_command,
    ("motion", "plan_preview"): _plan_preview,
    ("motion", "execute_plan"): _execute_motion_plan,
    ("motion", "move_end_effector_offset"): _motion_offset,
    ("motion", "stop_controller"): _motion_stop_controller,
    ("motion", "end_effector_pose"): _motion_end_effector_pose,
    ("motion", "list_obstacles"): _list_motion_obstacles,
    ("motion", "add_obstacle"): _add_motion_obstacle,
    ("motion", "remove_obstacle"): _remove_motion_obstacle,
    ("navigation", "base_velocity"): _base_velocity,
    ("navigation", "move_straight_to"): _move_straight_to,
}


def dispatch_command(
    runtime: ConsoleRuntime,
    service: str,
    method: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    handler = _COMMANDS.get((service, method))
    if handler is None:
        raise HTTPException(status_code=404, detail=f"unsupported SDK command: {service}.{method}")
    try:
        return handler(runtime, payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
