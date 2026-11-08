#!/usr/bin/env python3
"""右手摇铃:先预抬右臂,再围绕当前腕关节角度做正弦摆动,最后回落。

须在 G1 真机上运行;默认只读检查请加 --dry-run。详见 --help。
"""

import argparse
import math
import sys
import time


def smoothstep(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def build_trajectory(
    swing_joint,
    base_swing,
    amplitude_rad,
    frequency_hz,
    ring_s,
    dt_s,
    ramp_s,
    lift_joint=None,
    base_lift=0.0,
    lift_delta_rad=0.0,
    lift_s=0.0,
    return_s=0.0,
):
    from galbot_sdk.g1 import JointCommand, TrajectoryPoint

    total_s = lift_s + ring_s + return_s
    n = max(1, int(round(total_s / dt_s)))
    times = [total_s * i / n for i in range(n + 1)]
    joint_names = ([lift_joint] if lift_joint else []) + [swing_joint]

    points = []
    lift_positions = []
    swing_positions = []
    for t in times:
        if lift_joint:
            if t <= lift_s:
                env = smoothstep(t / lift_s) if lift_s > 0 else 1.0
            elif return_s > 0 and t >= lift_s + ring_s:
                env = smoothstep((total_s - t) / return_s)
            else:
                env = 1.0
            lift_q = base_lift + lift_delta_rad * env
            lift_positions.append(lift_q)

        ring_head = smoothstep((t - lift_s) / ramp_s) if ramp_s > 0 else 1.0
        ring_tail = smoothstep((lift_s + ring_s - t) / ramp_s) if ramp_s > 0 else 1.0
        swing_q = base_swing + amplitude_rad * min(ring_head, ring_tail) * math.sin(
            2.0 * math.pi * frequency_hz * (t - lift_s)
        )
        swing_positions.append(swing_q)

        values = ([lift_q] if lift_joint else []) + [swing_q]
        tp = TrajectoryPoint()
        tp.time_from_start_second = t + dt_s
        cmds = []
        for q in values:
            cmd = JointCommand()
            cmd.position = q
            cmds.append(cmd)
        tp.joint_command_vec = cmds
        points.append(tp)

    return times, joint_names, points, lift_positions, swing_positions


def parse_args():
    p = argparse.ArgumentParser(
        description="右手摇铃:先预抬臂让铃铛离开底盘,再摆动腕关节,最后回落。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--joint", default="right_arm_joint7", help="摇铃摆动的右臂关节名")
    p.add_argument("--amplitude-deg", type=float, default=8.0, help="摆动幅度(度,单侧)")
    p.add_argument("--frequency-hz", type=float, default=1.7, help="摆动频率(Hz)")
    p.add_argument("--duration-s", type=float, default=4.0, help="摇铃时长(秒)")
    p.add_argument("--dt", type=float, default=0.02, help="轨迹点间隔(秒)")
    p.add_argument("--ramp-s", type=float, default=0.5, help="摆动首尾渐变时间(秒)")
    p.add_argument(
        "--lift-joint",
        default="right_arm_joint1",
        help="预抬升关节名;填 none 关闭预抬升",
    )
    p.add_argument(
        "--lift-deg",
        type=float,
        default=-8.0,
        help="预抬升关节增量(度,带符号;joint1 负值抬高手部;0 关闭)",
    )
    p.add_argument("--lift-s", type=float, default=1.0, help="抬升渐变时间(秒)")
    p.add_argument(
        "--return-s",
        type=float,
        default=1.0,
        help="摇完后回落时间(秒);0 表示保持抬升状态不回落",
    )
    p.add_argument(
        "--max-peak-speed",
        type=float,
        default=1.5,
        help="允许的估算峰值关节速度(rad/s),超过则拒绝执行",
    )
    p.add_argument("--dry-run", action="store_true", help="只读:读取状态并打印计划,不运动")
    p.add_argument("--yes", action="store_true", help="跳过交互确认(不推荐)")
    args = p.parse_args()

    if args.amplitude_deg <= 0:
        p.error("--amplitude-deg 必须大于 0")
    if args.frequency_hz <= 0:
        p.error("--frequency-hz 必须大于 0")
    if args.duration_s <= 0:
        p.error("--duration-s 必须大于 0")
    if args.dt <= 0 or args.dt > 0.1:
        p.error("--dt 必须在 (0, 0.1] 秒内")
    if args.ramp_s < 0:
        p.error("--ramp-s 不能为负")
    if args.lift_s <= 0:
        p.error("--lift-s 必须大于 0")
    if args.return_s < 0:
        p.error("--return-s 不能为负")
    if args.lift_deg != 0 and args.lift_joint == args.joint:
        p.error("--lift-joint 不能与 --joint 相同")
    return args


def confirm_or_exit(args):
    print()
    print("执行前请逐项确认:")
    print("  [ ] 急停已松开,现场人员已就位")
    print("  [ ] 机器人周围 1 米内无人、无遮挡物")
    print("  [ ] 铃铛已由右手夹爪稳固夹持,不会被甩出")
    print("  [ ] 预抬升后铃铛与底盘/躯干保持净空")
    print("  [ ] 没有任何人触碰机器人")
    if args.yes:
        print("已指定 --yes,跳过交互确认。")
        return
    if not sys.stdin.isatty():
        sys.exit("标准输入不是终端,拒绝执行。确需非交互运行请显式加 --yes。")
    answer = input("确认无误请输入 RING 后回车: ").strip()
    if answer != "RING":
        sys.exit("未确认,已取消。")


def main():
    args = parse_args()

    from galbot_sdk.g1 import ControlStatus, GalbotRobot, Trajectory

    lift_enabled = args.lift_deg != 0 and args.lift_joint != "none"
    robot = GalbotRobot()
    initialized = False
    try:
        if not robot.init():
            sys.exit("SDK 初始化失败。")
        initialized = True
        time.sleep(1.0)

        names = robot.get_joint_names(only_active_joint=True, joint_groups=["right_arm"])
        print("右臂关节:", ", ".join(names))
        for joint in [args.joint, args.lift_joint if lift_enabled else None]:
            if joint and joint not in names:
                sys.exit(f"找不到关节 {joint},请从上面的列表中选择。")

        base_swing = robot.get_joint_positions(joint_names=[args.joint])[0]
        base_lift = (
            robot.get_joint_positions(joint_names=[args.lift_joint])[0]
            if lift_enabled
            else 0.0
        )
        amplitude_rad = math.radians(args.amplitude_deg)
        lift_delta_rad = math.radians(args.lift_deg) if lift_enabled else 0.0
        ramp_s = min(args.ramp_s, args.duration_s / 2.0)
        lift_s = args.lift_s if lift_enabled else 0.0

        lift_peak = (
            1.5 * abs(lift_delta_rad) / lift_s if lift_enabled else 0.0
        )
        swing_peak = amplitude_rad * 2.0 * math.pi * args.frequency_hz
        peak_speed = max(lift_peak, swing_peak)
        total_s = lift_s + args.duration_s + args.return_s

        times, joint_names, points, lift_positions, swing_positions = (
            build_trajectory(
                args.joint,
                base_swing,
                amplitude_rad,
                args.frequency_hz,
                args.duration_s,
                args.dt,
                ramp_s,
                lift_joint=args.lift_joint if lift_enabled else None,
                base_lift=base_lift,
                lift_delta_rad=lift_delta_rad,
                lift_s=lift_s,
                return_s=args.return_s,
            )
        )

        print()
        print(f"摇铃关节: {args.joint}")
        print(f"当前角度: {math.degrees(base_swing):+.2f}° ({base_swing:+.4f} rad)")
        print(f"摆动幅度: ±{args.amplitude_deg:.1f}°  频率: {args.frequency_hz:.2f} Hz")
        print(
            f"指令范围: {math.degrees(min(swing_positions)):+.2f}° ~ "
            f"{math.degrees(max(swing_positions)):+.2f}°"
        )
        if lift_enabled:
            print(
                f"预抬升: {args.lift_joint} {args.lift_deg:+.1f}° "
                f"(当前 {math.degrees(base_lift):+.2f}°, 用时 {lift_s:.1f} s)"
            )
            print(
                f"抬升范围: {math.degrees(min(lift_positions)):+.2f}° ~ "
                f"{math.degrees(max(lift_positions)):+.2f}°"
            )
        else:
            print("预抬升: 关闭")
        print(
            f"总时长: {total_s:.1f} s "
            f"(抬升 {lift_s:.1f} + 摇铃 {args.duration_s:.1f} + 回落 {args.return_s:.1f}), "
            f"轨迹点: {len(points)}"
        )
        print(f"估算峰值速度: {peak_speed:.2f} rad/s (上限 {args.max_peak_speed:.2f})")

        if peak_speed > args.max_peak_speed:
            sys.exit(
                "估算峰值速度超过 --max-peak-speed,拒绝执行。"
                "请降低幅度/频率或放慢抬升,或显式调高上限。"
            )

        if args.dry_run:
            print("\ndry-run 完成,未发送任何运动指令。")
            return

        confirm_or_exit(args)

        traj = Trajectory()
        traj.joint_groups = []
        traj.joint_names = joint_names
        traj.points = points

        print("\n开始执行:预抬升 -> 摇铃 -> 回落...")
        status = robot.execute_joint_trajectory(traj, True)
        if status != ControlStatus.SUCCESS:
            sys.exit(f"轨迹执行失败: {status}")
        print("摇铃完成。")
    except KeyboardInterrupt:
        print("\n收到中断,释放资源后退出。")
        raise SystemExit(130)
    finally:
        if initialized:
            robot.request_shutdown()
            robot.wait_for_shutdown()
            robot.destroy()
            print("SDK 资源已释放。")


if __name__ == "__main__":
    main()
