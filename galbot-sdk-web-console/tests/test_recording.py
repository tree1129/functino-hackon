from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from galbot_sdk_web_console.services.recording import RecordingService
from galbot_sdk_web_console.geometry.rerun_robot import (
    load_rerun_urdf_tree,
    robot_model_descriptor,
    robot_model_recording_id,
    robot_model_recording_path,
)
from galbot_sdk_web_console.services.robot import JOINT_GROUPS


class FakeStream:
    def __init__(self, *args, **kwargs):
        self.path = None
        self.logged = []
        self.entities = []

    def save(self, path):
        self.path = Path(path)
        self.path.write_bytes(b"rrd")

    def serve_grpc(self, **kwargs):
        return "rerun+http://127.0.0.1:9876/proxy"

    def send_blueprint(self, blueprint):
        pass

    def set_time(self, *args, **kwargs):
        pass

    def log(self, path, entity, **kwargs):
        self.logged.append(path)
        self.entities.append((path, entity))

    def flush(self, **kwargs):
        pass

    def disconnect(self):
        pass


class Navigation:
    def snapshot(self):
        return SimpleNamespace(pose={"x": 1, "y": 2, "z": 0, "qx": 0, "qy": 0, "qz": 0, "qw": 1, "frame_id": "map"}, task_id=None, status="IDLE")

    def path_snapshot(self, task_id):
        return {"available": False}


class Robot:
    def snapshot(self, **kwargs):
        return {"joint_state": None, "lidar": None, "sensors": None}


class Motion:
    def list_obstacles(self):
        return {"obstacles": [], "default_obstacle_ids": []}


def service(tmp_path):
    return RecordingService(Navigation(), Robot(), Motion(), model="g1", root=tmp_path)


