"""Shared lifecycle for the g1/s1/g3 Galbot Python packages."""
from importlib import import_module
from typing import Any

from .base import PlatformAdapter
from .motion import PythonMotionAdapter
from .navigation import PythonNavigationAdapter
from .robot import PythonRobotAdapter


class PythonSdkPlatform(PlatformAdapter):
    def __init__(self, model: str) -> None:
        self.model = model
        self._sdk: Any | None = None
        self._robot: Any | None = None
        self._navigation: Any | None = None
        self._motion: Any | None = None
        self.navigation = PythonNavigationAdapter(self)
        self.robot = PythonRobotAdapter(self)
        self.motion = PythonMotionAdapter(self)

    @property
    def connected(self) -> bool:
        return self._robot is not None and self._navigation is not None

    @property
    def sdk_module(self) -> Any | None:
        return self._sdk

    @property
    def robot_instance(self) -> Any | None:
        return self._robot

    @property
    def navigation_instance(self) -> Any | None:
        return self._navigation

    def initialize(self) -> None:
        if self.connected:
            return
        self._sdk = import_module(f"galbot_sdk.{self.model}")
        sensor_type = getattr(self._sdk, "SensorType", None)
        # Do not pass every enum member: SensorType also exposes model-specific
        # and mutually exclusive entries, which makes G1 init reject the whole
        # set.  Keep this list limited to sensors supported by this console.
        # Ultrasonic hardware is unavailable, and infrared cameras must remain
        # off unless explicitly introduced as an opt-in feature.
        documented_names = (
            "BASE_LIDAR", "CHASSIS_IMU", "TORSO_IMU",
            "LEFT_ARM_CAMERA", "LEFT_ARM_DEPTH_CAMERA",
            "RIGHT_ARM_CAMERA", "RIGHT_ARM_DEPTH_CAMERA",
            "HEAD_LEFT_CAMERA", "HEAD_RIGHT_CAMERA",
            "LEFT_FRONT_SURROUND_CAMERA", "RIGHT_FRONT_SURROUND_CAMERA",
            "LEFT_REAR_SURROUND_CAMERA", "RIGHT_REAR_SURROUND_CAMERA",
        )
        available = {name: getattr(sensor_type, name) for name in documented_names if sensor_type is not None and getattr(sensor_type, name, None) is not None}
        robot = self._sdk.GalbotRobot()
        if not robot.init(set(available.values())):
            robot.request_shutdown()
            robot.wait_for_shutdown()
            robot.destroy()
            raise RuntimeError(f"{self.model}: GalbotRobot.init() failed for sensor profile: {sorted(available)}")
        self.enabled_sensor_names = sorted(available)
        navigation = self._sdk.GalbotNavigation()
        if not navigation.init():
            robot.request_shutdown()
            robot.wait_for_shutdown()
            robot.destroy()
            raise RuntimeError(f"{self.model}: GalbotNavigation.init() failed")
        self._robot = robot
        self._navigation = navigation

    def close(self) -> None:
        if self._robot is None:
            return
        self._robot.request_shutdown()
        self._robot.wait_for_shutdown()
        self._robot.destroy()
        self._robot = None
        self._navigation = None
        self._motion = None

    def capabilities(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "services": {
                "navigation": self.navigation.capabilities(),
                "robot": self.robot.capabilities(),
                "motion": ["plan_preview", "execute_plan", *self.motion.capabilities()],
            },
        }

    def require_robot(self) -> tuple[Any, Any]:
        if self._robot is None or self._sdk is None:
            raise RuntimeError(f"{self.model} SDK session is not initialized")
        return self._robot, self._sdk

    def require_navigation(self) -> tuple[Any, Any, Any]:
        robot, sdk = self.require_robot()
        if self._navigation is None:
            raise RuntimeError(f"{self.model} navigation is not initialized")
        return robot, self._navigation, sdk

    def require_motion(self) -> tuple[Any, Any, Any]:
        robot, sdk = self.require_robot()
        if self._motion is None:
            motion = sdk.GalbotMotion()
            if not motion.init():
                raise RuntimeError(f"{self.model}: GalbotMotion.init() failed")
            self._motion = motion
        return robot, self._motion, sdk
