"""GalbotMotion planning and guarded trajectory execution adapter."""
from math import isfinite
from typing import Any
import numpy as np

from ...geometry.transforms import compose_poses, pose_array

MAX_EXECUTE_POINTS = 2000


class PythonMotionAdapter:
    def __init__(self, platform: object) -> None:
        self._platform = platform

    def capabilities(self) -> list[str]:
        return ["cartesian_plan", "trajectory_preview", "execute_planned_trajectory", "end_effector_offset", "stop_controller", "persistent_obstacles"]

    def list_obstacles(self) -> dict[str, list[str]]:
        _, motion, _ = self._platform.require_motion()
        loaded = [str(value) for value in motion.get_built_obstacles_list()]
        return {"obstacles": loaded, "default_obstacle_ids": [item for item in loaded if item == "ground"]}

    def add_obstacle(self, obstacle: dict[str, Any]) -> None:
        # Persistent obstacles use the same SDK signature as the annotator's
        # /obstacles/add endpoint.  Reference state is only for temporary
        # obstacles injected into an individual planning request.
        _, motion, sdk = self._platform.require_motion()
        status = motion.add_obstacle(
            obstacle_id=obstacle["obstacle_id"], obstacle_type=obstacle["obstacle_type"],
            pose=obstacle["pose"], scale=obstacle["scale"], target_frame=obstacle["target_frame"],
        )
        if status != sdk.MotionStatus.SUCCESS:
            raise RuntimeError(f"添加碰撞体失败: {self._status_name(status)}")

    def remove_obstacle(self, obstacle_id: str) -> None:
        _, motion, sdk = self._platform.require_motion()
        status = motion.remove_obstacle(obstacle_id)
        if status != sdk.MotionStatus.SUCCESS:
            raise RuntimeError(f"删除碰撞体失败: {self._status_name(status)}")

    @staticmethod
    def _status_name(status: Any) -> str:
        return str(getattr(status, "name", status)).rsplit(".", 1)[-1]

    def plan(self, request: dict[str, Any]) -> dict[str, Any]:
        robot, motion, sdk = self._platform.require_motion()
        chain = request["chain"]
        target_pose = pose_array(request["target_pose"])
        if target_pose is None:
            raise ValueError("target_pose 必须是有效的 [x,y,z,qx,qy,qz,qw]")

        if request.get("target_mode") == "current_offset":
            status, current = motion.get_end_effector_pose_on_chain(chain, "EndEffector", request["frame_id"])
            if status != sdk.MotionStatus.SUCCESS:
                raise RuntimeError(f"读取 {chain} 当前末端位姿失败: {self._status_name(status)}")
            current_pose = pose_array(current)
            if current_pose is None:
                raise RuntimeError("SDK 返回的末端位姿格式无效")
            target_pose = current_pose.copy()
            target_pose[:3] += request["position_offset"]

        joint_names = list(motion.get_chain_joint_names(chain))
        if not joint_names:
            raise RuntimeError(f"SDK 未返回 {chain} 的规划关节")
        start_positions = [float(value) for value in robot.get_joint_positions([], joint_names)]
        if len(start_positions) != len(joint_names):
            raise RuntimeError(f"读取 {chain} 规划起点失败")

        # motion_plan() accepts a RobotStates subclass (PoseState or
        # JointStates), not MotionPlanChainTarget.  The latter is for the
        # waypoint-planner overload and causes a pybind signature error on the
        # deployed G1 SDK.
        target = sdk.PoseState()
        target.chain_name = chain
        target.frame_id = "EndEffector"
        target.reference_frame = request["frame_id"]
        target.pose = sdk.Pose(target_pose.tolist())

        reference_state = sdk.RobotStates()
        whole_body = []
        for group in ("leg", "head", "left_arm", "right_arm"):
            try:
                whole_body.extend(float(value) for value in robot.get_joint_positions([group], []))
            except Exception:
                continue
        if whole_body:
            reference_state.set_whole_body_joint(whole_body)

        params = sdk.Parameter()
        # Use the public Parameter API exposed by the current SDK rather than
        # relying on pybind field assignment.  This matches the G1 SDK samples.
        params.set_direct_execute(False)
        params.set_blocking(True)
        params.set_check_collision(request["collision_check"])
        params.set_timeout(request["timeout_seconds"])
        params.set_tool_pose(True)
        params.set_reference_frame(request["frame_id"])
        if hasattr(params, "enable_env_collision_check"):
            params.enable_env_collision_check = request["environment_collision_check"]
        loaded_obstacles = []
        try:
            if request["environment_collision_check"]:
                for obstacle in request.get("obstacles", []):
                    obstacle_id = str(obstacle.get("obstacle_id", "")).strip()
                    if not obstacle_id:
                        raise ValueError("障碍物缺少 obstacle_id")
                    status = motion.add_obstacle(
                        obstacle_id=f"console_{obstacle_id}",
                        obstacle_type=obstacle["obstacle_type"],
                        pose=obstacle["pose"],
                        scale=obstacle["scale"],
                        target_frame=obstacle.get("target_frame", "base_link"),
                        reference_joint_positions=whole_body,
                        reference_base_pose=request.get("base_pose", [0, 0, 0, 0, 0, 0, 1]),
                    )
                    if status != sdk.MotionStatus.SUCCESS:
                        raise RuntimeError(f"添加障碍物 {obstacle_id} 失败: {self._status_name(status)}")
                    loaded_obstacles.append(f"console_{obstacle_id}")
            status, raw = motion.motion_plan(
                target=target,
                start=None,
                reference_robot_states=reference_state,
                enable_collision_check=request["collision_check"],
                params=params,
            )
        finally:
            remove_obstacle = getattr(motion, "remove_obstacle", None)
            if remove_obstacle:
                for obstacle_id in loaded_obstacles:
                    try:
                        remove_obstacle(obstacle_id)
                    except Exception:
                        pass
        success = status == sdk.MotionStatus.SUCCESS
        positions = [[float(value) for value in point] for point in (raw.get(chain, []) if raw else [])]
        return {
            "success": success,
            "status": self._status_name(status),
            "chain": chain,
            "joint_names": joint_names,
            "positions": positions,
            "start_positions": start_positions,
            "target_pose": target_pose.tolist(),
        }

    def execute(self, plan: dict[str, Any], speed_rad_s: float, start_tolerance_rad: float) -> dict[str, Any]:
        robot, sdk = self._platform.require_robot()
        joint_names = list(plan["joint_names"])
        frames = [[float(value) for value in frame] for frame in plan["positions"]]
        if not frames or len(frames) > MAX_EXECUTE_POINTS:
            raise ValueError("规划轨迹为空或轨迹点数超过执行上限")
        if any(len(frame) != len(joint_names) or not all(isfinite(value) for value in frame) for frame in frames):
            raise ValueError("规划轨迹包含无效关节数据")
        current = [float(value) for value in robot.get_joint_positions([], joint_names)]
        if len(current) != len(joint_names):
            raise RuntimeError("执行前读取真机关节状态失败")
        max_error = max(abs(actual - planned) for actual, planned in zip(current, frames[0]))
        if max_error > start_tolerance_rad:
            raise RuntimeError(
                f"真机已偏离规划起点 {max_error:.3f} rad，超过 {start_tolerance_rad:.3f} rad，拒绝执行"
            )

        max_step = max(
            (abs(current_value - previous_value)
             for previous, current_frame in zip(frames, frames[1:])
             for previous_value, current_value in zip(previous, current_frame)),
            default=0.0,
        )
        intervals = max(len(frames) - 1, 1)
        duration = max(intervals * 0.05, intervals * max_step / speed_rad_s)
        trajectory = sdk.Trajectory()
        trajectory.joint_names = joint_names
        points = []
        for index, frame in enumerate(frames):
            commands = []
            for value in frame:
                command = sdk.JointCommand()
                command.position = value
                commands.append(command)
            point = sdk.TrajectoryPoint()
            point.joint_command_vec = commands
            point.time_from_start_second = index * duration / intervals
            points.append(point)
        trajectory.points = points
        status = robot.execute_joint_trajectory(trajectory, True)
        success = status == sdk.ControlStatus.SUCCESS
        return {
            "success": success,
            "status": self._status_name(status),
            "point_count": len(frames),
            "duration_seconds": duration,
            "max_start_error_rad": max_error,
        }

    def move_end_effector_offset(self, arm: str, offset, rpy=None) -> Any:
        robot, motion, sdk = self._platform.require_motion()
        status, pose = motion.get_end_effector_pose_on_chain(arm, "EndEffector", "base_link")
        if status != sdk.MotionStatus.SUCCESS:
            raise RuntimeError(f"读取 {arm} 当前末端位姿失败: {self._status_name(status)}")
        current = pose_array(pose)
        if current is None:
            raise RuntimeError("SDK 返回的末端位姿格式无效")
        target = current.copy()
        target[:3] += offset
        if rpy is not None:
            roll, pitch, yaw = np.asarray(rpy, dtype=np.float64)
            cr, sr = np.cos(roll / 2), np.sin(roll / 2)
            cp, sp = np.cos(pitch / 2), np.sin(pitch / 2)
            cy, sy = np.cos(yaw / 2), np.sin(yaw / 2)
            delta = np.asarray([sr * cp * cy - cr * sp * sy, cr * sp * cy + sr * cp * sy, cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy])
            target[3:] = compose_poses(np.r_[np.zeros(3), delta], np.r_[np.zeros(3), target[3:]])[3:]
        return motion.set_end_effector_pose(
            target_pose=target.tolist(), end_effector_frame=arm, reference_frame="base_link",
            enable_collision_check=True, is_blocking=False, timeout=5.0, params=sdk.Parameter(),
        )

    def end_effector_pose(self, arm: str) -> list[float]:
        _, motion, sdk = self._platform.require_motion()
        status, pose = motion.get_end_effector_pose_on_chain(arm, "EndEffector", "base_link")
        if status != sdk.MotionStatus.SUCCESS:
            raise RuntimeError(f"读取 {arm} 末端位姿失败: {self._status_name(status)}")
        normalized = pose_array(pose)
        if normalized is None:
            raise RuntimeError("SDK 返回的末端位姿格式无效")
        return normalized.tolist()