def test_recording_requires_explicit_arm_and_returns_to_inactive(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("rerun.RecordingStream", FakeStream)
    recorder = service(tmp_path)

    with pytest.raises(RuntimeError):
        recorder.start()
    armed = recorder.arm({"sources": ["base_pose"], "directory": "runs", "filename": "test", "sample_hz": 5, "camera_fps": 5})
    assert armed["state"] == "ARMED"
    assert recorder.start()["state"] == "RECORDING"
    assert recorder.pause()["state"] == "PAUSED"
    assert recorder.resume()["state"] == "RECORDING"
    result = recorder.finish()

    assert result["completed"] is True
    assert result["state"] == "INACTIVE"
    assert (tmp_path / "runs" / "test.rrd").read_bytes() == b"rrd"
    assert (tmp_path / "runs" / "test.json").is_file()
    assert recorder.status()["state"] == "INACTIVE"


def test_recording_rejects_path_escape_and_cancel_removes_partial(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("rerun.RecordingStream", FakeStream)
    recorder = service(tmp_path)
    with pytest.raises(ValueError):
        recorder.arm({"sources": ["joints"], "directory": "../outside", "filename": "bad", "sample_hz": 5, "camera_fps": 5})

    recorder.arm({"sources": ["joints"], "directory": "", "filename": "cancelled", "sample_hz": 5, "camera_fps": 5})
    recorder.start()
    assert recorder.cancel()["cancelled"] is True
    assert not list(tmp_path.glob("*.partial.rrd"))
    assert not (tmp_path / "cancelled.rrd").exists()


def test_recording_playback_token_is_scoped_to_one_file(tmp_path) -> None:
    recorder = service(tmp_path)
    token = recorder.playback_token("one")
    assert recorder.validate_playback_token("one", token)
    assert not recorder.validate_playback_token("two", token)


def test_recording_import_is_managed_and_rejects_unsafe_names(tmp_path) -> None:
    recorder = service(tmp_path)
    imported = recorder.import_recording("debug.rrd", b"rrd-data")
    assert imported["id"] == "debug"
    assert (tmp_path / "debug.rrd").read_bytes() == b"rrd-data"
    assert recorder.validate_playback_token("debug", imported["playback_token"])
    second = recorder.import_recording("debug.rrd", b"other-data")
    assert second["name"] == "debug_2.rrd"
    assert (tmp_path / "debug_2.rrd").read_bytes() == b"other-data"
    with pytest.raises(ValueError):
        recorder.import_recording("../escape.rrd", b"rrd-data")


def test_recording_keeps_only_core_joint_angles(tmp_path) -> None:
    recorder = service(tmp_path)
    sink = FakeStream()
    recorder._file_stream = sink
    recorder._log_joints([
        {"name": "leg_joint1", "position": 0.1, "velocity": 2, "effort": 3, "timestamp_ns": 10},
        {"name": "head_joint2", "position": 0.2, "timestamp_ns": 10},
        {"name": "left_arm_joint7", "position": 0.3, "timestamp_ns": 10},
        {"name": "right_arm_joint7", "position": 0.4, "timestamp_ns": 10},
        {"name": "left_gripper_joint", "position": 0.5, "timestamp_ns": 10},
    ], 10)
    assert sink.logged == [
        "/robot/joints/leg/leg_joint1/position",
        "/robot/joints/head/head_joint2/position",
        "/robot/joints/left_arm/left_arm_joint7/position",
        "/robot/joints/right_arm/right_arm_joint7/position",
    ]


def test_g1_recording_model_retains_provenance_and_loads_as_static_reference() -> None:
    descriptor = robot_model_descriptor("g1")
    assert descriptor is not None
    assert descriptor["visual_urdf_path"] == "assets/robots/g1/visual/galbot_g1.urdf"
    assert descriptor["display_mode"] == "strict_urdf_joint_animation"
    tree, loaded = load_rerun_urdf_tree("g1")
    assert tree.name == "galbot_g1"
    assert loaded["root_frame"] == "robot_model/base_footprint"
    expected = {name for joints in JOINT_GROUPS.values() for name in joints}
    assert all(tree.get_joint_by_name(name) is not None for name in expected)


def test_collision_recording_uses_opaque_unique_orange_materials(monkeypatch) -> None:
    captured = {}

    def load_temporary_urdf(path, **_kwargs):
        root = ET.parse(path).getroot()
        materials = root.findall(".//visual/material")
        captured["names"] = [item.attrib["name"] for item in materials]
        captured["colors"] = [item.find("color").attrib["rgba"] for item in materials]
        return SimpleNamespace(root_link=lambda: SimpleNamespace(name="base_footprint"))

    monkeypatch.setattr("rerun.urdf.UrdfTree.from_file_path", load_temporary_urdf)
    load_rerun_urdf_tree("g1", representation="collision")

    assert captured["names"]
    assert len(captured["names"]) == len(set(captured["names"]))
    assert set(captured["colors"]) == {"0.95 0.48 0.10 1.0"}


def test_visual_recording_keeps_transform_only_links_in_the_urdf(monkeypatch) -> None:
    captured = {}

    def load_temporary_urdf(path, **_kwargs):
        root = ET.parse(path).getroot()
        captured["markers"] = [
            link.attrib["name"]
            for link in root.findall("link")
            if link.find("visual[@name='__rerun_transform_marker']") is not None
        ]
        return SimpleNamespace(root_link=lambda: SimpleNamespace(name="base_footprint"))

    monkeypatch.setattr("rerun.urdf.UrdfTree.from_file_path", load_temporary_urdf)
    load_rerun_urdf_tree("g1", representation="visual")

    assert captured["markers"]
    assert "base_footprint" in captured["markers"]


def test_model_recording_uses_stable_store_and_asset_location() -> None:
    assert robot_model_recording_id("g1") == robot_model_recording_id("g1")
    assert robot_model_recording_id("g1") != robot_model_recording_id("another")
    assert robot_model_recording_path("g1").as_posix().endswith("assets/robots/g1/rerun/model.rrd")
    assert robot_model_recording_path("g1", representation="visual").as_posix().endswith("assets/robots/g1/rerun/model-visual.rrd")


def test_world_geometry_uses_named_map_coordinate_frame(tmp_path) -> None:
    recorder = service(tmp_path)
    sink = FakeStream()
    recorder._file_stream = sink
    recorder._config = {"sources": ["joints"]}
    recorder._log_static()
    world_entities = [entity for path, entity in sink.entities if path == "/world"]
    assert any(type(entity).__name__ == "CoordinateFrame" for entity in world_entities)
    assert "/metadata/recording_format" in sink.logged


def test_playback_file_merges_shared_model_into_temporary_cache(tmp_path, monkeypatch) -> None:
    recorder = service(tmp_path)
    recording = tmp_path / "run.rrd"
    recording.write_bytes(b"telemetry")
    model_asset = tmp_path / "model.rrd"
    model_asset.write_bytes(b"model")
    cache = tmp_path / "cache"
    recorder._playback_cache_root = cache
    monkeypatch.setattr(
        "galbot_sdk_web_console.services.recording.robot_model_recording_path",
        lambda model, representation="collision": model_asset,
    )
    calls = []

    def merge(command, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"merged")

    monkeypatch.setattr("subprocess.run", merge)
    first = recorder.playback_file("run")
    second = recorder.playback_file("run")

    assert first == second
    assert first.read_bytes() == b"merged"
    assert len(calls) == 1
    assert calls[0][5:7] == [str(model_asset), str(recording)]
