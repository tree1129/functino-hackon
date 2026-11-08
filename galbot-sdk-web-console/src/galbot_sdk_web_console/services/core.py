"""Core console supervisor: robot status, services, data files, action orchestration."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .robot import JOINT_GROUPS

SYSTEM_CFG = Path("/data/galbot/config/system.cfg")
MODE_CFG = Path("/userdata/user_config/mode_service_config_default.toml")
SWALLOWS_DIR = Path("/userdata/swallows")
LAUNCHER_LOG_DIR = Path("/userdata/log/hpu_launcher")
TTS_BIN = Path("/home/galbot/tts/say")
VPS_HOST = "107.175.67.78"
TRAJECTORY_DIR = Path("/home/galbot/galbot-sdk-web-console/trajectories")
AUDIO_DIR = Path("/home/galbot/galbot-sdk-web-console/assets/audio")
AUDIO_PLAYER = Path("/home/galbot/galbot-sdk-web-console/scripts/play_audio_sync.py")
BGM_SYNC_FILE = Path("/tmp/jingju_bgm_start")

JOINT_ORDER = {
    "leg": [f"leg_joint{i}" for i in range(1, 6)],
    "head": [f"head_joint{i}" for i in range(1, 3)],
    "left_arm": [f"left_arm_joint{i}" for i in range(1, 8)],
    "right_arm": [f"right_arm_joint{i}" for i in range(1, 8)],
}

HEART_LEFT = [1.53, 0.36, -2.54, -1.80, 0.12, -0.82, 0.09]
HEART_RIGHT = [-1.53, -0.36, 2.54, 1.80, -0.12, 0.82, -0.09]
HOME_LEFT = [1.90, -1.46, -0.54, -1.96, 0.0, -0.4, 0.0]
HOME_RIGHT = [-1.90, 1.46, 0.54, 1.96, 0.0, 0.4, 0.0]


def _joint(group: str, positions: list[float], speed: float = 0.1, timeout: float = 30.0, wait: float = 0.2) -> dict[str, Any]:
    return {"type": "joint", "group": group, "positions": positions, "speed": speed, "timeout": timeout, "wait": wait}


def _gripper(side: str, width: float, wait: float = 0.6) -> dict[str, Any]:
    return {"type": "gripper", "side": side, "width": width, "speed": 0.05, "effort": 10.0, "wait": wait}


def _wait(seconds: float) -> dict[str, Any]:
    return {"type": "wait", "seconds": seconds}


def _stop() -> dict[str, Any]:
    return {"type": "stop"}


ACTIONS: dict[str, dict[str, Any]] = {
    "head_nod": {
        "label": "点头",
        "description": "头部小幅点头两次",
        "warning": "头部附近不要站人",
        "steps": [
            _joint("head", [0.0, 0.15], 0.2, 10, 0.4),
            _joint("head", [0.0, 0.0], 0.2, 10, 0.4),
            _joint("head", [0.0, 0.15], 0.2, 10, 0.4),
            _joint("head", [0.0, 0.0], 0.2, 10, 0.2),
        ],
    },
    "look_left": {
        "label": "向左看",
        "description": "头部向左转约 17°",
        "warning": "确认颈部活动空间",
        "steps": [_joint("head", [0.3, 0.0], 0.2, 10)],
    },
    "look_right": {
        "label": "向右看",
        "description": "头部向右转约 17°",
        "warning": "确认颈部活动空间",
        "steps": [_joint("head", [-0.3, 0.0], 0.2, 10)],
    },
    "look_center": {
        "label": "回正头部",
        "description": "头部回到中位",
        "warning": "确认颈部活动空间",
        "steps": [_joint("head", [0.0, 0.0], 0.2, 10)],
    },
    "gripper_left_open": {
        "label": "左夹爪张开",
        "description": "左夹爪张开到 10cm",
        "warning": "确认夹爪周围无遮挡",
        "steps": [_gripper("left", 0.10)],
    },
    "gripper_left_close": {
        "label": "左夹爪闭合",
        "description": "左夹爪闭合到 2cm",
        "warning": "确认夹爪内无手指/异物",
        "steps": [_gripper("left", 0.02)],
    },
    "gripper_right_open": {
        "label": "右夹爪张开",
        "description": "右夹爪张开到 10cm",
        "warning": "确认夹爪周围无遮挡",
        "steps": [_gripper("right", 0.10)],
    },
    "gripper_right_close": {
        "label": "右夹爪闭合",
        "description": "右夹爪闭合到 2cm",
        "warning": "确认夹爪内无手指/异物",
        "steps": [_gripper("right", 0.02)],
    },
    "arm_home": {
        "label": "双臂回初始位",
        "description": "双臂回到示例初始姿态（低速）",
        "warning": "双臂 1 米范围内无人、无遮挡",
        "steps": [
            _joint("left_arm", HOME_LEFT, 0.2, 40, 0.3),
            _joint("right_arm", HOME_RIGHT, 0.2, 40, 0.3),
        ],
    },
    "heart_pose": {
        "label": "比心动作",
        "description": "双臂比心，保持 4 秒后回到初始位",
        "warning": "双臂运动范围 1 米内无人；急停可及",
        "steps": [
            _joint("left_arm", HEART_LEFT, 0.1, 50, 0.3),
            _joint("right_arm", HEART_RIGHT, 0.1, 50, 0.3),
            _wait(4.0),
            _joint("left_arm", HOME_LEFT, 0.2, 40, 0.2),
            _joint("right_arm", HOME_RIGHT, 0.2, 40, 0.2),
        ],
    },
    "jingju_wuxi": {
        "label": "京剧身法",
        "description": "起势→云手→山膀→亮相→收势（16.8s，双臂）",
        "warning": "确认机器人周围 1.5m 无人、急停可及",
        "steps": [{"type": "trajectory", "path": "jingju_wuxi_v1_both.json", "wait": 0.2}],
    },
    "jingju_yunshou": {
        "label": "云手三叠",
        "description": "起势→云手左→云手右→云手左→收势（12s）",
        "warning": "确认双臂 1m 内无人、急停可及",
        "steps": [{"type": "trajectory", "path": "jingju_combo_yunshou.json", "wait": 0.2}],
    },
    "jingju_shanbang_liangxiang": {
        "label": "山膀亮相",
        "description": "起势→山膀→亮相→保持→收势（12s）",
        "warning": "确认双臂 1.5m 内无人、急停可及",
        "steps": [{"type": "trajectory", "path": "jingju_combo_shanbang_liangxiang.json", "wait": 0.2}],
    },
    "jingju_baoquan": {
        "label": "抱拳礼",
        "description": "起势→抱拳→保持→收势（9s）",
        "warning": "确认双手 0.5m 内无遮挡、急停可及",
        "steps": [{"type": "trajectory", "path": "jingju_combo_baoquan.json", "wait": 0.2}],
    },
    "jingju_wangyue": {
        "label": "望月",
        "description": "起势→左臂高、右臂低、抬头→保持→收势（9s）",
        "warning": "确认双臂 1m 内无人、急停可及",
        "steps": [{"type": "trajectory", "path": "jingju_combo_wangyue.json", "wait": 0.2}],
    },
    "jingju_lianzhao": {
        "label": "云手连招",
        "description": "起势→云手左→云手右→山膀→亮相→收势（16s）",
        "warning": "确认双臂 1.5m 内无人、急停可及",
        "steps": [{"type": "trajectory", "path": "jingju_combo_lianzhao.json", "wait": 0.2}],
    },
    "jingju_zonghe": {
        "label": "综合身段",
        "description": "抱拳→山膀→亮相→抱拳→收势（18s）",
        "warning": "确认双臂 1.5m 内无人、急停可及",
        "steps": [{"type": "trajectory", "path": "jingju_combo_zonghe.json", "wait": 0.2}],
    },
    "jingju_60s": {
        "label": "京剧一分钟",
        "description": "云手三叠→山膀亮相→抱拳→望月→连招→收势（60s）",
        "warning": "确认周围 1.5m 无人、无遮挡、急停可及",
        "steps": [{"type": "trajectory", "path": "jingju_60s.json", "wait": 0.2}],
    },
    "jingju_danzhi": {
        "label": "单指",
        "description": "起势→右臂前指、左手护腰→保持→收势（9s）",
        "warning": "确认双臂 1m 内无人、急停可及",
        "steps": [{"type": "trajectory", "path": "jingju_combo_danzhi.json", "wait": 0.2}],
    },
    "jingju_shuangtui": {
        "label": "双推掌",
        "description": "起势→双臂前推→抱拳→收势（9s）",
        "warning": "确认双臂 1m 内无人、急停可及",
        "steps": [{"type": "trajectory", "path": "jingju_combo_shuangtui.json", "wait": 0.2}],
    },
    "jingju_refined": {
        "label": "全式身段",
        "description": "云手→单指→双托掌→山膀→亮相→双推掌→抱拳（27s）",
        "warning": "确认双臂 1.5m 内无人、急停可及",
        "steps": [{"type": "trajectory", "path": "jingju_combo_refined.json", "wait": 0.2}],
    },
    "jingju_60s_v2": {
        "label": "京剧一分钟 v2",
        "description": "云手→单指→望月→山膀亮相→双推掌→抱拳收势（60s）",
        "warning": "确认周围 1.5m 无人、无遮挡、急停可及",
        "steps": [{"type": "trajectory", "path": "jingju_60s_v2.json", "wait": 0.2}],
    },
    "jingju_story_90s": {
        "label": "剧情·将军出征",
        "description": "起势→自报家门→云手巡视→单指→双推掌→望月→山膀亮相→抱拳转身→出征→收势（90s）",
        "warning": "确认周围 1.5m 无人、无遮挡、急停可及；64-76s 为转身占位",
        "steps": [{"type": "trajectory", "path": "jingju_story_90s.json", "wait": 0.2}],
    },
    "jingju_muguiying_57s": {
        "label": "穆桂英挂帅·57s",
        "description": "自动回起势（3s）+ 完整《穆桂英挂帅》卡点动作 + BGM 同步启动（57.2s）",
        "warning": "确认周围 1.5m 无人、无遮挡、急停可及；点击后先回起势，再同步播放 BGM",
        "steps": [
            {"type": "trajectory", "path": "rehome_from_baoquan_3s.json", "wait": 0.2},
            {"type": "show", "trajectory": "jingju_muguiying_57s.json", "audio": "muguiying_16k.pcm", "volume": 55.0, "wait": 0.2},
        ],
    },
    "jingju_muguiying_complete": {
        "label": "穆桂英·完整版",
        "description": "稳定上半身57.2s + 2圈平滑绕圆平移退场 + 后退0.5m（BGM音量100）",
        "warning": "确认周围 1.5m 无人、无遮挡、急停可及；建议先重置控制器；演出中不要切模式",
        "steps": [
            {"type": "trajectory", "path": "rehome_from_baoquan_3s.json", "wait": 0.2},
            {"type": "show", "trajectory": "jingju_muguiying_57s.json", "audio": "muguiying_16k.pcm", "volume": 100.0, "wait": 0.2},
            {"type": "route", "audio": "muguiying_16k.pcm", "volume": 100.0, "wait": 0.2},
        ],
    },
    "jingju_muguiying_exit": {
        "label": "穆桂英·退场（2圈）",
        "description": "2圈平滑绕圆平移退场 + 后退0.5m，BGM音量100",
        "warning": "确认周围 1.5m 无人、无遮挡、急停可及；独立执行",
        "steps": [{"type": "route", "audio": "muguiying_16k.pcm", "volume": 100.0, "wait": 0.2}],
    },
    "jingju_rehome_from_baoquan_3s": {
        "label": "回起势（抱拳后 3s）",
        "description": "从紧凑抱拳姿态用 3s 回到起势，便于衔接完整演出",
        "warning": "确认双臂 1m 内无人、急停可及",
        "steps": [{"type": "trajectory", "path": "rehome_from_baoquan_3s.json", "wait": 0.2}],
    },
    "jingju_rehome": {
        "label": "回起势（低速）",
        "description": "头部/双臂/腿回到京剧起势姿态（0.1 rad/s）",
        "warning": "确认双臂 1m 内无人、急停可及",
        "steps": [
            _joint("head", [0.0, 0.0], 0.1, 10, 0.2),
            _joint("left_arm", [0.9462, -1.3504, -0.8103, -1.4806, -0.1472, -0.2256, 0.4748], 0.1, 40, 0.2),
            _joint("right_arm", [-0.7927, 1.3677, 0.1520, 1.5804, 0.6111, -0.0672, -0.0876], 0.1, 40, 0.2),
            _joint("leg", [0.4995, 1.4993, 1.0001, 0.0004, 0.0001], 0.1, 20, 0.2),
        ],
    },
    "left_wrist_shake": {
        "label": "左腕快速震动（摇铃）",
        "description": "左腕关节小幅高频摆动，模拟摇铃",
        "warning": "确认左手/腕周围无遮挡，急停可及",
        "steps": [{"type": "shake", "joint": "left_arm_joint7", "amplitude": 0.08, "frequency": 3.0, "duration": 2.0}],
    },
    "right_wrist_shake": {
        "label": "右腕快速震动（摇铃）",
        "description": "右腕 joint6 小幅快速左右摆动，模拟摇铃",
        "warning": "确认右手/腕周围无遮挡，急停可及",
        "steps": [{"type": "shake", "joint": "right_arm_joint6", "amplitude": 0.10, "frequency": 4.0, "duration": 2.5}],
    },
    "stop_all": {
        "label": "停止全部运动",
        "description": "停止所有控制器并清零底盘速度",
        "warning": "立即停止",
        "steps": [_stop()],
    },
}


class CoreService:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    # ---------- status ----------
    def status(self) -> dict[str, Any]:
        errors: list[str] = []
        system = self._system_info(errors)
        mode = self._mode_info(errors)
        robot = self._robot_snapshot(errors)
        services = self._services(errors)
        data = self._data_files(errors)
        disk = self._disk_info(errors)
        return {
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "live": bool(getattr(self.runtime, "live", False)),
            "system": system,
            "mode": mode,
            "robot": robot,
            "services": services,
            "data": data,
            "disk": disk,
            "tts_available": TTS_BIN.exists(),
            "cameras": ["head_fisheye", "head_right", "surround_front_left", "surround_front_right", "surround_rear_left", "surround_rear_right", "left_rgb", "right_rgb", "left_depth", "right_depth"],
            "errors": errors,
        }

    def _system_info(self, errors: list[str]) -> dict[str, Any]:
        try:
            return json.loads(SYSTEM_CFG.read_text(encoding="utf-8"))
        except Exception as error:
            errors.append(f"system.cfg: {error}")
            return {}

    def _mode_info(self, errors: list[str]) -> dict[str, Any]:
        auto = None
        try:
            text = MODE_CFG.read_text(encoding="utf-8")
            match = re.search(r'auto_enter_mode_type\s*=\s*"([^"]+)"', text)
            auto = match.group(1) if match else None
        except Exception as error:
            errors.append(f"mode config: {error}")
        current = auto
        try:
            files = sorted(LAUNCHER_LOG_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
            if files:
                with files[0].open("rb") as handle:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    handle.seek(max(0, size - 300_000))
                    tail = handle.read().decode("utf-8", "replace")
                matches = re.findall(r"Cur mode:([A-Z_]+)", tail)
                if matches:
                    current = matches[-1]
        except Exception as error:
            errors.append(f"launcher log: {error}")
        return {"auto": auto, "current": current}

    def _robot_snapshot(self, errors: list[str]) -> dict[str, Any]:
        try:
            snap = self.runtime.robot.snapshot(include_lidar=False, include_sensors=True)
        except Exception as error:
            errors.append(f"robot snapshot: {error}")
            return {"available": False, "error": str(error)}
        joints = snap.get("joint_state") or []
        by_name = {item.get("name"): item.get("position") for item in joints if isinstance(item, dict)}
        groups = {group: [by_name.get(name) for name in names] for group, names in JOINT_ORDER.items()}
        sensors = snap.get("sensors") or {}
        return {
            "available": bool(joints),
            "dry_run": snap.get("dry_run"),
            "joint_count": len(joints),
            "groups": groups,
            "sensors": sensors,
        }

    def _services(self, errors: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for unit in ("galbot_daemon", "galbot_svc_hpu_comm", "frpc"):
            result[unit] = self._systemctl(unit, user=False)
        result["galbot-console"] = self._systemctl("galbot-console", user=True)
        for name, pattern in {
            "left_arm_camera_capture": "left_arm_camera_capture",
            "right_arm_camera_capture": "right_arm_camera_capture",
            "front_head_camera_capture": "front_head_camera_capture",
            "surround_cameras_capture": "surround_cameras_capture",
            "swallows": "swallows",
            "teleoperation": "teleoperation",
            "data_backflow_service": "data_backflow_service",
        }.items():
            result[name] = self._pgrep(pattern)
        result["vps_20022"] = self._tcp(VPS_HOST, 20022)
        result["vps_28080"] = self._tcp(VPS_HOST, 28080)
        result["vps_7000"] = self._tcp(VPS_HOST, 7000)
        return result

    def _systemctl(self, unit: str, user: bool) -> str:
        command = ["systemctl"] + (["--user"] if user else []) + ["is-active", unit]
        env = os.environ.copy()
        if user:
            env["XDG_RUNTIME_DIR"] = "/run/user/1000"
            env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/run/user/1000/bus"
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=3, env=env)
            return completed.stdout.strip() or "unknown"
        except Exception:
            return "unknown"

    def _pgrep(self, pattern: str) -> bool:
        try:
            completed = subprocess.run(["pgrep", "-f", pattern], capture_output=True, timeout=3)
            return completed.returncode == 0
        except Exception:
            return False

    def _tcp(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return True
        except OSError:
            return False

    def _data_files(self, errors: list[str]) -> dict[str, Any]:
        files = []
        total = 0
        try:
            if SWALLOWS_DIR.exists():
                for path in sorted(SWALLOWS_DIR.rglob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
                    if path.is_file():
                        size = path.stat().st_size
                        total += size
                        if len(files) < 40:
                            files.append({"name": str(path.relative_to(SWALLOWS_DIR)), "size": size, "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime))})
        except Exception as error:
            errors.append(f"data files: {error}")
        return {"dir": str(SWALLOWS_DIR), "count": len(files), "total_bytes": total, "files": files}

    def _disk_info(self, errors: list[str]) -> dict[str, Any]:
        try:
            usage = shutil.disk_usage("/userdata" if Path("/userdata").exists() else "/")
            return {"total": usage.total, "used": usage.used, "free": usage.free}
        except Exception as error:
            errors.append(f"disk: {error}")
            return {}

    # ---------- actions ----------
    def list_actions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "label": action["label"],
                "description": action["description"],
                "warning": action["warning"],
                "steps": len(action["steps"]),
            }
            for name, action in ACTIONS.items()
        ]

    def run_action(self, name: str, confirmed: bool) -> dict[str, Any]:
        if name not in ACTIONS:
            raise ValueError(f"unknown action: {name}")
        if not confirmed:
            raise ValueError("action requires explicit confirmation")
        self._assert_live()
        start = time.time()
        log = self._execute(ACTIONS[name]["steps"])
        return {"ok": all(item.get("ok") for item in log), "action": name, "duration_s": round(time.time() - start, 2), "log": log}

    def run_sequence(self, steps: list[dict[str, Any]], confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("sequence requires explicit confirmation")
        self._assert_live()
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps must be a non-empty list")
        for step in steps:
            self._validate_step(step)
        start = time.time()
        log = self._execute(steps)
        return {"ok": all(item.get("ok") for item in log), "action": "sequence", "duration_s": round(time.time() - start, 2), "log": log}

    def _assert_live(self) -> None:
        if not getattr(self.runtime, "live", False):
            raise RuntimeError("core actions require the console to run in --live mode")
        if not getattr(self.runtime.session, "initialized", False):
            raise RuntimeError("SDK session is not initialized")

    def _validate_step(self, step: dict[str, Any]) -> None:
        if not isinstance(step, dict):
            raise ValueError("each step must be an object")
        kind = step.get("type")
        if kind == "joint":
            group = step.get("group")
            if group not in JOINT_ORDER:
                raise ValueError(f"invalid joint group: {group}")
            positions = step.get("positions")
            if not isinstance(positions, list) or len(positions) != len(JOINT_ORDER[group]):
                raise ValueError(f"{group} requires {len(JOINT_ORDER[group])} positions")
            speed = float(step.get("speed", 0.1))
            timeout = float(step.get("timeout", 30))
            if not 0.01 <= speed <= 0.5 or not 1 <= timeout <= 60:
                raise ValueError("joint speed must be 0.01-0.5 rad/s and timeout 1-60 s")
        elif kind == "gripper":
            if step.get("side") not in {"left", "right"}:
                raise ValueError("gripper side must be left or right")
            if not 0.0 <= float(step.get("width", 0.05)) <= 0.12:
                raise ValueError("gripper width must be 0-0.12 m")
            if not 0.01 <= float(step.get("speed", 0.05)) <= 0.2:
                raise ValueError("gripper speed must be 0.01-0.2 m/s")
            if not 1 <= float(step.get("effort", 10)) <= 100:
                raise ValueError("gripper effort must be 1-100")
        elif kind == "wait":
            seconds = float(step.get("seconds", 0.5))
            if not 0 <= seconds <= 60:
                raise ValueError("wait must be 0-60 s")
        elif kind == "shake":
            joint = str(step.get("joint", "left_arm_joint7"))
            if joint not in {"left_arm_joint6", "left_arm_joint7", "right_arm_joint6", "right_arm_joint7"}:
                raise ValueError("shake joint must be left/right arm joint6 or joint7")
            if not 0.0 <= float(step.get("amplitude", 0.08)) <= 0.2:
                raise ValueError("shake amplitude must be 0-0.2 rad")
            if not 1.0 <= float(step.get("frequency", 3.0)) <= 6.0:
                raise ValueError("shake frequency must be 1-6 Hz")
            if not 0.5 <= float(step.get("duration", 2.0)) <= 5.0:
                raise ValueError("shake duration must be 0.5-5 s")
        elif kind == "route":
            audio_name = str(step.get("audio", "")).strip()
            if not audio_name or Path(audio_name).name != audio_name:
                raise ValueError("audio path must be a file name")
            audio_path = (AUDIO_DIR / audio_name).resolve()
            if audio_path.parent != AUDIO_DIR.resolve() or not audio_path.is_file():
                raise ValueError(f"audio file not found: {audio_name}")
            volume = float(step.get("volume", 100.0))
            if not 0.0 <= volume <= 100.0:
                raise ValueError("volume must be 0-100")
        elif kind == "show":
            trajectory_name = str(step.get("trajectory", "")).strip()
            if not trajectory_name:
                raise ValueError("show requires trajectory")
            self._validate_step({"type": "trajectory", "path": trajectory_name})
            audio_name = str(step.get("audio", "")).strip()
            if not audio_name or Path(audio_name).name != audio_name:
                raise ValueError("audio path must be a file name")
            audio_path = (AUDIO_DIR / audio_name).resolve()
            if audio_path.parent != AUDIO_DIR.resolve() or not audio_path.is_file():
                raise ValueError(f"audio file not found: {audio_name}")
            volume = float(step.get("volume", 55.0))
            if not 0.0 <= volume <= 100.0:
                raise ValueError("volume must be 0-100")
        elif kind == "trajectory":
            name = str(step.get("path", "")).strip()
            if not name or Path(name).name != name:
                raise ValueError("trajectory path must be a file name")
            path = (TRAJECTORY_DIR / name).resolve()
            if path.parent != TRAJECTORY_DIR.resolve() or not path.is_file():
                raise ValueError(f"trajectory file not found: {name}")
            data = json.loads(path.read_text(encoding="utf-8"))
            groups = data.get("groups")
            points = data.get("points")
            if not isinstance(groups, list) or not groups or any(group not in JOINT_ORDER for group in groups):
                raise ValueError("invalid trajectory groups")
            if not isinstance(points, list) or not points:
                raise ValueError("invalid trajectory points")
            for frame in points:
                if not isinstance(frame, dict):
                    raise ValueError("invalid trajectory frame")
                for group in groups:
                    values = frame.get(group)
                    if not isinstance(values, list) or len(values) != len(JOINT_ORDER[group]):
                        raise ValueError(f"trajectory frame {group} length mismatch")
        elif kind == "stop":
            pass
        else:
            raise ValueError(f"unsupported step type: {kind}")

    def _load_trajectory_file(self, name: str):
        path = (TRAJECTORY_DIR / name).resolve()
        data = json.loads(path.read_text(encoding="utf-8"))
        groups = list(data["groups"])
        joint_names = [joint for group in groups for joint in JOINT_ORDER[group]]
        frames = []
        for frame in data["points"]:
            values = [float(value) for group in groups for value in frame[group]]
            if len(values) != len(joint_names):
                raise ValueError("trajectory frame length mismatch")
            frames.append((float(frame["t"]), values))
        if not frames:
            raise ValueError("trajectory has no points")
        robot, sdk = self.runtime.session.platform.require_robot()
        trajectory = sdk.Trajectory()
        trajectory.joint_names = joint_names
        points = []
        for timestamp, values in frames:
            point = sdk.TrajectoryPoint()
            point.time_from_start_second = timestamp
            commands = []
            for value in values:
                command = sdk.JointCommand()
                command.position = value
                commands.append(command)
            point.joint_command_vec = commands
            points.append(point)
        trajectory.points = points
        return robot, sdk, groups, frames, trajectory, frames[-1][0]

    def _switch_trajectory_controllers(self, robot: Any, sdk: Any, groups: list[str]) -> None:
        controller_names = {
            "leg": "LEG_PVT_CTRL",
            "head": "HEAD_PVT_CTRL",
            "left_arm": "LEFT_ARM_PVT_CTRL",
            "right_arm": "RIGHT_ARM_PVT_CTRL",
        }
        for group in groups:
            controller_name = controller_names.get(group)
            if controller_name is None:
                raise ValueError(f"unsupported group in trajectory: {group}")
            controller = getattr(sdk.G1ControllerName, controller_name)
            status = robot.switch_controller(controller)
            if status != sdk.ControlStatus.SUCCESS:
                raise RuntimeError(f"switch_controller({controller_name}) failed: {status}")

    def _execute_show_file(self, name: str, audio_name: str, volume: float) -> dict[str, Any]:
        audio_path = (AUDIO_DIR / audio_name).resolve()
        if not audio_path.is_file():
            raise ValueError(f"audio file not found: {audio_name}")
        if not AUDIO_PLAYER.is_file():
            raise ValueError("audio player script not found")
        BGM_SYNC_FILE.unlink(missing_ok=True)
        audio_proc = subprocess.Popen(
            [sys.executable, str(AUDIO_PLAYER), str(audio_path), str(volume)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        audio_lines: list[str] = []
        try:
            ready = False
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                line = audio_proc.stdout.readline() if audio_proc.stdout else ""
                if not line:
                    break
                audio_lines.append(line.rstrip())
                if line.startswith("READY"):
                    ready = True
                    break
            if not ready:
                raise RuntimeError(f"BGM player not ready: {audio_lines[-3:]}")
            robot, sdk, groups, frames, trajectory, duration = self._load_trajectory_file(name)
            self._switch_trajectory_controllers(robot, sdk, groups)
            BGM_SYNC_FILE.touch()
            sync_timestamp = time.time()
            started = time.monotonic()
            status = robot.execute_joint_trajectory(trajectory, True)
            elapsed = time.monotonic() - started
            if elapsed < duration - 0.2:
                time.sleep(duration - elapsed + 0.2)
            success = status == sdk.ControlStatus.SUCCESS
            try:
                returncode = audio_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                audio_proc.terminate()
                returncode = audio_proc.wait(timeout=3)
            if audio_proc.stdout:
                tail = audio_proc.stdout.read()
                if tail:
                    audio_lines.extend(tail.strip().splitlines())
            return {
                "success": bool(success) and returncode == 0,
                "status": getattr(status, "name", str(status)),
                "points": len(frames),
                "duration_seconds": duration,
                "sync_timestamp": sync_timestamp,
                "audio": {"returncode": returncode, "lines": audio_lines[-4:]},
            }
        finally:
            if audio_proc.poll() is None:
                audio_proc.terminate()
                try:
                    audio_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    audio_proc.kill()
            BGM_SYNC_FILE.unlink(missing_ok=True)

    def _execute_trajectory_file(self, name: str) -> dict[str, Any]:
        robot, sdk, groups, frames, trajectory, duration = self._load_trajectory_file(name)
        self._switch_trajectory_controllers(robot, sdk, groups)
        started = time.monotonic()
        status = robot.execute_joint_trajectory(trajectory, True)
        elapsed = time.monotonic() - started
        if elapsed < duration - 0.2:
            time.sleep(duration - elapsed + 0.2)
        success = status == sdk.ControlStatus.SUCCESS
        return {
            "success": bool(success),
            "status": getattr(status, "name", str(status)),
            "points": len(frames),
            "duration_seconds": duration,
        }

    def _execute(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        log: list[dict[str, Any]] = []
        for index, step in enumerate(steps, start=1):
            started = time.time()
            try:
                self._validate_step(step)
                kind = step["type"]
                if kind == "joint":
                    group = step["group"]
                    result = self.runtime.robot.set_joint_positions(
                        [float(value) for value in step["positions"]],
                        JOINT_ORDER[group],
                        float(step.get("speed", 0.1)),
                        float(step.get("timeout", 30)),
                    )
                elif kind == "gripper":
                    result = self.runtime.robot.set_gripper_command(
                        str(step["side"]),
                        float(step.get("width", 0.05)),
                        float(step.get("speed", 0.05)),
                        float(step.get("effort", 10)),
                    )
                elif kind == "wait":
                    time.sleep(float(step.get("seconds", 0.5)))
                    result = {"accepted": True}
                elif kind == "shake":
                    joint = str(step.get("joint", "left_arm_joint7"))
                    group = "right_arm" if joint.startswith("right_") else "left_arm"
                    result = self.runtime.session.platform.robot.wrist_shake(
                        group=group,
                        joint=joint,
                        amplitude=float(step.get("amplitude", 0.08)),
                        frequency=float(step.get("frequency", 3.0)),
                        duration=float(step.get("duration", 2.0)),
                    )
                elif kind == "route":
                    result = self._execute_route_file(
                        str(step["audio"]),
                        float(step.get("volume", 100.0)),
                    )
                elif kind == "show":
                    result = self._execute_show_file(
                        str(step["trajectory"]),
                        str(step["audio"]),
                        float(step.get("volume", 55.0)),
                    )
                elif kind == "trajectory":
                    result = self._execute_trajectory_file(str(step["path"]))
                elif kind == "stop":
                    stop_result = self.runtime.motion.stop_controller(group="all")
                    base_result = self.runtime.robot.set_base_velocity([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 0.0)
                    result = {"stop_controller": stop_result, "base": base_result}
                else:
                    raise ValueError(f"unsupported step type: {kind}")
                wait = float(step.get("wait", 0.0))
                if wait:
                    time.sleep(wait)
                log.append({"index": index, "type": kind, "ok": True, "result": result, "elapsed_s": round(time.time() - started, 2)})
            except Exception as error:
                log.append({"index": index, "type": step.get("type") if isinstance(step, dict) else "invalid", "ok": False, "error": str(error), "elapsed_s": round(time.time() - started, 2)})
                break
        return log
