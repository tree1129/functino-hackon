#!/usr/bin/env python3
"""Slow joint-space rehome from the current pose to the neutral show start."""
from __future__ import annotations

TARGETS = {
    "head": [0.0, 0.0],
    "left_arm": [0.9462, -1.3504, -0.8103, -1.4806, -0.1472, -0.2256, 0.4748],
    "right_arm": [-0.7927, 1.3677, 0.1520, 1.5804, 0.6111, -0.0672, -0.0876],
    "leg": [0.4995, 1.4993, 1.0001, 0.0004, 0.0001],
}


def main() -> int:
    from galbot_sdk.g1 import GalbotRobot

    robot = GalbotRobot()
    if not robot.init():
        print("GalbotRobot.init() failed")
        return 2
    try:
        for group, target in TARGETS.items():
            names = [f"{group}_joint{i}" for i in range(1, len(target) + 1)]
            result = robot.set_joint_positions(target, [], names, True, 0.1, 40.0)
            print(group, result, flush=True)
        return 0
    finally:
        robot.request_shutdown()
        robot.wait_for_shutdown()
        robot.destroy()
        print("SDK resources released")


if __name__ == "__main__":
    raise SystemExit(main())
