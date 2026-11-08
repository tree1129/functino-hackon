"""Composition root for one shared SDK session and application services."""
from typing import Any

from ..sdk.registry import capability_document
from ..sdk.session import SdkSession
from .events import EventBroker
from .motion import MotionService
from .navigation import NavigationService
from .robot import RobotService
from .recording import RecordingService
from .configuration import ConfigurationService


class ConsoleRuntime:
    def __init__(self, model: str = "g1", live: bool = False) -> None:
        self.live = live
        self.session = SdkSession(model=model)
        self.navigation = NavigationService(self.session, live=live)
        self.robot = RobotService(self.session, live=live)
        self.motion = MotionService(self.session, live=live)
        self.configuration = ConfigurationService(self.session, live=live)
        self.recording = RecordingService(self.navigation, self.robot, self.motion, model=model)
        self.events = EventBroker(self.navigation, self.robot)

    def initialize(self) -> None:
        if self.live:
            self.session.initialize()

    async def close(self) -> None:
        self.recording.close()
        await self.events.close()
        self.session.close()

    def capabilities(self) -> dict[str, Any]:
        return capability_document(self.session.platform, self.live)
