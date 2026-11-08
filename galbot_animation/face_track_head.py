#!/usr/bin/env python3
"""Head-only visual face tracking for Galbot G1.

The tracker reads the head camera through the official Galbot SDK, runs YuNet
(OpenCV FaceDetectorYN), locks onto one face, and commands only the two head
joints. Arms, grippers, legs and base are never touched.

Default is dry-run:
    export LD_LIBRARY_PATH=/data/galbot/lib:$LD_LIBRARY_PATH
    export PYTHONPATH=/data/galbot/lib:$PYTHONPATH
    cd /home/galbot/galbot-sdk-web-console
    .venv/bin/python /home/galbot/galbot_animation/face_track_head.py --duration 20

Actually move the head:
    .venv/bin/python .../face_track_head.py --execute --duration 30

The YuNet model is expected at:
    /home/galbot/galbot_animation/models/face_detection_yunet_2023mar.onnx
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from face_gallery import DEFAULT_GALLERY_DIR, DEFAULT_SFACE_MODEL, FaceGallery, SFaceEmbedder

DEFAULT_MODEL = "/home/galbot/galbot_animation/models/face_detection_yunet_2023mar.onnx"
DEFAULT_SAVE_DIR = "/home/galbot/galbot_animation/out/vision"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="G1 head-only face tracking")
    parser.add_argument("--camera", choices=("head_left", "head_right"), default="head_left")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--fps", type=float, default=8.0)
    parser.add_argument("--duration", type=float, default=30.0, help="0 = run until Ctrl-C")
    parser.add_argument("--execute", action="store_true", help="actually command head joints")
    parser.add_argument("--target", choices=("center", "largest"), default="largest", help="initial face selection")
    parser.add_argument("--score", type=float, default=0.65, help="YuNet confidence threshold")
    parser.add_argument("--min-area-ratio", type=float, default=0.004, help="ignore faces smaller than this fraction of the image")
    parser.add_argument("--deadband", type=float, default=0.06, help="normalized center deadband")
    parser.add_argument("--gain-yaw", type=float, default=0.32)
    parser.add_argument("--gain-pitch", type=float, default=0.22)
    parser.add_argument("--max-step-yaw", type=float, default=0.06)
    parser.add_argument("--max-step-pitch", type=float, default=0.035)
    parser.add_argument("--max-yaw", type=float, default=0.80)
    parser.add_argument("--max-pitch-low", type=float, default=-0.21)
    parser.add_argument("--max-pitch-high", type=float, default=0.45)
    parser.add_argument("--yaw-sign", type=float, default=1.0, help="+1 if positive joint yaw looks left")
    parser.add_argument("--pitch-sign", type=float, default=-1.0, help="-1 if positive joint pitch looks down")
    parser.add_argument("--lost-hold", type=float, default=1.0, help="seconds to hold after losing face")
    parser.add_argument("--recenter-after", type=float, default=5.0, help="seconds before easing head to neutral")
    parser.add_argument("--recenter-step", type=float, default=0.025)
    parser.add_argument("--no-recenter", action="store_true")
    parser.add_argument("--save-dir", default=DEFAULT_SAVE_DIR)
    parser.add_argument("--save-every", type=float, default=1.0, help="seconds between snapshots; 0 disables")
    parser.add_argument("--max-snapshots", type=int, default=200)
    parser.add_argument("--jsonl", default="", help="optional JSONL log path")
    parser.add_argument("--gallery-dir", default=DEFAULT_GALLERY_DIR)
    parser.add_argument("--recognition-model", default=DEFAULT_SFACE_MODEL)
    parser.add_argument("--recognition-threshold", type=float, default=0.40)
    parser.add_argument("--no-recognition", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


@dataclass
class Face:
    x: float
    y: float
    w: float
    h: float
    score: float
    raw: np.ndarray | None = None

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def as_dict(self) -> dict[str, float]:
        return {
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "w": round(self.w, 2),
            "h": round(self.h, 2),
            "cx": round(self.cx, 2),
            "cy": round(self.cy, 2),
            "score": round(self.score, 3),
        }


class FaceLock:
    """Keeps one face identity across frames instead of jumping to the largest face."""

    def __init__(self, mode: str, reacquire_after: int = 12) -> None:
        self.mode = mode
        self.reacquire_after = max(1, reacquire_after)
        self.target: Face | None = None
        self.misses = 0

    def _initial(self, faces: list[Face], width: int, height: int) -> Face:
        if self.mode == "largest":
            return max(faces, key=lambda f: (f.area, f.score))
        # Prefer a face near the image center; break ties with area/score.
        return min(
            faces,
            key=lambda f: (
                abs(f.cx - width / 2.0) / max(1.0, width / 2.0)
                + 0.45 * abs(f.cy - height / 2.0) / max(1.0, height / 2.0)
                - 0.08 * (f.area / max(1.0, width * height)),
                -f.score,
            ),
        )

    @staticmethod
    def _iou(a: Face, b: Face) -> float:
        x1 = max(a.x, b.x)
        y1 = max(a.y, b.y)
        x2 = min(a.x + a.w, b.x + b.w)
        y2 = min(a.y + a.h, b.y + b.h)
        inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        union = a.area + b.area - inter
        return inter / union if union > 0 else 0.0

    def select(self, faces: list[Face], width: int, height: int) -> Face | None:
        if not faces:
            self.misses += 1
            if self.misses >= self.reacquire_after:
                self.target = None
            return None

        if self.target is None:
            self.target = self._initial(faces, width, height)
            self.misses = 0
            return self.target

        best: Face | None = None
        best_score = -1.0
        for face in faces:
            dx = (face.cx - self.target.cx) / max(1.0, width)
            dy = (face.cy - self.target.cy) / max(1.0, height)
            distance = math.hypot(dx, dy)
            size_ratio = face.area / max(1.0, self.target.area)
            if distance > 0.42 or size_ratio < 0.35 or size_ratio > 3.0:
                continue
            score = self._iou(face, self.target) + 0.35 * face.score - 0.45 * distance
            if score > best_score:
                best_score = score
                best = face

        if best is not None:
            self.target = best
            self.misses = 0
            return best

        self.misses += 1
        if self.misses >= self.reacquire_after:
            self.target = self._initial(faces, width, height)
            self.misses = 0
            return self.target
        return None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def as_float(value: Any) -> float:
    if isinstance(value, (int, float, np.floating)):
        return float(value)
    for attr in ("position", "value", "q"):
        if hasattr(value, attr):
            return float(getattr(value, attr))
    raise TypeError(f"cannot convert joint value: {value!r}")


def get_head_positions(robot: Any) -> tuple[float, float]:
    raw = list(robot.get_joint_positions(joint_groups=["head"], joint_names=[]) or [])
    if len(raw) != 2:
        raise RuntimeError(f"unexpected head joint count: {len(raw)}")
    return as_float(raw[0]), as_float(raw[1])


def make_detector(model_path: str, score: float) -> cv2.FaceDetectorYN:
    if not Path(model_path).is_file():
        raise FileNotFoundError(
            f"YuNet model missing: {model_path}\n"
            "Download it on G1 with:\n"
            "  curl -L -o /home/galbot/galbot_animation/models/face_detection_yunet_2023mar.onnx "
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
        )
    return cv2.FaceDetectorYN.create(model_path, "", (640, 480), score, 0.3, 5000)


def decode_frame(robot: Any, sensor: Any, output_format: Any) -> np.ndarray | None:
    frame = robot.get_rgb_data(sensor, output_format, True)
    if not isinstance(frame, dict):
        return None
    data = frame.get("data")
    if not data:
        return None
    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


def detect_faces(detector: cv2.FaceDetectorYN, image: np.ndarray, score_threshold: float) -> list[Face]:
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    ok, raw = detector.detect(image)
    if not ok or raw is None:
        return []
    faces: list[Face] = []
    for item in raw:
        score = float(item[-1])
        if score < score_threshold:
            continue
        x, y, w, h = (float(item[i]) for i in range(4))
        if w <= 0 or h <= 0:
            continue
        faces.append(Face(x, y, w, h, score, np.asarray(item, dtype=np.float32).copy()))
    return faces


def annotate(
    image: np.ndarray,
    face: Face | None,
    head: tuple[float, float],
    cmd: tuple[float, float],
    visible: bool,
    label_text: str | None = None,
    similarity: float = 0.0,
) -> np.ndarray:
    height, width = image.shape[:2]
    cx, cy = width // 2, height // 2
    cv2.drawMarker(image, (cx, cy), (255, 255, 255), cv2.MARKER_CROSS, 26, 2)
    cv2.rectangle(image, (cx - int(width * 0.06), cy - int(height * 0.06)), (cx + int(width * 0.06), cy + int(height * 0.06)), (255, 255, 255), 1)
    if face is not None:
        x, y, w, h = (int(round(v)) for v in (face.x, face.y, face.w, face.h))
        known = label_text is not None and label_text != "UNKNOWN"
        box_color = (0, 255, 0) if known else (0, 200, 255)
        cv2.rectangle(image, (x, y), (x + w, y + h), box_color, 2)
        if label_text:
            text = f"{label_text} {similarity:.2f}"
            cv2.putText(image, text, (x, max(20, y - 9)), cv2.FONT_HERSHEY_SIMPLEX, 0.72, box_color, 2)
        else:
            cv2.putText(image, f"{face.score:.2f}", (x, max(16, y - 7)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.circle(image, (int(round(face.cx)), int(round(face.cy))), 5, (0, 255, 0), -1)
    color = (0, 255, 0) if visible else (0, 165, 255)
    cv2.putText(
        image,
        f"head=({head[0]:+.2f},{head[1]:+.2f}) cmd=({cmd[0]:+.2f},{cmd[1]:+.2f}) {'FACE' if visible else 'LOST'}",
        (18, 34),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        color,
        2,
    )
    return image


def main() -> int:
    args = parse_args()
    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    detector = make_detector(args.model, args.score)
    print(f"YuNet ready model={args.model} target={args.target} execute={args.execute}")

    embedder: SFaceEmbedder | None = None
    gallery: FaceGallery | None = None
    recognition_enabled = False
    if not args.no_recognition:
        try:
            gallery = FaceGallery(args.gallery_dir).load()
            if gallery.count:
                embedder = SFaceEmbedder(args.recognition_model)
                recognition_enabled = True
                print(f"recognition ready gallery={args.gallery_dir} identities={gallery.identities} samples={gallery.count}")
            else:
                print(f"recognition disabled: empty gallery {args.gallery_dir}")
        except Exception as error:
            gallery = None
            embedder = None
            print(f"recognition disabled: {error}")

    from galbot_sdk.g1 import ControlStatus, G1ControllerName, GalbotRobot, JointCommand, RgbOutputFormat, SensorType

    sensor = SensorType.HEAD_LEFT_CAMERA if args.camera == "head_left" else SensorType.HEAD_RIGHT_CAMERA
    robot = GalbotRobot()
    if not robot.init({sensor}):
        print("GalbotRobot.init() failed")
        return 2

    log_handle = open(args.jsonl, "a", encoding="utf-8") if args.jsonl else None
    selector = FaceLock(args.target)
    snapshots = 0
    frames = 0
    face_frames = 0
    last_face_time = 0.0
    last_snapshot = 0.0
    start = time.monotonic()
    yaw = pitch = 0.0
    try:
        status = robot.switch_controller(G1ControllerName.HEAD_PVT_CTRL)
        if status != ControlStatus.SUCCESS:
            print(f"switch_controller(HEAD_PVT_CTRL) failed: {status}")
            return 3

        yaw, pitch = get_head_positions(robot)
        print(f"head_start=({yaw:+.4f},{pitch:+.4f})")
        interval = 1.0 / max(1.0, args.fps)
        next_tick = time.monotonic()

        while args.duration <= 0 or time.monotonic() - start < args.duration:
            loop_start = time.monotonic()
            image = decode_frame(robot, sensor, RgbOutputFormat.JPEG)
            if image is None:
                time.sleep(min(interval, 0.1))
                continue

            height, width = image.shape[:2]
            faces = detect_faces(detector, image, args.score)
            min_area = args.min_area_ratio * width * height
            faces = [candidate for candidate in faces if candidate.area >= min_area]
            face = selector.select(faces, width, height)
            now = time.monotonic()
            visible = face is not None
            if visible:
                face_frames += 1
                last_face_time = now

            identity: str | None = None
            similarity = 0.0
            label_text: str | None = None
            if recognition_enabled and face is not None and embedder is not None and gallery is not None and face.raw is not None:
                try:
                    embedding = embedder.embedding(image, face.raw)
                    identity, similarity = gallery.identify(embedding, args.recognition_threshold)
                    label_text = identity or "UNKNOWN"
                except Exception as error:
                    recognition_enabled = False
                    print(f"recognition disabled after error: {error}")

            current_yaw, current_pitch = get_head_positions(robot)
            target_yaw, target_pitch = current_yaw, current_pitch
            step_yaw_limit = args.max_step_yaw
            step_pitch_limit = args.max_step_pitch

            if face is not None:
                dx = (face.cx - width / 2.0) / max(1.0, width / 2.0)
                dy = (face.cy - height / 2.0) / max(1.0, height / 2.0)
                if abs(dx) > args.deadband:
                    target_yaw = current_yaw - dx * args.yaw_sign * args.gain_yaw
                if abs(dy) > args.deadband:
                    target_pitch = current_pitch - dy * args.pitch_sign * args.gain_pitch
            else:
                lost_for = now - last_face_time if last_face_time else 0.0
                if not args.no_recenter and last_face_time and lost_for > args.lost_hold:
                    if lost_for <= args.lost_hold + args.recenter_after:
                        target_yaw = 0.0
                        target_pitch = 0.0
                        step_yaw_limit = args.recenter_step
                        step_pitch_limit = args.recenter_step * 0.6
                    # After the recenter window, hold the current pose.

            target_yaw = clamp(target_yaw, -args.max_yaw, args.max_yaw)
            target_pitch = clamp(target_pitch, args.max_pitch_low, args.max_pitch_high)
            cmd_yaw = current_yaw + clamp(target_yaw - current_yaw, -step_yaw_limit, step_yaw_limit)
            cmd_pitch = current_pitch + clamp(target_pitch - current_pitch, -step_pitch_limit, step_pitch_limit)
            cmd_yaw = clamp(cmd_yaw, -args.max_yaw, args.max_yaw)
            cmd_pitch = clamp(cmd_pitch, args.max_pitch_low, args.max_pitch_high)

            if args.execute and (abs(cmd_yaw - current_yaw) > 0.001 or abs(cmd_pitch - current_pitch) > 0.001):
                commands = []
                for value in (cmd_yaw, cmd_pitch):
                    command = JointCommand()
                    command.position = float(value)
                    commands.append(command)
                result = robot.set_joint_commands(commands, ["head"], [], 0.0)
                if result != ControlStatus.SUCCESS:
                    print(f"set_joint_commands failed: {result}")
                    break
                yaw, pitch = cmd_yaw, cmd_pitch
            else:
                yaw, pitch = current_yaw, current_pitch

            record = {
                "t": round(now - start, 3),
                "face_visible": visible,
                "face": face.as_dict() if face else None,
                "faces_seen": len(faces),
                "head": [round(current_yaw, 4), round(current_pitch, 4)],
                "cmd": [round(cmd_yaw, 4), round(cmd_pitch, 4)],
                "identity": identity,
                "similarity": round(similarity, 3),
                "recognition": recognition_enabled,
                "execute": args.execute,
            }
            if log_handle:
                log_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                log_handle.flush()
            if not args.quiet:
                print(json.dumps(record, ensure_ascii=False), flush=True)

            if args.save_every > 0 and now - last_snapshot >= args.save_every and snapshots < args.max_snapshots:
                snapshots += 1
                last_snapshot = now
                annotated = annotate(
                    image.copy(),
                    face,
                    (current_yaw, current_pitch),
                    (cmd_yaw, cmd_pitch),
                    visible,
                    label_text=label_text,
                    similarity=similarity,
                )
                cv2.imwrite(str(save_dir / f"track_{snapshots:04d}.jpg"), annotated)
                cv2.imwrite(str(save_dir / "latest.jpg"), annotated)

            frames += 1
            next_tick += interval
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
            else:
                next_tick = time.monotonic()

        print(f"done frames={frames} face_frames={face_frames} final_head=({yaw:+.4f},{pitch:+.4f}) execute={args.execute}")
        return 0
    except KeyboardInterrupt:
        print(f"interrupted frames={frames} face_frames={face_frames}")
        return 130
    finally:
        if log_handle:
            log_handle.close()
        robot.request_shutdown()
        robot.wait_for_shutdown()
        robot.destroy()
        print("SDK resources released")


if __name__ == "__main__":
    raise SystemExit(main())
