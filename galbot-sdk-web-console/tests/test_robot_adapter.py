import struct
from math import sqrt

import numpy as np

from galbot_sdk_web_console.sdk.platforms.robot import PythonRobotAdapter
from galbot_sdk_web_console.sdk.platforms.python_sdk import PythonSdkPlatform
from galbot_sdk_web_console.services.robot import RobotService


def test_robot_initialization_excludes_unavailable_ultrasonic_and_infrared(monkeypatch) -> None:
    class SensorType:
        BASE_LIDAR = object()
        BASE_ULTRASONIC = object()
        CHASSIS_IMU = object()
        TORSO_IMU = object()
        LEFT_ARM_CAMERA = object()
        LEFT_ARM_DEPTH_CAMERA = object()
        LEFT_ARM_INFRA_CAMERA_1 = object()
        LEFT_ARM_INFRA_CAMERA_2 = object()
        RIGHT_ARM_CAMERA = object()
        HEAD_LEFT_CAMERA = object()

    class Robot:
        initialized_with = None

        def init(self, sensors):
            type(self).initialized_with = sensors
            return True

    class Navigation:
        def init(self):
            return True

    sdk = type("Sdk", (), {"SensorType": SensorType, "GalbotRobot": Robot, "GalbotNavigation": Navigation})
    monkeypatch.setattr("galbot_sdk_web_console.sdk.platforms.python_sdk.import_module", lambda _: sdk)

    platform = PythonSdkPlatform("g1")
    platform.initialize()

    assert platform.enabled_sensor_names == [
        "BASE_LIDAR", "CHASSIS_IMU", "HEAD_LEFT_CAMERA", "LEFT_ARM_CAMERA",
        "LEFT_ARM_DEPTH_CAMERA", "RIGHT_ARM_CAMERA", "TORSO_IMU",
    ]
    assert SensorType.BASE_ULTRASONIC not in Robot.initialized_with
    assert SensorType.LEFT_ARM_INFRA_CAMERA_1 not in Robot.initialized_with
    assert SensorType.LEFT_ARM_INFRA_CAMERA_2 not in Robot.initialized_with


def test_camera_frame_keeps_a_single_rgb_frame_as_binary() -> None:
    class SensorType:
        LEFT_ARM_CAMERA = object()

    class RgbOutputFormat:
        JPEG = object()

    class Robot:
        def get_rgb_data(self, sensor, output_format, compressed):
            assert sensor is SensorType.LEFT_ARM_CAMERA
            assert output_format is RgbOutputFormat.JPEG
            assert compressed is True
            return {"data": b"jpeg-bytes", "width": 320, "height": 240, "timestamp_ns": 7}

    class Platform:
        sdk_module = type("Sdk", (), {"SensorType": SensorType, "RgbOutputFormat": RgbOutputFormat})

        def require_robot(self):
            return Robot(), self.sdk_module

    result = PythonRobotAdapter(Platform()).camera_frame("left_rgb")

    assert result["kind"] == "rgb"
    assert result["mime_type"] == "image/jpeg"
    assert result["width"] == 320
    assert result["data"] == b"jpeg-bytes"


def test_depth_frame_is_vectorized_to_an_8_bit_display_payload() -> None:
    class SensorType:
        LEFT_ARM_DEPTH_CAMERA = object()

    class Robot:
        def get_depth_data(self, sensor):
            assert sensor is SensorType.LEFT_ARM_DEPTH_CAMERA
            return {"data": struct.pack("<HHHH", 0, 1000, 2000, 3000), "width": 2, "height": 2, "depth_scale": 1000.0}

    class Platform:
        sdk_module = type("Sdk", (), {"SensorType": SensorType})

        def require_robot(self):
            return Robot(), self.sdk_module

    result = PythonRobotAdapter(Platform()).camera_frame("left_depth")

    assert result["encoding"] == "depth_u8"
    assert len(result["data"]) == 4
    assert result["data"][0] == 0
    assert result["depth_low"] >= 1000
    assert result["depth_high"] <= 3000


def test_depth_frame_keeps_metric_source_data_for_recording() -> None:
    class SensorType:
        LEFT_ARM_DEPTH_CAMERA = object()

    raw = struct.pack("<HHHH", 0, 1000, 2000, 3000)

    class Robot:
        def get_depth_data(self, sensor):
            return {"data": raw, "width": 2, "height": 2, "depth_scale": 1000.0, "format": "16UC1"}

    class Platform:
        sdk_module = type("Sdk", (), {"SensorType": SensorType})

        def require_robot(self):
            return Robot(), self.sdk_module

    result = PythonRobotAdapter(Platform()).camera_frame("left_depth", preview_depth=False)

    assert result["encoding"] == "16UC1"
    assert result["depth_scale"] == 1000.0
    assert result["data"] == raw


def test_camera_frame_skips_payload_conversion_when_timestamp_is_unchanged() -> None:
    class SensorType:
        LEFT_ARM_DEPTH_CAMERA = object()

    class Robot:
        def get_depth_data(self, sensor):
            assert sensor is SensorType.LEFT_ARM_DEPTH_CAMERA
            return {"data": b"not-inspected", "width": 640, "height": 480, "timestamp_ns": 99}

    class Platform:
        sdk_module = type("Sdk", (), {"SensorType": SensorType})

        def require_robot(self):
            return Robot(), self.sdk_module

    result = PythonRobotAdapter(Platform()).camera_frame("left_depth", last_timestamp_ns=99)

    assert result == {"source": "left_depth", "timestamp_ns": 99, "unchanged": True}


