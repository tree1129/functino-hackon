"""Shared latest-state producer with bounded per-client queues."""
import asyncio
import contextlib
import time
from dataclasses import asdict
from typing import Any, AsyncIterator

from .navigation import NavigationService
from .robot import RobotService


class EventBroker:
    def __init__(self, navigation: NavigationService, robot: RobotService) -> None:
        self.navigation = navigation
        self.robot = robot
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        self._sequence = 0

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        self.start()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        self._subscribers.add(queue)
        try:
            # A client can connect after the planner has already published a path.
            # Replay the current snapshots so it does not have to wait for the next
            # path revision before it can render the active task.
            try:
                navigation = await asyncio.to_thread(self.navigation.snapshot)
                self._publish_to(queue, "navigation_state", asdict(navigation))
                path = await asyncio.to_thread(self.navigation.path_snapshot, navigation.task_id)
                self._publish_to(queue, "navigation_path", path)
            except Exception as error:
                self._publish_to(queue, "service_error", {"message": str(error)})
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        next_lidar_at = 0.0
        last_path_version: tuple[str | None, int] | None = None
        while True:
            try:
                now = time.monotonic()
                include_lidar = self.robot.lidar_stream_config["enabled"] and now >= next_lidar_at
                navigation = await asyncio.to_thread(self.navigation.snapshot)
                pose = navigation.pose or {}
                robot = await asyncio.to_thread(self.robot.snapshot, include_lidar, pose, True)
                self._publish("navigation_state", asdict(navigation))
                path = await asyncio.to_thread(self.navigation.path_snapshot, navigation.task_id)
                path_version = (path.get("task_id"), int(path.get("revision", 0)))
                if path_version != last_path_version:
                    self._publish("navigation_path", path)
                    last_path_version = path_version
                if robot["joint_state"] is not None:
                    self._publish("joint_state", robot["joint_state"])
                if robot["lidar"] is not None:
                    self._publish("local_cloud", robot["lidar"])
                    next_lidar_at = now + 1.0
                if robot.get("sensors") is not None:
                    self._publish("robot_sensors", robot["sensors"])
                await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._publish("service_error", {"message": str(error)})
                await asyncio.sleep(1.0)

    def _publish(self, event_type: str, data: Any) -> None:
        for queue in tuple(self._subscribers):
            self._publish_to(queue, event_type, data)

    def _publish_to(self, queue: asyncio.Queue[dict[str, Any]], event_type: str, data: Any) -> None:
        self._sequence += 1
        event = {"type": event_type, "timestamp_ns": time.time_ns(), "sequence": self._sequence, "data": data}
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(event)
