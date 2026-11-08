"""Generate the reusable Rerun geometry asset for a robot model."""

from __future__ import annotations

import argparse
from pathlib import Path

from galbot_sdk_web_console.geometry.rerun_robot import write_robot_model_recording


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="g1")
    parser.add_argument("--representation", choices=("collision", "visual"), default="collision")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(write_robot_model_recording(
        args.model,
        args.output,
        representation=args.representation,
        force=args.force,
    ))


if __name__ == "__main__":
    main()
