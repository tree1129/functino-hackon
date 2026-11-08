"""Lifecycle and domain ports exposed by one robot-model package."""
from abc import ABC, abstractmethod
from typing import Any

from ..ports import MotionPort, NavigationPort, RobotPort


class PlatformAdapter(ABC):
    model: str
    navigation: NavigationPort
    robot: RobotPort
    motion: MotionPort

    @abstractmethod
    def initialize(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @abstractmethod
    def capabilities(self) -> dict[str, Any]: ...
