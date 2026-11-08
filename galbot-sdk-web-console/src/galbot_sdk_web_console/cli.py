import argparse
import logging
import os
import socket

import uvicorn


class AccessHostLogFilter(logging.Filter):
    """Keep the wildcard bind while showing the browser-reachable LAN URL."""

    def __init__(self, access_host: str) -> None:
        super().__init__()
        self.access_host = access_host

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.error" and "Uvicorn running on" in record.getMessage():
            record.args = tuple(
                value.replace("0.0.0.0", self.access_host).replace("[::]", self.access_host)
                if isinstance(value, str) else value
                for value in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        return True


class AccessUrlServer(uvicorn.Server):
    """Emit a standalone URL after bind so VS Code can detect the browser link."""

    def __init__(self, config: uvicorn.Config, access_url: str) -> None:
        super().__init__(config)
        self.access_url = access_url

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets=sockets)
        if self.started:
            print(f"Galbot SDK Web Console is ready: {self.access_url}", flush=True)


def get_access_host() -> str:
    """Return the LAN address a browser on the robot's network can use."""
    try:
        # This does not send traffic; it asks the OS which source address it
        # would use for an outbound route.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            host = sock.getsockname()[0]
            if not host.startswith("127."):
                return host
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            host = info[4][0]
            if not host.startswith("127."):
                return host
    except OSError:
        pass
    return "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Galbot SDK Web Console gateway")
    parser.add_argument("--host", default="0.0.0.0", help="listen address (default: all interfaces)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--robot-model", choices=("g1", "s1", "g3"), default="g1")
    parser.add_argument("--live", action="store_true", help="initialize the selected robot SDK")
    args = parser.parse_args()
    os.environ["GALBOT_MODEL"] = args.robot_model
    os.environ["GALBOT_LIVE"] = "1" if args.live else "0"
    access_host = get_access_host() if args.host in {"0.0.0.0", "::"} else args.host
    try:
        if args.reload:
            uvicorn.run(
                "galbot_sdk_web_console.gateway.app:app",
                host=args.host,
                port=args.port,
                reload=True,
            )
            return
        config = uvicorn.Config("galbot_sdk_web_console.gateway.app:app", host=args.host, port=args.port)
        logging.getLogger("uvicorn.error").addFilter(AccessHostLogFilter(access_host))
        AccessUrlServer(config, f"http://{access_host}:{args.port}").run()
    except KeyboardInterrupt:
        # Python 3.14's asyncio.Runner re-raises Ctrl+C after Uvicorn has
        # already completed its shutdown. Treat this expected operator action
        # as a clean exit instead of printing a traceback.
        print("Galbot SDK Web Console stopped.", flush=True)
