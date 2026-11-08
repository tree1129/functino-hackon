"""Browser-visible service registry, derived from platform capabilities."""
from .platforms.base import PlatformAdapter


def capability_document(platform: PlatformAdapter, live: bool) -> dict:
    document = platform.capabilities()
    document["live_mode"] = live
    document["command_protocol"] = {"endpoint": "/api/commands/{service}/{method}", "transport": "HTTP JSON"}
    document["event_protocol"] = {"endpoint": "/api/events", "transport": "WebSocket", "subscription": "latest-state topics"}
    return document
