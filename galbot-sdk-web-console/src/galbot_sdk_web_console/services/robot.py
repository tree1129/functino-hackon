"""Robot state and controller use cases."""
import threading
from typing import Any

from ..sdk.session import SdkSession

JOINT_GROUPS = {
    "leg": {"leg_joint1", "leg_joint2", "leg_joint3", "leg_joint4", "leg_joint5"},
    "head": {"head_joint1", "head_joint2"},
    "left_arm": {f"left_arm_joint{index}" for index in range(1, 8)},
    "right_arm": {f"right_arm_joint{index}" for index in range(1, 8)},
}


class RobotService:
    def __init__(self, session: SdkSession, live: bool = False) -> None:
        self.session = session
        self.live = live
        self.lidar_stream_config: dict[str, Any] = {"enabled": True, "max_points": 8000, "crop": None}
        # Display and recording may sample concurrently. Galbot SDK read calls
        # are serialized here so a recording never races the latest-state feed.
        self._snapshot_lock = threading.Lock()
        self._camera_locks: dict[str, threading.Lock] = {}
        self._camera_locks_guard = threading.Lock()

    def snapshot(self, include_lidar: bool = False, lidar_center: dict[str, float] | None = None, include_sensors: bool = False, include_joints: bool = True) -> dict[str, Any]:
        if not self.live or not self.session.initialized:
            return {"joint_state": None, "lidar": None, "sensors": None, "dry_run": True}
        lidar_options = None
        if include_lidar:
            lidar_options = {
                "max_points": self.lidar_stream_config["max_points"],
                "crop": self.lidar_stream_config["crop"],
                "center": lidar_center or {"x": 0.0, "y": 0.0, "z": 0.0},
            }
        options: dict[str, Any] = {"include_lidar": include_lidar, "lidar_options": lidar_options, "include_joints": include_joints}
        if include_sensors:
            options["include_sensors"] = True
        with self._snapshot_lock:
            return {**self.session.platform.robot.snapshot(**options), "dry_run": False}

    def configure_lidar_stream(self, config: dict[str, Any]) -> dict[str, Any]:
        self.lidar_stream_config = {"enabled": config["enabled"], "max_points": config["max_points"], "crop": config.get("crop")}
        return {"accepted": True, "dry_run": not self.live, **self.lidar_stream_config}

    def camera_frame(self, source: str, last_timestamp_ns: int | None = None, preview_depth: bool = True) -> dict[str, Any]:
        if not self.live or not self.session.initialized:
            raise RuntimeError("camera frames are only available in live SDK mode")
        with self._camera_locks_guard:
            source_lock = self._camera_locks.setdefault(source, threading.Lock())
        with source_lock:
            return {**self.session.platform.robot.camera_frame(source, last_timestamp_ns, preview_depth), "dry_run": False}

    def set_joint_positions(self, positions: list[float], joint_names: list[str], max_speed: float, timeout: float) -> dict[str, Any]:
        if len(positions) != len(joint_names) or not joint_names:
            raise ValueError("joint positions and joint names must be non-empty and have matching lengths")
        if not any(set(joint_names).issubset(group) for group in JOINT_GROUPS.values()):
            raise ValueError("joint command must contain joints from exactly one of: leg, head, left_arm, right_arm")
        if not 0.01 <= max_speed <= 0.5:
            raise ValueError("joint max_speed must be between 0.01 and 0.5 rad/s")
        if not 1 <= timeout <= 60:
            raise ValueError("joint timeout must be between 1 and 60 seconds")
        if not self.live or not self.session.initialized: return {"accepted": True, "dry_run": True}
        return {"accepted": True, "dry_run": False, "sdk_result": str(self.session.platform.robot.set_joint_positions(positions, joint_names, max_speed, timeout))}

    def set_gripper_command(self, side: str, width: float, speed: float, effort: float) -> dict[str, Any]:
        if side not in {"left", "right"}: raise ValueError("gripper side must be left or right")
        if not self.live or not self.session.initialized: return {"accepted": True, "dry_run": True}
        return {"accepted": True, "dry_run": False, "sdk_result": str(self.session.platform.robot.set_gripper_command(side, width, speed, effort))}

    def controller_snapshot(self, group: str) -> dict[str, Any]:
        if not self.live or not self.session.initialized:
            return {"group": group, "active": None, "available": [], "dry_run": True}
        return {**self.session.platform.robot.controller_snapshot(group), "dry_run": False}

    def switch_controller(self, controller: str) -> dict[str, Any]:
        if not controller:
            raise ValueError("controller is required")
        if not self.live or not self.session.initialized:
            return {"accepted": True, "dry_run": True, "controller": controller}
        result = self.session.platform.robot.switch_controller(controller)
        return {"accepted": True, "dry_run": False, "sdk_result": str(result)}

    def set_base_velocity(self, linear, angular, duration):
        if not self.live or not self.session.initialized:
            return {"accepted": True, "dry_run": True, "linear": linear, "angular": angular, "duration": duration}
        return {"accepted": True, "dry_run": False, "sdk_result": str(self.session.platform.robot.set_base_velocity(linear, angular, duration))}

    def emergency_stop(self) -> dict[str, Any]:
        if not self.live or not self.session.initialized:
            return {"accepted": True, "dry_run": True, "status": "EMERGENCY_STOP"}
        return {"accepted": True, "dry_run": False, "status": "EMERGENCY_STOP", "sdk_result": str(self.session.platform.robot.emergency_stop())}

    def resume_emergency_stop(self) -> dict[str, Any]:
        if not self.live or not self.session.initialized:
            return {"accepted": True, "dry_run": True, "status": "RESUME_REQUESTED"}
        result = self.session.platform.robot.resume_from_emergency_stop()
        return {"accepted": True, "dry_run": False, "status": "RESUME_REQUESTED", "sdk_result": str(result)}
