"""Generate a deterministic Rerun recording for browser playback checks."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import rerun as rr

from galbot_sdk_web_console.geometry.rerun_robot import (
    RECORDING_FORMAT_NAME,
    RECORDING_FORMAT_VERSION,
    RERUN_APPLICATION_ID,
    load_rerun_urdf_tree,
    recording_blueprint,
    robot_model_recording_id,
)


JOINT_GROUPS = {
    "leg": [f"leg_joint{index}" for index in range(1, 6)],
    "head": [f"head_joint{index}" for index in range(1, 3)],
    "left_arm": [f"left_arm_joint{index}" for index in range(1, 8)],
    "right_arm": [f"right_arm_joint{index}" for index in range(1, 8)],
}


def generate(path: Path, *, force: bool = False) -> Path:
    path = path.resolve()
    if path.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing recording: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)

    stream = rr.RecordingStream(RERUN_APPLICATION_ID, recording_id=robot_model_recording_id("g1"))
    stream.save(path)
    stream.send_blueprint(recording_blueprint())
    robot_tree, robot_model = load_rerun_urdf_tree("g1", representation="collision")
    stream.log("/metadata/recording_format", rr.TextDocument(json.dumps({
        "name": RECORDING_FORMAT_NAME,
        "version": RECORDING_FORMAT_VERSION,
        "application_id": RERUN_APPLICATION_ID,
        "timeline": "capture_time",
        "world_frame": "map",
        "robot_model_store_id": str(robot_model_recording_id("g1")),
    }, ensure_ascii=False), media_type="application/json"), static=True)
    stream.log("/metadata/session", rr.TextDocument(json.dumps({
        "model": "g1", "kind": "synthetic_demo", "joint_count": 21,
        "robot_model": robot_model,
        "time_quality": "generated", "warning": "This file contains synthetic data and never controls a robot.",
    }, ensure_ascii=False), media_type="application/json"), static=True)
    stream.log("/metadata/robot_model", rr.TextDocument(json.dumps(robot_model, ensure_ascii=False), media_type="application/json"), static=True)
    stream.log("/metadata/joints", rr.TextDocument(json.dumps({"field": "position", "unit": "rad", "count": 21}, ensure_ascii=False), media_type="application/json"), static=True)
    stream.log("/world", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)
    stream.log("/world", rr.CoordinateFrame("map"), static=True)
    stream.log("/world/robot/model/root", rr.Transform3D(parent_frame="base_link", child_frame=robot_model["root_frame"]), static=True)

    grid = np.asarray([[x, y, 0.0] for x in np.linspace(-5, 5, 41) for y in np.linspace(-5, 5, 41)], dtype=np.float32)
    for entity_path in ("/world/map", "/world/navigation/path", "/world/obstacles/box", "/world/obstacles/sphere", "/world/lidar/base"):
        stream.log(entity_path, rr.CoordinateFrame("map"), static=True)
    stream.log("/world/map", rr.Points3D(grid, colors=[70, 82, 98], radii=0.012), static=True)
    stream.log("/world/navigation/path", rr.LineStrips3D([[[-3, -2, 0.03], [-1, -1, 0.03], [1, 0.5, 0.03], [3, 2, 0.03]]], colors=[0, 155, 255], radii=0.035), static=True)
    stream.log("/world/obstacles/box", rr.Boxes3D(centers=[[1.2, 0.7, 0.45]], sizes=[[0.8, 0.6, 0.9]], colors=[255, 145, 40, 200], labels=["demo_box"]), static=True)
    stream.log("/world/obstacles/sphere", rr.Ellipsoids3D(centers=[[-1.0, 1.4, 0.35]], radii=[0.35], colors=[255, 90, 120, 200], labels=["demo_sphere"]), static=True)
    stream.log("/sensors/cameras/head_fisheye", rr.Pinhole(resolution=[320, 180], focal_length=210), static=True)
    stream.log("/sensors/cameras/left_depth", rr.Pinhole(resolution=[160, 90], focal_length=110), static=True)

    started_ns = time.time_ns()
    frame_count = 60
    for frame in range(frame_count):
        elapsed = frame / 5.0
        timestamp_ns = started_ns + int(elapsed * 1e9)
        stream.set_time("recording_time", duration=elapsed)
        stream.set_time("capture_time", timestamp=np.datetime64(timestamp_ns, "ns"))

        x = -3.0 + frame * 6.0 / (frame_count - 1)
        y = 0.45 * math.sin(frame / 10)
        yaw = 0.18 * math.sin(frame / 14)
        stream.log("/world/robot/base_link", rr.Transform3D(
            translation=[x, y, 0], quaternion=[0, 0, math.sin(yaw / 2), math.cos(yaw / 2)],
            parent_frame="map", child_frame="base_link",
        ))

        joint_index = 0
        for group, joints in JOINT_GROUPS.items():
            for joint in joints:
                angle = 0.35 * math.sin(elapsed * 0.8 + joint_index * 0.27)
                stream.log(f"/robot/joints/{group}/{joint}/position", rr.Scalars([angle]))
                model_joint = robot_tree.get_joint_by_name(joint)
                if model_joint is None:
                    raise RuntimeError(f"G1 URDF is missing demo joint: {joint}")
                stream.log(f"/world/robot/model/joints/{joint}", model_joint.compute_transform(angle, clamp=False))
                joint_index += 1

        angles = np.linspace(0, math.tau, 240, endpoint=False)
        radii = 2.2 + 0.18 * np.sin(angles * 5 + elapsed)
        cloud = np.column_stack((x + radii * np.cos(angles), y + radii * np.sin(angles), 0.12 + 0.08 * np.sin(angles * 3)))
        stream.log("/world/lidar/base", rr.Points3D(cloud, colors=[70, 225, 175], radii=0.018))

        image_x = np.arange(320, dtype=np.uint16)[None, :]
        image_y = np.arange(180, dtype=np.uint16)[:, None]
        rgb = np.empty((180, 320, 3), dtype=np.uint8)
        rgb[..., 0] = (image_x + frame * 4) % 256
        rgb[..., 1] = (image_y * 2 + frame * 3) % 256
        rgb[..., 2] = ((image_x // 2 + image_y + frame * 2) % 256)
        stream.log("/sensors/cameras/head_fisheye/image", rr.Image(rgb, color_model="RGB"))

        depth_x = np.arange(160, dtype=np.uint16)[None, :]
        depth_y = np.arange(90, dtype=np.uint16)[:, None]
        depth = (800 + depth_x * 7 + depth_y * 4 + frame * 5).astype(np.uint16)
        stream.log("/sensors/cameras/left_depth/depth", rr.DepthImage(depth, meter=1000.0))

    stream.flush(timeout_sec=30)
    stream.disconnect()
    metadata = {
        "id": path.stem, "name": path.name, "relative_path": path.name,
        "model": "g1", "kind": "synthetic_demo", "started_ns": started_ns,
        "robot_model": robot_model,
        "finished_ns": started_ns + int((frame_count - 1) / 5 * 1e9),
        "duration_seconds": (frame_count - 1) / 5,
        "config": {"sources": ["joints", "base_pose", "lidar", "static_map", "motion_obstacles", "navigation", "camera.head_fisheye", "camera.left_depth"], "sample_hz": 5, "camera_fps": 5},
        "samples": {"frames": frame_count}, "drops": {}, "errors": {},
    }
    path.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("recordings/demo_recording.rrd"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(generate(args.output, force=args.force))


if __name__ == "__main__":
    main()
