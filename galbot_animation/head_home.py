#!/usr/bin/env python3
"""Smoothly return the G1 head to a safe pose. Head joints only."""
from __future__ import annotations

import time

from galbot_sdk.g1 import ControlStatus, G1ControllerName, GalbotRobot, JointCommand, SensorType


def main() -> int:
    robot = GalbotRobot()
    if not robot.init({SensorType.HEAD_LEFT_CAMERA}):
        print("INIT_FAILED")
        return 2
    try:
        if robot.switch_controller(G1ControllerName.HEAD_PVT_CTRL) != ControlStatus.SUCCESS:
            print("SWITCH_FAILED")
            return 3
        yaw, pitch = [float(v) for v in robot.get_joint_positions(joint_groups=["head"], joint_names=[])]
        print("start", yaw, pitch)
        for _ in range(200):
            yaw, pitch = [float(v) for v in robot.get_joint_positions(joint_groups=["head"], joint_names=[])]
            dy = max(-0.04, min(0.04, 0.0 - yaw))
            dp = max(-0.03, min(0.03, 0.0 - pitch))
            if abs(dy) < 0.001 and abs(dp) < 0.001:
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
            time.sleep(0.08)
        print("final", robot.get_joint_positions(joint_groups=["head"], joint_names=[]))
        return 0
    finally:
        robot.request_shutdown()
        robot.wait_for_shutdown()
        robot.destroy()
        print("released")


if __name__ == "__main__":
    raise SystemExit(main())
