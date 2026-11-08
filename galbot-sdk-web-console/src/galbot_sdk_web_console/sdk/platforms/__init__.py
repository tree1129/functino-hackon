"""Robot-model-specific SDK adapters."""

from .base import PlatformAdapter
from .factory import create_platform

__all__ = ["PlatformAdapter", "create_platform"]
