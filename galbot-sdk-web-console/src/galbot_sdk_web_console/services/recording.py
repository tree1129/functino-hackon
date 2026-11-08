"""Server-owned Rerun recording lifecycle and sampling workers."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import numpy as np

from .robot import JOINT_GROUPS
from ..geometry.rerun_robot import (
    RECORDING_FORMAT_NAME,
    RECORDING_FORMAT_VERSION,
    RERUN_APPLICATION_ID,
    load_rerun_urdf_tree,
    recording_blueprint,
    robot_model_descriptor,
    robot_model_recording_path,
    robot_model_recording_id,
)


CAMERA_SOURCES = {
    "camera.left_rgb",
    "camera.left_depth",
    "camera.right_rgb",
    "camera.right_depth",
    "camera.head_fisheye",
}
BASE_SOURCES = {
    "joints", "base_pose", "lidar", "static_map", "motion_obstacles",
    "navigation", "imu", "grippers", "bms",
}
ALL_SOURCES = BASE_SOURCES | CAMERA_SOURCES
JOINT_GROUP_BY_NAME = {
    joint: group
    for group, joints in JOINT_GROUPS.items()
    for joint in joints
}


class RecordingService:
    """Own one explicit recording intent and at most one active Rerun writer."""

    def __init__(self, navigation: Any, robot: Any, motion: Any, *, model: str, root: Path | None = None, static_map: Path | None = None) -> None:
        self.navigation = navigation
        self.robot = robot
        self.motion = motion
        configured_root = Path(os.getenv("GALBOT_RECORDINGS_DIR", "recordings"))
        self.root = (root or configured_root).resolve()
        self.static_map = static_map
        self.model = model
        self.viewer_port = int(os.getenv("GALBOT_RERUN_WEB_PORT", "9090"))
        self.grpc_port = int(os.getenv("GALBOT_RERUN_GRPC_PORT", "0"))
        self._live_uri: str | None = None
        self._live_port: int | None = None
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._camera_threads: list[threading.Thread] = []
        self._file_stream: Any | None = None
        self._live_stream: Any | None = None
        self._state = "INACTIVE"
        self._config: dict[str, Any] | None = None
        self._recording_id: str | None = None
        self._partial_path: Path | None = None
        self._final_path: Path | None = None
        self._started_ns: int | None = None
        self._paused_ns: int | None = None
        self._paused_total_ns = 0
        self._samples: dict[str, int] = {}
        self._drops: dict[str, int] = {}
        self._errors: dict[str, str] = {}
        self._download_secret = secrets.token_bytes(32)
        self._last_obstacle_snapshot = ""
        self._logged_obstacles: set[str] = set()
        self._robot_model_root_frame: str | None = None
        self._robot_model_joints: dict[str, Any] = {}
        self._coordinate_frames: dict[str, str] = {}
        self._playback_lock = threading.Lock()
        self._playback_cache_root = Path(tempfile.gettempdir()) / "galbot-rerun-playback"

    def capabilities(self) -> dict[str, Any]:
        labels = {
            "joints": "全身关节角（腿、头、双臂）", "base_pose": "底盘位姿", "lidar": "实时 LiDAR",
            "static_map": "静态地图", "motion_obstacles": "Motion 碰撞体",
            "navigation": "导航任务与路径", "imu": "IMU", "grippers": "夹爪状态", "bms": "电源状态",
            "camera.left_rgb": "左臂 RGB", "camera.left_depth": "左臂深度",
            "camera.right_rgb": "右臂 RGB", "camera.right_depth": "右臂深度",
            "camera.head_fisheye": "头部鱼眼 RGB",
        }
        groups = {
            "robot": ["joints", "base_pose"],
            "camera": sorted(CAMERA_SOURCES),
            "sensor": ["lidar", "imu", "grippers", "bms"],
            "environment": ["static_map", "motion_obstacles", "navigation"],
        }
        return {
            "model": self.model,
            "model_asset_available": robot_model_recording_path(self.model, representation="visual").is_file(),
            "model_representations": {"visual": robot_model_recording_path(self.model, representation="visual").is_file(), "collision": robot_model_recording_path(self.model, representation="collision").is_file()},
            "sources": [{"id": key, "label": labels[key]} for key in sorted(ALL_SOURCES)],
            "groups": groups,
            "default_sources": ["joints", "base_pose"],
            "recording_root": str(self.root),
            "sample_hz": {"default": 5, "minimum": 0.2, "maximum": 30},
            "camera_fps": {"default": 5, "minimum": 1, "maximum": 10},
        }

    def arm(self, config: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._state not in {"INACTIVE", "ARMED"}:
                raise RuntimeError("an active recording must be finished or cancelled first")
            sources = list(dict.fromkeys(str(value) for value in config["sources"]))
            unknown = sorted(set(sources) - ALL_SOURCES)
            if unknown:
                raise ValueError(f"unknown recording sources: {', '.join(unknown)}")
            directory = self._safe_directory(str(config.get("directory", "")))
            basename = str(config.get("filename") or datetime.now().strftime("galbot_%Y%m%d_%H%M%S"))
            final_path = directory / f"{basename}.rrd"
            partial_path = directory / f".{basename}.{uuid.uuid4().hex}.partial.rrd"
            if final_path.exists():
                raise ValueError(f"recording already exists: {final_path.name}")
            directory.mkdir(parents=True, exist_ok=True)
            self._config = {**config, "sources": sources, "filename": basename, "directory": str(directory.relative_to(self.root)).replace("\\", "/")}
            self._recording_id = basename
            self._partial_path = partial_path
            self._final_path = final_path
            self._state = "ARMED"
            self._reset_metrics()
            return self.status()

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._state != "ARMED" or self._partial_path is None or self._config is None:
                raise RuntimeError("recording is not armed")
            import rerun as rr

            recording_id = robot_model_recording_id(self.model)
            self._file_stream = rr.RecordingStream(RERUN_APPLICATION_ID, recording_id=recording_id)
            self._file_stream.save(self._partial_path)
            self._live_stream = rr.RecordingStream(RERUN_APPLICATION_ID, recording_id=recording_id)
            blueprint = recording_blueprint()
            self._file_stream.send_blueprint(blueprint)
            self._live_stream.send_blueprint(blueprint)
            try:
                selected_port = self.grpc_port or self._available_port()
                self._live_uri = self._live_stream.serve_grpc(
                    grpc_port=selected_port,
                    server_memory_limit="512MiB",
                    cors_allow_origin=["http://*", "https://*"],
                )
                self._live_port = urlparse(self._live_uri.replace("rerun+", "")).port or selected_port
            except Exception as error:
                self._errors["rerun_live"] = str(error)
                self._live_stream = None
                self._live_uri = None
                self._live_port = None
            self._stop.clear()
            self._started_ns = time.time_ns()
            self._state = "RECORDING"
            self._log_static()
            self._thread = threading.Thread(target=self._sample_loop, name="galbot-recorder", daemon=True)
            self._thread.start()
            self._camera_threads = []
            for source in self._config["sources"]:
                if source in CAMERA_SOURCES:
                    worker = threading.Thread(target=self._camera_loop, args=(source.removeprefix("camera."),), name=f"galbot-recorder-{source}", daemon=True)
                    worker.start()
                    self._camera_threads.append(worker)
            return self.status()

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if self._state != "RECORDING":
                raise RuntimeError("only an active recording can be paused")
            self._state = "PAUSED"
            self._paused_ns = time.time_ns()
            self._log_text("/status/recording", "recording paused", self._paused_ns)
            return self.status()

    def resume(self) -> dict[str, Any]:
        with self._lock:
            if self._state != "PAUSED":
                raise RuntimeError("recording is not paused")
            now = time.time_ns()
            self._paused_total_ns += now - int(self._paused_ns or now)
            self._paused_ns = None
            self._state = "RECORDING"
            self._log_text("/status/recording", "recording resumed", now)
            return self.status()

    def finish(self) -> dict[str, Any]:
        with self._lock:
            if self._state not in {"RECORDING", "PAUSED"}:
                raise RuntimeError("there is no active recording to finish")
            self._state = "FINALIZING"
        self._stop_workers()
        with self._lock:
            self._close_streams()
            if self._partial_path is None or self._final_path is None:
                raise RuntimeError("recording paths are unavailable")
            self._partial_path.replace(self._final_path)
            completed = self._completed_metadata()
            self._write_metadata(self._final_path, completed)
            self._state = "INACTIVE"
            result = {**completed, "state": "INACTIVE", "completed": True}
            self._clear_session()
            return result

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            if self._state == "INACTIVE":
                return self.status()
            self._state = "CANCELLING"
        self._stop_workers()
        with self._lock:
            self._close_streams()
            if self._partial_path and self._partial_path.is_file():
                self._partial_path.unlink()
            cancelled_id = self._recording_id
            self._state = "INACTIVE"
            self._clear_session()
            return {"state": "INACTIVE", "cancelled": True, "recording_id": cancelled_id}

    def close(self) -> None:
        if self._state in {"RECORDING", "PAUSED", "FINALIZING"}:
            try:
                self.finish()
            except Exception:
                self.cancel()
        elif self._state == "ARMED":
            self.cancel()

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = self._paused_ns or time.time_ns()
            duration = 0.0 if self._started_ns is None else max(0.0, (now - self._started_ns - self._paused_total_ns) / 1e9)
            size = self._partial_path.stat().st_size if self._partial_path and self._partial_path.is_file() else 0
            return {
                "state": self._state,
                "recording_id": self._recording_id,
                "config": self._config,
                "duration_seconds": duration,
                "size_bytes": size,
                "samples": dict(self._samples),
                "drops": dict(self._drops),
                "errors": dict(self._errors),
                "viewer_port": self.viewer_port,
                "grpc_port": self._live_port,
                "live_available": self._live_stream is not None,
            }

    def recordings(self) -> list[dict[str, Any]]:
        self.root.mkdir(parents=True, exist_ok=True)
        result = []
        for path in sorted(self.root.rglob("*.rrd"), key=lambda item: item.stat().st_mtime_ns, reverse=True):
            if path.name.endswith(".partial.rrd"):
                continue
            metadata_path = path.with_suffix(".json")
            metadata: dict[str, Any] = {}
            if metadata_path.is_file():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    metadata = {}
            relative = path.relative_to(self.root).as_posix()
            result.append({
                **metadata,
                "id": relative.removesuffix(".rrd"),
                "name": path.name,
                "relative_path": relative,
                "size_bytes": path.stat().st_size,
                "modified_ns": path.stat().st_mtime_ns,
                "playback_token": self.playback_token(relative.removesuffix(".rrd")),
            })
        return result

    def recording_file(self, recording_id: str) -> Path:
        candidate = (self.root / f"{recording_id}.rrd").resolve()
        if self.root not in candidate.parents or not candidate.is_file() or candidate.name.endswith(".partial.rrd"):
            raise FileNotFoundError(recording_id)
        return candidate

    def playback_file(self, recording_id: str, representation: str | None = None) -> Path:
        """Return one viewer-ready RRD assembled from shared model and telemetry.

        Rerun's web viewer treats two URLs with the same Store ID as duplicate
        recordings.  The official merge command combines them into one stream.
        The result lives in the system cache, never beside the user's recording.
        """
        recording = self.recording_file(recording_id)
        # Older recordings predate the representation field and used collision.
        selected_representation = representation
        metadata_path = recording.with_suffix(".json")
        if selected_representation not in {"visual", "collision"} and metadata_path.is_file():
            try:
                selected_representation = json.loads(metadata_path.read_text(encoding="utf-8")).get("config", {}).get("representation")
            except (OSError, ValueError, TypeError):
                pass
        if selected_representation not in {"visual", "collision"}:
            selected_representation = "collision"
        model_asset = robot_model_recording_path(self.model, representation=selected_representation)
        if not model_asset.is_file():
            raise RuntimeError(
                f"{selected_representation} robot model asset is missing: {model_asset}. "
                "Generate it with scripts/generate_robot_model_recording.py before playback."
            )
        signature = hashlib.sha256(
            f"{recording}:{recording.stat().st_size}:{recording.stat().st_mtime_ns}:"
            f"{model_asset}:{model_asset.stat().st_size}:{model_asset.stat().st_mtime_ns}:{selected_representation}".encode()
        ).hexdigest()[:20]
        destination = self._playback_cache_root / f"{recording.stem}-{signature}.rrd"
        if destination.is_file():
            return destination
        with self._playback_lock:
            if destination.is_file():
                return destination
            self._playback_cache_root.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".partial.rrd")
            try:
                subprocess.run(
                    [sys.executable, "-m", "rerun", "rrd", "merge", str(model_asset), str(recording), "-o", str(temporary)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                temporary.replace(destination)
            except subprocess.TimeoutExpired as error:
                temporary.unlink(missing_ok=True)
                message = f"Rerun merge timed out after {error.timeout} seconds"
                print(f"Playback preparation failed: {message}", file=sys.stderr, flush=True)
                raise RuntimeError(message) from error
            except subprocess.CalledProcessError as error:
                temporary.unlink(missing_ok=True)
                output = (error.stderr or error.stdout or "").strip()
                detail = output[-4000:] if output else "the Rerun CLI produced no diagnostic output"
                message = f"Rerun merge exited with code {error.returncode}: {detail}"
                print(f"Playback preparation failed: {message}", file=sys.stderr, flush=True)
                raise RuntimeError(message) from error
            except OSError as error:
                temporary.unlink(missing_ok=True)
                message = f"could not start the Rerun merge command: {error}"
                print(f"Playback preparation failed: {message}", file=sys.stderr, flush=True)
                raise RuntimeError(message) from error
        return destination

    def delete(self, recording_id: str) -> None:
        path = self.recording_file(recording_id)
        path.unlink()
        metadata = path.with_suffix(".json")
        if metadata.is_file():
            metadata.unlink()

    def import_recording(self, filename: str, data: bytes) -> dict[str, Any]:
        """Store a user-selected Rerun recording inside the managed recording root."""
        temporary, destination = self.prepare_import(filename)
        if not data:
            raise ValueError("recording file is empty")
        try:
            temporary.write_bytes(data)
            return self.complete_import(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()

    def prepare_import(self, filename: str) -> tuple[Path, Path]:
        name = Path(filename).name
        if name != filename or not name.lower().endswith(".rrd") or name.lower().endswith(".partial.rrd"):
            raise ValueError("only a plain .rrd filename is allowed")
        self.root.mkdir(parents=True, exist_ok=True)
        destination = (self.root / name).resolve()
        if self.root not in destination.parents:
            raise ValueError("recording destination escapes the configured root")
        if destination.exists():
            stem, suffix, index = destination.stem, destination.suffix, 2
            while destination.exists():
                destination = (self.root / f"{stem}_{index}{suffix}").resolve()
                index += 1
        temporary = self.root / f".{uuid.uuid4().hex}.upload.rrd"
        return temporary, destination

    def complete_import(self, temporary: Path, destination: Path) -> dict[str, Any]:
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise ValueError("recording file is empty")
        temporary.replace(destination)
        recording_id = destination.relative_to(self.root).as_posix().removesuffix(".rrd")
        return {
            "id": recording_id,
            "name": destination.name,
            "relative_path": destination.relative_to(self.root).as_posix(),
            "size_bytes": destination.stat().st_size,
            "modified_ns": destination.stat().st_mtime_ns,
            "model": self.model,
            "playback_token": self.playback_token(recording_id),
            "imported": True,
        }

    def playback_token(self, recording_id: str) -> str:
        return hmac.new(self._download_secret, recording_id.encode(), hashlib.sha256).hexdigest()

    def validate_playback_token(self, recording_id: str, token: str) -> bool:
        return bool(token) and hmac.compare_digest(token, self.playback_token(recording_id))

    def _safe_directory(self, value: str) -> Path:
        pure = PurePosixPath(value.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
            for part in pure.parts for character in part
        ):
            raise ValueError("recording directory must remain inside the configured root")
        candidate = (self.root / Path(*pure.parts)).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("recording directory escapes the configured root")
        return candidate

    @staticmethod
    def _available_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def _sample_loop(self) -> None:
        assert self._config is not None
        interval = 1.0 / float(self._config["sample_hz"])
        sources = set(self._config["sources"])
        while not self._stop.is_set():
            started = time.monotonic()
            if self._state == "RECORDING":
                captured_ns = time.time_ns()
                try:
                    navigation = self.navigation.snapshot()
                    if "base_pose" in sources and navigation.pose:
                        self._log_pose(navigation.pose, captured_ns)
                    if "navigation" in sources:
                        self._log_json("/metadata/navigation/state", navigation.__dict__, captured_ns)
                        path = self.navigation.path_snapshot(navigation.task_id)
                        if path.get("available"):
                            points = [[pose[axis] for axis in ("x", "y", "z")] for pose in path.get("poses", [])]
                            self._log_path("/world/navigation/path", points, captured_ns)
                    needs_sensors = bool(sources & {"imu", "grippers", "bms"})
                    robot = self.robot.snapshot(
                        include_lidar="lidar" in sources,
                        lidar_center=navigation.pose or {},
                        include_sensors=needs_sensors,
                        include_joints="joints" in sources,
                    )
                    if "joints" in sources and robot.get("joint_state"):
                        self._log_joints(robot["joint_state"], captured_ns)
                    if "lidar" in sources and robot.get("lidar"):
                        lidar = robot["lidar"]
                        self._log_points(
                            "/world/lidar/base",
                            [[p["x"], p["y"], p["z"]] for p in lidar.get("points", [])],
                            captured_ns,
                            [70, 220, 180],
                            frame_id=str(lidar.get("frame_id") or "map"),
                        )
                    sensors = robot.get("sensors") or {}
                    for source in ("imu", "grippers", "bms"):
                        if source in sources and sensors.get(source) is not None:
                            self._log_json(f"/sensors/{source}", sensors[source], captured_ns)
                    if "motion_obstacles" in sources:
                        self._log_obstacles(self.motion.list_obstacles(), captured_ns)
                except Exception as error:
                    self._errors["telemetry"] = str(error)
            self._stop.wait(max(0.0, interval - (time.monotonic() - started)))

    def _camera_loop(self, source: str) -> None:
        assert self._config is not None
        interval = 1.0 / int(self._config["camera_fps"])
        last_timestamp: int | None = None
        while not self._stop.is_set():
            started = time.monotonic()
            if self._state == "RECORDING":
                try:
                    frame = self.robot.camera_frame(source, last_timestamp, preview_depth=False)
                    if not frame.get("unchanged"):
                        last_timestamp = int(frame.get("timestamp_ns") or time.time_ns())
                        self._log_camera(source, frame, last_timestamp)
                except Exception as error:
                    self._errors[source] = str(error)
            self._stop.wait(max(0.0, interval - (time.monotonic() - started)))

    def _log_static(self) -> None:
        now = time.time_ns()
        representation = (self._config or {}).get("representation", "collision")
        model_descriptor = robot_model_descriptor(self.model, representation=representation)
        self._log_json("/metadata/recording_format", {
            "name": RECORDING_FORMAT_NAME,
            "version": RECORDING_FORMAT_VERSION,
            "application_id": RERUN_APPLICATION_ID,
            "timeline": "capture_time",
            "world_frame": "map",
            "robot_model_store_id": str(robot_model_recording_id(self.model)),
        }, now, static=True)
        self._log_json("/metadata/session", {
            "model": self.model,
            "robot_model": model_descriptor,
            "config": self._config,
            "started_ns": now,
        }, now, static=True)
        import rerun as rr
        self._log_entity("/world", rr.ViewCoordinates.RIGHT_HAND_Z_UP, now, static=True)
        self._log_entity("/world", rr.CoordinateFrame("map"), now, static=True)
        if model_descriptor and self._config and "joints" in self._config["sources"]:
            self._log_json("/metadata/robot_model", model_descriptor, now, static=True)
            try:
                tree, loaded_descriptor = load_rerun_urdf_tree(self.model, representation=representation)
                # Live viewing has only one gRPC source, so it still needs the
                # geometry. File recordings intentionally reference the shared
                # asset instead of embedding these large meshes again.
                if self._live_stream is not None:
                    with self._write_lock:
                        tree.log_urdf_to_recording(self._live_stream)
                self._robot_model_root_frame = loaded_descriptor["root_frame"]
                self._robot_model_joints = {
                    name: tree.get_joint_by_name(name)
                    for name in JOINT_GROUP_BY_NAME
                    if tree.get_joint_by_name(name) is not None
                }
                missing = sorted(set(JOINT_GROUP_BY_NAME) - set(self._robot_model_joints))
                if missing:
                    raise ValueError(f"robot model is missing recorded joints: {', '.join(missing)}")
                self._log_entity(
                    "/world/robot/model/root",
                    rr.Transform3D(parent_frame="base_link", child_frame=self._robot_model_root_frame),
                    now,
                    static=True,
                )
                self._samples["/world/robot/model"] = 1
            except Exception as error:
                self._errors["robot_model"] = str(error)
        if self._config and "static_map" in self._config["sources"] and self.static_map and self.static_map.is_file():
            try:
                from ..geometry.pcd import load_pcd_xyz
                self._log_points("/world/map", load_pcd_xyz(self.static_map).tolist(), now, [115, 130, 150], static=True, frame_id="map")
            except Exception as error:
                self._errors["static_map"] = str(error)

    def _streams(self) -> list[Any]:
        return [stream for stream in (self._file_stream, self._live_stream) if stream is not None]

    def _log_entity(self, path: str, entity: Any, timestamp_ns: int, *, static: bool = False) -> None:
        with self._write_lock:
            for stream in self._streams():
                if not static:
                    stream.set_time("capture_time", timestamp=np.datetime64(timestamp_ns, "ns"))
                stream.log(path, entity, static=static)
        self._samples[path] = self._samples.get(path, 0) + 1

    def _log_text(self, path: str, text: str, timestamp_ns: int) -> None:
        import rerun as rr
        self._log_entity(path, rr.TextLog(text), timestamp_ns)

    def _log_json(self, path: str, value: Any, timestamp_ns: int, *, static: bool = False) -> None:
        import rerun as rr
        self._log_entity(path, rr.TextDocument(json.dumps(value, ensure_ascii=False, default=str), media_type="application/json"), timestamp_ns, static=static)

    def _log_pose(self, pose: dict[str, Any], timestamp_ns: int) -> None:
        import rerun as rr
        self._log_entity(
            "/world/robot/base_link",
            rr.Transform3D(
                translation=[pose["x"], pose["y"], pose["z"]],
                quaternion=[pose["qx"], pose["qy"], pose["qz"], pose["qw"]],
                parent_frame="map", child_frame="base_link",
            ),
            timestamp_ns,
        )
        self._log_json("/metadata/base_pose/time", {"source_time_ns": None, "receive_time_ns": timestamp_ns, "time_quality": "receive"}, timestamp_ns)

    def _log_joints(self, joints: list[dict[str, Any]], fallback_ns: int) -> None:
        import rerun as rr
        for joint in joints:
            name = str(joint.get("name", ""))
            group = JOINT_GROUP_BY_NAME.get(name)
            if group is None:
                continue
            timestamp_ns = int(joint.get("timestamp_ns") or fallback_ns)
            position = float(joint.get("position", 0))
            self._log_entity(f"/robot/joints/{group}/{name}/position", rr.Scalars([position]), timestamp_ns)
            model_joint = self._robot_model_joints.get(name)
            if model_joint is not None:
                self._log_entity(
                    f"/world/robot/model/joints/{name}",
                    model_joint.compute_transform(position, clamp=False),
                    timestamp_ns,
                )

    def _ensure_coordinate_frame(self, path: str, frame_id: str, timestamp_ns: int) -> None:
        if not frame_id or self._coordinate_frames.get(path) == frame_id:
            return
        import rerun as rr
        self._log_entity(path, rr.CoordinateFrame(frame_id), timestamp_ns, static=True)
        self._coordinate_frames[path] = frame_id

    def _log_points(self, path: str, points: list[list[float]], timestamp_ns: int, color: list[int], *, static: bool = False, frame_id: str = "map") -> None:
        if not points:
            return
        import rerun as rr
        self._ensure_coordinate_frame(path, frame_id, timestamp_ns)
        self._log_entity(path, rr.Points3D(points, colors=color, radii=0.015), timestamp_ns, static=static)

    def _log_path(self, path: str, points: list[list[float]], timestamp_ns: int) -> None:
        if len(points) < 2:
            return
        import rerun as rr
        self._ensure_coordinate_frame(path, "map", timestamp_ns)
        self._log_entity(path, rr.LineStrips3D([points], colors=[0, 145, 255], radii=0.025), timestamp_ns)

    def _log_obstacles(self, snapshot: dict[str, Any], timestamp_ns: int) -> None:
        import rerun as rr
        signature = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
        if signature == self._last_obstacle_snapshot:
            return
        self._last_obstacle_snapshot = signature
        self._log_json("/metadata/obstacles/snapshot", snapshot, timestamp_ns)
        current = {
            str(obstacle.get("obstacle_id", "unknown")).replace("/", "_")
            for obstacle in snapshot.get("obstacles", []) if not obstacle.get("external") and obstacle.get("pose")
        }
        for removed in self._logged_obstacles - current:
            self._log_entity(f"/world/obstacles/{removed}", rr.Clear(recursive=True), timestamp_ns)
        self._logged_obstacles = current
        for obstacle in snapshot.get("obstacles", []):
            if obstacle.get("external") or not obstacle.get("pose"):
                continue
            name = str(obstacle.get("obstacle_id", "unknown")).replace("/", "_")
            pose = obstacle["pose"]
            scale = obstacle.get("scale", [0, 0, 0])
            path = f"/world/obstacles/{name}"
            self._ensure_coordinate_frame(path, "map", timestamp_ns)
            kind = obstacle.get("obstacle_type")
            if kind == "box":
                shape = rr.Boxes3D(centers=[pose[:3]], sizes=[scale], quaternions=[pose[3:7]], colors=[255, 150, 40, 180], labels=[name])
            elif kind == "sphere":
                shape = rr.Ellipsoids3D(centers=[pose[:3]], radii=[scale[0]], colors=[255, 150, 40, 180], labels=[name])
            elif kind == "cylinder":
                shape = rr.Cylinders3D(centers=[pose[:3]], quaternions=[pose[3:7]], radii=[scale[0]], lengths=[scale[1]], colors=[255, 150, 40, 180], labels=[name])
            else:
                continue
            self._log_entity(path, shape, timestamp_ns)

    def _log_camera(self, source: str, frame: dict[str, Any], timestamp_ns: int) -> None:
        import rerun as rr
        path = f"/sensors/cameras/{source}"
        if frame["kind"] == "rgb":
            entity = rr.EncodedImage(contents=frame["data"], media_type=frame.get("mime_type", "image/jpeg"))
        else:
            image = np.frombuffer(frame["data"], dtype="<u2", count=int(frame["width"]) * int(frame["height"])).reshape((int(frame["height"]), int(frame["width"])))
            entity = rr.DepthImage(image, meter=float(frame.get("depth_scale", 1000.0)))
        self._log_entity(f"{path}/{'image' if frame['kind'] == 'rgb' else 'depth'}", entity, timestamp_ns)

    def _stop_workers(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        for worker in self._camera_threads:
            worker.join(timeout=1)

    def _close_streams(self) -> None:
        with self._write_lock:
            for stream in self._streams():
                try:
                    stream.flush(timeout_sec=5)
                finally:
                    stream.disconnect()
            self._file_stream = None
            self._live_stream = None
            self._live_uri = None
            self._live_port = None

    def _completed_metadata(self) -> dict[str, Any]:
        status = self.status()
        return {
            "id": self._recording_id,
            "name": self._final_path.name if self._final_path else None,
            "relative_path": self._final_path.relative_to(self.root).as_posix() if self._final_path else None,
            "model": self.model,
            "config": self._config,
            "started_ns": self._started_ns,
            "finished_ns": time.time_ns(),
            "duration_seconds": status["duration_seconds"],
            "samples": status["samples"],
            "drops": status["drops"],
            "errors": status["errors"],
        }

    @staticmethod
    def _write_metadata(path: Path, metadata: dict[str, Any]) -> None:
        path.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def _reset_metrics(self) -> None:
        self._started_ns = None
        self._paused_ns = None
        self._paused_total_ns = 0
        self._samples = {}
        self._drops = {}
        self._errors = {}
        self._last_obstacle_snapshot = ""
        self._logged_obstacles = set()
        self._robot_model_root_frame = None
        self._robot_model_joints = {}
        self._coordinate_frames = {}

    def _clear_session(self) -> None:
        self._config = None
        self._recording_id = None
        self._partial_path = None
        self._final_path = None
        self._thread = None
        self._camera_threads = []
        self._started_ns = None
        self._paused_ns = None
        self._paused_total_ns = 0
