from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NavigationSnapshot:
    connected: bool = False
    localized: bool = False
    pose: dict[str, Any] | None = None
    status: str = "IDLE"
    task_id: str | None = None
    detail: str = ""
