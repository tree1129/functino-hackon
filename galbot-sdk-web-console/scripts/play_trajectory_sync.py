#!/usr/bin/env python3
"""Play a full-body trajectory on Galbot G1 via the official SDK.

The trajectory JSON may contain leg / head / left_arm / right_arm. Grippers are
never commanded here; both grippers keep their current opening.

Run on the robot:
    export LD_LIBRARY_PATH=/data/galbot/lib:$LD_LIBRARY_PATH
    export PYTHONPATH=/data/galbot/lib:$PYTHONPATH
    python3 play_trajectory.py --input out/trajectory_jingju_v1_both.json --execute

The console service (galbot-console.service) can hold controllers. Stop it
first if controller switching fails.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def build_trajectory(data: dict):
    from galbot_sdk.g1 import JointCommand, Trajectory, TrajectoryPoint

    traj = Trajectory()
    traj.joint_groups = list(data["groups"])
    traj.joint_names = []
    points = []
    for frame in data["points"]:
        point = TrajectoryPoint()
        point.time_from_start_second = float(frame["t"])
        commands = []
        for group in data["groups"]:
            for value in frame[group]:
                command = JointCommand()
                command.position = float(value)
                command.velocity = 0.0
                command.acceleration = 0.0
                command.effort = 0.0
                commands.append(command)
        point.joint_command_vec = commands
        points.append(point)
    traj.points = points
    return traj


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="out/trajectory_fullbody.json")
    parser.add_argument("--execute", action="store_true", help="actually execute; default dry-run")
    parser.add_argument("--block", action="store_true", default=True)
    parser.add_argument("--sync-file", default="", help="touch this file immediately before trajectory execution")
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    print(
        f"trajectory={data.get('name')} frames={len(data['points'])} "
        f"duration={data.get('duration')} groups={data['groups']} locked={data.get('locked_groups')}"
    )
    if not args.execute:
        print("DRY-RUN: add --execute to move the robot. Grippers will NOT be commanded.")
        return 0

    from galbot_sdk.g1 import ControlStatus, G1ControllerName, GalbotRobot

    robot = GalbotRobot()
    if not robot.init():
        print("GalbotRobot.init() failed")
        return 2
    try:
        # Only the groups present in the trajectory are switched. Grippers are
        # deliberately left alone so their opening does not change.
        controller_names = {
            "leg": "LEG_PVT_CTRL",
            "head": "HEAD_PVT_CTRL",
            "left_arm": "LEFT_ARM_PVT_CTRL",
            "right_arm": "RIGHT_ARM_PVT_CTRL",
        }
        for group in data["groups"]:
            name = controller_names.get(group)
            if name is None:
                raise RuntimeError(f"unsupported group in trajectory: {group}")
            controller = getattr(G1ControllerName, name)
            status = robot.switch_controller(controller)
            if status != ControlStatus.SUCCESS:
                raise RuntimeError(f"switch_controller({name}) failed: {status}")
            print(f"controller {group} -> {name}: {status}")

        traj = build_trajectory(data)
        if args.sync_file:
            Path(args.sync_file).touch()
            print(f"SYNC_SIGNAL {time.time():.6f}", flush=True)
        start = time.monotonic()
        status = robot.execute_joint_trajectory(traj, args.block)
        elapsed = time.monotonic() - start
        print(f"execute_joint_trajectory status={status} elapsed={elapsed:.2f}s")
        return 0 if status == ControlStatus.SUCCESS else 3
    finally:
        robot.request_shutdown()
        robot.wait_for_shutdown()
        robot.destroy()
        print("SDK resources released")


if __name__ == "__main__":
    raise SystemExit(main())
