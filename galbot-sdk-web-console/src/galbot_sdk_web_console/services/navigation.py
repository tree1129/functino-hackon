"""Navigation use cases, task state, and dry-run behavior."""

import math
import time
import uuid
from typing import Any, Sequence

from ..sdk.session import SdkSession
from .models import NavigationSnapshot


def pose_json(values: Sequence[float], frame_id: str = "map") -> dict[str, Any]:
    values = list(values)
    if len(values) != 7:
        raise ValueError(f"SDK pose must contain 7 values, got {len(values)}")
    keys = ("x", "y", "z", "qx", "qy", "qz", "qw")
    return {**dict(zip(keys, map(float, values))), "frame_id": frame_id}


class NavigationService:
    def __init__(self, session: SdkSession, live: bool = False) -> None:
        self.session = session
        self.live = live
        self._snapshot = NavigationSnapshot(connected=session.initialized)
        self._tasks: list[dict[str, Any]] = []

    def snapshot(self) -> NavigationSnapshot:
        if not self.session.initialized:
            return self._snapshot
        report = self.session.platform.navigation.snapshot()
        pose = pose_json(report["pose"]) if report["pose"] else None
        status = report["status"]
        detail = self._snapshot.detail
        if self._snapshot.task_id:
            status = self.session.platform.navigation.navigation_target_status(self._snapshot.task_id)
            detail = f"task {self._snapshot.task_id}: {status}"
            self._update_task_status(self._snapshot.task_id, status)
        self._snapshot = NavigationSnapshot(
            True, report["localized"], pose, status, self._snapshot.task_id, detail
        )
        return self._snapshot

    def go(
        self,
        target: Sequence[float],
        *,
        frame_id: str = "map",
        collision_check: bool = True,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        target_json = pose_json(target, frame_id)
        if not self.live or not self.session.initialized:
            return self._record_task(target_json, {}, dry_run=True)
        result = self.session.platform.navigation.navigate_to_goal(
            target, collision_check, timeout
        )
        return self._record_task(target_json, result, dry_run=False)

    def _record_task(
        self,
        target: dict[str, Any] | None,
        result: dict[str, Any],
        *,
        dry_run: bool,
        mode: str = "goal",
    ) -> dict[str, Any]:
        accepted = bool(result.get("request_sent", True))
        task_id = result.get("task_id") if accepted else None
        if dry_run:
            task_id = task_id or f"nav_{uuid.uuid4().hex}"
        self._snapshot = NavigationSnapshot(
            self.session.initialized,
            self._snapshot.localized,
            target or self._snapshot.pose,
            "RUNNING" if accepted else "REJECTED",
            task_id,
        )
        if task_id:
            self._tasks = [task for task in self._tasks if task["task_id"] != task_id]
            self._tasks.append({
                "task_id": task_id,
                "mode": mode,
                "status": "RUNNING" if accepted else "REJECTED",
                "target": target,
                "created_ns": time.time_ns(),
                "dry_run": dry_run,
            })
            self._tasks = self._tasks[-50:]
        return {
            "accepted": accepted,
            "task_id": task_id,
            "dry_run": dry_run,
            "target": target,
            "sdk_result": str(result.get("result", "dry-run")),
        }

    def go_v2(self, target: Sequence[float], *, max_velocity: Sequence[float], frame_id: str, collision_check: bool, timeout: float, omni_plan: bool) -> dict[str, Any]:
        target_json = pose_json(target, frame_id)
        if not self.live or not self.session.initialized:
            return self._record_task(target_json, {}, dry_run=True)
        result = self.session.platform.navigation.navigate_to_goal_v2(target, max_velocity, frame_id, collision_check, timeout, omni_plan)
        return self._record_task(target_json, result, dry_run=False)

    def set_target(self, target: Sequence[float], *, frame_id: str, speed_ratio: float, collision_check: bool) -> dict[str, Any]:
        target_json = pose_json(target, frame_id)
        if not self.live or not self.session.initialized:
            return self._record_task(target_json, {}, dry_run=True)
        result = self.session.platform.navigation.set_navigation_target(target, frame_id, speed_ratio, collision_check)
        return self._record_task(target_json, result, dry_run=False)

    def navigate_route(self, poses: Sequence[Sequence[float]], *, frame_id: str, speed_ratio: float, collision_check: bool, trajectory: bool, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("多点导航需要明确确认")
        target_json = pose_json(poses[-1], frame_id)
        if not self.live or not self.session.initialized:
            result = self._record_task(target_json, {}, dry_run=True, mode="trajectory" if trajectory else "waypoints")
        elif trajectory:
            result = self._record_task(target_json, self.session.platform.navigation.navigate_along_trajectory(poses, frame_id, speed_ratio, collision_check), dry_run=False, mode="trajectory")
        else:
            result = self._record_task(target_json, self.session.platform.navigation.navigate_through_waypoints(poses, frame_id, collision_check), dry_run=False, mode="waypoints")
        return {**result, "mode": "trajectory" if trajectory else "waypoints", "pose_count": len(poses)}

    def navigate_velocity(self, *, vx: float, vy: float, vyaw: float, duration: float, collision_check: bool, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("速度导航需要明确确认")
        if not self.live or not self.session.initialized:
            result = self._record_task(self._snapshot.pose, {}, dry_run=True, mode="velocity")
        else:
            native = self.session.platform.navigation.navigate_with_velocity(vx, vy, vyaw, duration, collision_check)
            result = self._record_task(self._snapshot.pose, native, dry_run=False, mode="velocity")
        return {**result, "velocity": {"vx": vx, "vy": vy, "vyaw": vyaw, "duration": duration}}

    def check_reachability(self, goal: Sequence[float], current: Sequence[float] | None = None) -> dict[str, Any]:
        if not self.live or not self.session.initialized:
            return {"reachable": None, "dry_run": True, "detail": "dry-run cannot evaluate planner reachability"}
        if current is None:
            pose = self.snapshot().pose
            if pose is None:
                raise ValueError("当前机器人位姿不可用")
            current = [pose[key] for key in ("x", "y", "z", "qx", "qy", "qz", "qw")]
        reachable = self.session.platform.navigation.check_path_reachability(goal, current)
        return {"reachable": reachable, "dry_run": False}

    def path_snapshot(self, task_id: str | None = None) -> dict[str, Any]:
        selected_task_id = task_id or self._snapshot.task_id
        if not selected_task_id:
            return {"task_id": None, "available": False, "revision": 0, "frame_id": "", "poses": [], "msg": "no navigation task"}
        if not self.live or not self.session.initialized:
            return {"task_id": selected_task_id, "available": False, "revision": 0, "frame_id": "map", "poses": [], "msg": "dry-run has no planner path"}
        return self.session.platform.navigation.navigation_path(selected_task_id)

    def tasks(self) -> list[dict[str, Any]]:
        """Return recent tasks newest first for explicit path selection."""
        return [dict(task) for task in reversed(self._tasks)]

    def _update_task_status(self, task_id: str, status: str) -> None:
        for task in reversed(self._tasks):
            if task["task_id"] == task_id:
                task["status"] = status
                return

    def stop(self) -> dict[str, Any]:
        detail = (
            str(self.session.platform.navigation.stop())
            if self.live and self.session.initialized
            else "dry-run"
        )
        self._snapshot = NavigationSnapshot(
            self._snapshot.connected,
            self._snapshot.localized,
            self._snapshot.pose,
            "STOPPED",
            self._snapshot.task_id,
            detail,
        )
        if self._snapshot.task_id:
            self._update_task_status(self._snapshot.task_id, "STOPPED")
        return {"accepted": True, "dry_run": not self.live, "detail": detail}

    def relocalize(
        self, pose: Sequence[float], *, frame_id: str = "map"
    ) -> dict[str, Any]:
        if self.live and self.session.initialized:
            result = self.session.platform.navigation.relocalize(pose)
            return {"accepted": True, "dry_run": False, "sdk_result": str(result)}
        return {"accepted": True, "dry_run": True, "pose": pose_json(pose, frame_id)}

    def move_straight_to(
        self, *, x: float, y: float, yaw: float, timeout: float, confirmed: bool
    ) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("直线位移需要明确确认")
        target = [x, y, 0.0, 0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0)]
        if not self.live or not self.session.initialized:
            return {
                "accepted": True,
                "dry_run": True,
                "target": {"x": x, "y": y, "yaw": yaw},
            }
        result = self.session.platform.navigation.move_straight_to(target, timeout)
        self._snapshot = NavigationSnapshot(
            True,
            self.snapshot().localized,
            self._snapshot.pose,
            "RUNNING",
            self._snapshot.task_id,
        )
        return {
            "accepted": True,
            "dry_run": False,
            "target": {"x": x, "y": y, "yaw": yaw},
            "sdk_result": str(result),
        }
