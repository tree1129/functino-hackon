"""Motion planning, preview ownership, and guarded execution use cases."""
import time
import uuid
from typing import Any

from ..sdk.session import SdkSession


class MotionService:
    def __init__(self, session: SdkSession, live: bool = False) -> None:
        self.session = session
        self.live = live
        self._plans: dict[str, dict[str, Any]] = {}
        # Definitions submitted through this console.  The SDK can enumerate
        # loaded IDs but does not expose the original geometry, so retain it
        # here for the editor and visualizer.
        self._obstacles: dict[str, dict[str, Any]] = {}

    def capabilities(self) -> list[str]:
        return ["plan_preview", "execute_plan", "list_obstacles", "add_obstacle", "remove_obstacle", *self.session.platform.motion.capabilities()]

    def list_obstacles(self) -> dict[str, Any]:
        loaded_ids: list[str] = []
        defaults: list[str] = []
        if self.live and self.session.initialized:
            result = self.session.platform.motion.list_obstacles()
            loaded_ids = result["obstacles"]
            defaults = result["default_obstacle_ids"]
        known = [
            self._obstacles[key]
            for key in self._obstacles
            if not (self.live and self.session.initialized) or key in loaded_ids
        ]
        # IDs created outside the console are intentionally shown too, even
        # though their geometry is unavailable to the browser.
        external = [{"obstacle_id": item, "external": True} for item in loaded_ids if item not in self._obstacles]
        return {"obstacles": [*known, *external], "default_obstacle_ids": defaults, "dry_run": not self.live or not self.session.initialized}

    def add_obstacle(self, obstacle: dict[str, Any]) -> dict[str, Any]:
        obstacle_id = obstacle["obstacle_id"]
        if obstacle_id in self._obstacles:
            raise ValueError(f"碰撞体 {obstacle_id} 已存在，请使用不同名称")
        if self.live and self.session.initialized:
            try:
                self.session.platform.motion.add_obstacle(obstacle)
            except Exception as error:
                raise ValueError(f"Motion 未能加载碰撞体 {obstacle_id}: {error}") from error
        self._obstacles[obstacle_id] = obstacle
        return {"accepted": True, "obstacle": obstacle, "dry_run": not self.live or not self.session.initialized}

    def remove_obstacle(self, obstacle_id: str) -> dict[str, Any]:
        if obstacle_id == "ground":
            raise ValueError("Motion 默认 ground 碰撞体不可删除")
        if self.live and self.session.initialized:
            try:
                self.session.platform.motion.remove_obstacle(obstacle_id)
            except Exception as error:
                raise ValueError(f"Motion 未能删除碰撞体 {obstacle_id}: {error}") from error
        existed = self._obstacles.pop(obstacle_id, None) is not None
        return {"accepted": True, "removed": obstacle_id, "known": existed, "dry_run": not self.live or not self.session.initialized}

    def plan_preview(
        self,
        *,
        chain: str,
        target_pose: list[float],
        frame_id: str,
        collision_check: bool,
        environment_collision_check: bool,
        obstacles: list[dict],
        target_mode: str = "absolute",
        position_offset: list[float] | None = None,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        request = {
            "chain": chain,
            "target_pose": target_pose,
            "target_mode": target_mode,
            "position_offset": position_offset or [0.0, 0.0, 0.0],
            "frame_id": frame_id,
            "collision_check": collision_check,
            "environment_collision_check": environment_collision_check,
            "obstacles": obstacles,
            "timeout_seconds": timeout_seconds,
        }
        if not self.live or not self.session.initialized:
            return {
                "accepted": True,
                "dry_run": True,
                "plan_only": True,
                "sdk_planner_called": False,
                "request": request,
            }
        try:
            result = self.session.platform.motion.plan(request)
        except Exception as error:
            # A planner/SDK failure is an expected planning outcome.  Returning
            # it as structured data keeps the UI actionable instead of turning
            # an SDK exception into an opaque HTTP 500 response.
            return {
                "accepted": False,
                "dry_run": False,
                "plan_only": True,
                "sdk_planner_called": True,
                "success": False,
                "status": "PLANNING_ERROR",
                "detail": str(error) or error.__class__.__name__,
                "request": request,
            }
        response = {**result, "accepted": True, "dry_run": False, "plan_only": True, "sdk_planner_called": True}
        if result["success"] and result["positions"]:
            plan_id = uuid.uuid4().hex
            self._plans = {plan_id: {**result, "created_at": time.monotonic(), "executed": False}}
            response["plan_id"] = plan_id
        return response

    def execute_plan(
        self,
        *,
        plan_id: str,
        confirmed: bool,
        speed_rad_s: float,
        start_tolerance_rad: float,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("必须明确确认后才能执行实机轨迹")
        if not self.live or not self.session.initialized:
            return {"accepted": False, "dry_run": True, "status": "LIVE_MODE_REQUIRED"}
        plan = self._plans.get(plan_id)
        if not plan:
            raise ValueError("规划不存在或已被新的规划替换，请重新规划")
        if plan["executed"]:
            raise ValueError("该规划已经执行过，请重新规划")
        if time.monotonic() - plan["created_at"] > 120.0:
            self._plans.pop(plan_id, None)
            raise ValueError("规划已超过 120 秒，请重新规划")
        result = self.session.platform.motion.execute(plan, speed_rad_s, start_tolerance_rad)
        if result["success"]:
            plan["executed"] = True
        return {**result, "accepted": result["success"], "dry_run": False, "plan_id": plan_id}

    def move_end_effector_offset(self, *, arm: str, dx: float, dy: float, dz: float, roll: float, pitch: float, yaw: float, confirmed: bool) -> dict[str, Any]:
        if not self.live or not self.session.initialized:
            return {"accepted": False, "dry_run": True, "status": "LIVE_MODE_REQUIRED"}
        result = self.session.platform.motion.move_end_effector_offset(arm, [dx, dy, dz], [roll, pitch, yaw])
        return {"accepted": True, "dry_run": False, "sdk_result": str(result)}

    def stop_controller(self, *, group: str = "all") -> dict[str, Any]:
        if not self.live or not self.session.initialized:
            return {"accepted": True, "dry_run": True, "status": "CONTROLLER_STOPPED", "group": group}
        result = self.session.platform.robot.stop_controller(group)
        return {"accepted": True, "dry_run": False, "status": "CONTROLLER_STOPPED", "group": group, "sdk_result": str(result)}

    def end_effector_pose(self, *, arm: str) -> dict[str, Any]:
        if not self.live or not self.session.initialized:
            return {"available": False, "dry_run": True, "arm": arm}
        return {"available": True, "dry_run": False, "arm": arm, "pose": self.session.platform.motion.end_effector_pose(arm), "frame_id": "base_link"}
