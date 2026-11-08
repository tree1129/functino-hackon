from .base import PlatformAdapter
from .g1 import G1Platform
from .g3 import G3Platform
from .s1 import S1Platform


def create_platform(model: str) -> PlatformAdapter:
    platforms = {"g1": G1Platform, "s1": S1Platform, "g3": G3Platform}
    try:
        return platforms[model.lower()]()
    except KeyError as error:
        raise ValueError(f"Unsupported robot model: {model}") from error
