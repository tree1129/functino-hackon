"""One SDK session per console process, independent of robot model."""
from .platforms import PlatformAdapter, create_platform


class SdkSession:
    def __init__(self, model: str = "g1", platform: PlatformAdapter | None = None) -> None:
        self.model = model.lower()
        self.platform = platform or create_platform(self.model)

    @property
    def initialized(self) -> bool:
        return self.platform.connected

    def initialize(self) -> None:
        self.platform.initialize()

    def close(self) -> None:
        self.platform.close()
