#!/usr/bin/env python3
"""Smoothly move the G1 head to a requested yaw/pitch. Head joints only."""
from __future__ import annotations

import argparse
import time

from galbot_sdk.g1 import ControlStatus, G1ControllerName, GalbotRobot, JointCommand, SensorType


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yaw", type=float, default=0.0)
    parser.add_argument("--pitch", type=float, default=0.0)
    parser.add_argument("--max-step", type=float, default=0.035)
    parser.add_argument("--interval", type=float, default=0.08)
    args = parser.parse_args()

    robot = GalbotRobot()
    if not robot.init({SensorType.HEAD_LEFT_CAMERA}):
        print("INIT_FAILED")
        return 2
    try:
        if robot.switch_controller(G1ControllerName.HEAD_PVT_CTRL) != ControlStatus.SUCCESS:
            print("SWITCH_FAILED")
            return 3
        yaw, pitch = [float(v) for v in robot.get_joint_positions(joint_groups=["head"], joint_names=[])]
        print("start", round(yaw, 4), round(pitch, 4), "target", args.yaw, args.pitch)
        for _ in range(240):
            yaw, pitch = [float(v) for v in robot.get_joint_positions(joint_groups=["head"], joint_names=[])]
            dy = max(-args.max_step, min(args.max_step, args.yaw - yaw))
            dp = max(-args.max_step, min(args.max_step, args.pitch - pitch))
            if abs(dy) < 0.0008 and abs(dp) < 0.0008:
                break
            commands = []
            for value in (yaw + dy, pitch + dp):
                command = JointCommand()
                command.position = float(value)
                commands.append(command)
            result = robot.set_joint_commands(commands, ["head"], [], 0.0)
            if result != ControlStatus.SUCCESS:
                print("command_failed", result)
                return 4
            time.sleep(args.interval)
        print("final", robot.get_joint_positions(joint_groups=["head"], joint_names=[]))
        return 0
    finally:
        robot.request_shutdown()
        robot.wait_for_shutdown()
        robot.destroy()
        print("released")


if __name__ == "__main__":
    raise SystemExit(main())