def test_lidar_is_cropped_before_the_transmission_limit_is_applied() -> None:
    coordinates = [(float(index % 20), 0.0, 0.0) for index in range(2500)]
    cloud = {
        "fields": [
            {"name": "x", "offset": 0},
            {"name": "y", "offset": 4},
            {"name": "z", "offset": 8},
        ],
        "point_step": 12,
        "width": len(coordinates),
        "height": 1,
        "data": b"".join(struct.pack("<fff", *point) for point in coordinates),
        "header": {"frame_id": "base_link"},
    }

    result = PythonRobotAdapter._point_cloud_xyz(
        cloud,
        max_points=1000,
        crop={"half_x": 10.0, "half_y": 1.0, "half_z": 1.0},
        center={"x": 100.0, "y": 100.0, "z": 100.0},
    )

    assert result is not None
    assert result["selected_point_count"] == 1375
    assert len(result["points"]) == 1000
    assert all(point["x"] <= 10.0 for point in result["points"])
    assert result["truncated"] is True


def test_robot_service_only_passes_point_processing_options_to_adapter() -> None:
    class FakeRobotPort:
        def snapshot(self, include_lidar=False, lidar_options=None, include_sensors=False, include_joints=True):
            assert include_lidar is True
            assert set(lidar_options) == {"max_points", "crop", "center"}
            return {"joint_state": [], "lidar": None}

    class FakePlatform:
        connected = True
        robot = FakeRobotPort()

    class FakeSession:
        initialized = True
        platform = FakePlatform()

    service = RobotService(FakeSession(), live=True)
    service.configure_lidar_stream({"enabled": True, "max_points": 15000, "crop": None})
    result = service.snapshot(True, {"x": 1.0, "y": 2.0, "z": 0.0})
    assert result["dry_run"] is False


def _cloud(point=(1.0, 0.0, 0.0), frame_id="lidar_link", timestamp_ns=123) -> dict:
    return {
        "fields": [
            {"name": "x", "offset": 0},
            {"name": "y", "offset": 4},
            {"name": "z", "offset": 8},
        ],
        "point_step": 12,
        "width": 1,
        "height": 1,
        "data": struct.pack("<fff", *point),
        "header": {"frame_id": frame_id, "timestamp_ns": timestamp_ns},
    }


def test_lidar_points_are_rotated_and_translated_into_map_before_cropping() -> None:
    half_turn_z = sqrt(0.5)
    map_from_source = [10.0, 20.0, 1.0, 0.0, 0.0, half_turn_z, half_turn_z]

    result = PythonRobotAdapter._point_cloud_xyz(
        _cloud(),
        crop={"half_x": 2.0, "half_y": 2.0, "half_z": 2.0},
        center={"x": 10.0, "y": 20.0, "z": 1.0},
        transform=map_from_source,
    )

    assert result is not None
    assert result["frame_id"] == "map"
    assert result["source_frame_id"] == "lidar_link"
    assert result["transform_applied"] is True
    assert result["points"][0]["x"] == 10.0
    assert result["points"][0]["y"] == 21.0
    assert result["points"][0]["z"] == 1.0


def test_snapshot_composes_robot_pose_with_lidar_to_base_transform() -> None:
    class FakeRobot:
        def get_joint_states(self, _, __):
            return []

        def get_lidar_data(self, _):
            return _cloud()

        def get_transform(self, target, source, timestamp_ns=0, timeout_ms=0):
            assert (target, source, timestamp_ns, timeout_ms) == ("base_link", "lidar_link", 123, 100)
            return ([0.5, 0.0, 0.25, 0.0, 0.0, 0.0, 1.0], 120)

    class SensorType:
        BASE_LIDAR = object()

    class Platform:
        robot_instance = FakeRobot()
        sdk_module = type("Sdk", (), {"SensorType": SensorType})

    result = PythonRobotAdapter(Platform()).snapshot(
        include_lidar=True,
        lidar_options={
            "center": {"x": 2.0, "y": 3.0, "z": 0.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0},
        },
    )["lidar"]

    assert result is not None
    assert result["source_pose_map"] == [2.5, 3.0, 0.25, 0.0, 0.0, 0.0, 1.0]
    assert result["points"] == [{"x": 3.5, "y": 3.0, "z": 0.25}]
    assert result["tf_timestamp_ns"] == 120


def test_snapshot_reports_transform_failure_without_claiming_map_coordinates() -> None:
    class FakeRobot:
        def get_joint_states(self, _, __):
            return []

        def get_lidar_data(self, _):
            return _cloud()

        def get_transform(self, *args, **kwargs):
            return ([], 0)

    class SensorType:
        BASE_LIDAR = object()

    class Platform:
        robot_instance = FakeRobot()
        sdk_module = type("Sdk", (), {"SensorType": SensorType})

    result = PythonRobotAdapter(Platform()).snapshot(
        include_lidar=True,
        lidar_options={
            "center": {"x": 0.0, "y": 0.0, "z": 0.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0},
        },
    )["lidar"]

    assert result is not None
    assert result["frame_id"] == "lidar_link"
    assert result["transform_applied"] is False
    assert "没有" in result["transform_error"]


def test_map_cloud_is_not_transformed_twice() -> None:
    class RobotThatMustNotQueryTf:
        def get_transform(self, *args, **kwargs):
            raise AssertionError("map cloud must not query TF")

    pose, error, timestamp = PythonRobotAdapter._cloud_pose_in_map(
        RobotThatMustNotQueryTf(),
        _cloud(frame_id="/map"),
        None,
    )

    np.testing.assert_array_equal(pose, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    assert error is None
    assert timestamp is None
