#!/usr/bin/env python3
"""Enroll one face into the G1 local face gallery.

Run with the head camera while the person stands in front:
    run_enroll_face.sh --name User --samples 12

The script never commands head motors. It only captures the current camera.
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import cv2
import numpy as np

from face_gallery import DEFAULT_GALLERY_DIR, DEFAULT_SFACE_MODEL, FaceGallery, SFaceEmbedder
from face_track_head import DEFAULT_MODEL, decode_frame, detect_faces, make_detector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enroll one face into the G1 gallery")
    parser.add_argument("--name", required=True, help="identity label, e.g. User or 张三")
    parser.add_argument("--camera", choices=("head_left", "head_right"), default="head_left")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--recognition-model", default=DEFAULT_SFACE_MODEL)
    parser.add_argument("--gallery-dir", default=DEFAULT_GALLERY_DIR)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--min-samples", type=int, default=5)
    parser.add_argument("--duration", type=float, default=25.0)
    parser.add_argument("--sample-interval", type=float, default=0.35)
    parser.add_argument("--score", type=float, default=0.70)
    parser.add_argument("--min-area-ratio", type=float, default=0.008)
    parser.add_argument("--min-blur", type=float, default=20.0)
    parser.add_argument("--append", action="store_true", help="append to an existing identity instead of replacing it")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def safe_name(name: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", name.strip())
    return value.strip("._") or "person"


def main() -> int:
    args = parse_args()
    name = args.name.strip()
    if not name:
        print("empty --name")
        return 2
    folder_name = safe_name(name)
    gallery_dir = Path(args.gallery_dir)
    image_dir = gallery_dir / "images" / folder_name
    image_dir.mkdir(parents=True, exist_ok=True)

    detector = make_detector(args.model, args.score)
    embedder = SFaceEmbedder(args.recognition_model)
    gallery = FaceGallery(args.gallery_dir).load()
    print(f"enroll name={name!r} folder={folder_name} gallery_before={gallery.count} identities={gallery.identities}")

    from galbot_sdk.g1 import GalbotRobot, RgbOutputFormat, SensorType

    sensor = SensorType.HEAD_LEFT_CAMERA if args.camera == "head_left" else SensorType.HEAD_RIGHT_CAMERA
    robot = GalbotRobot()
    if not robot.init({sensor}):
        print("GalbotRobot.init() failed")
        return 3

    embeddings: list[np.ndarray] = []
    saved_paths: list[str] = []
    start = time.monotonic()
    last_sample = 0.0
    last_embedding: np.ndarray | None = None
    frames = 0
    try:
        while time.monotonic() - start < args.duration and len(embeddings) < args.samples:
            image = decode_frame(robot, sensor, RgbOutputFormat.JPEG)
            if image is None:
                time.sleep(0.08)
                continue
            frames += 1
            height, width = image.shape[:2]
            faces = detect_faces(detector, image, args.score)
            min_area = args.min_area_ratio * width * height
            candidates = []
            for face in faces:
                if face.area < min_area:
                    continue
                dx = abs(face.cx - width / 2.0) / max(1.0, width / 2.0)
                dy = abs(face.cy - height / 2.0) / max(1.0, height / 2.0)
                if dx > 0.72 or dy > 0.80:
                    continue
                x, y, w, h = (int(round(v)) for v in (face.x, face.y, face.w, face.h))
                crop = image[max(0, y):min(height, y + h), max(0, x):min(width, x + w)]
                if crop.size == 0:
                    continue
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                if blur < args.min_blur:
                    continue
                candidates.append((face, blur))
            if not candidates:
                time.sleep(0.08)
                continue

            face, blur = max(candidates, key=lambda item: item[0].area)
            now = time.monotonic()
            if now - last_sample < args.sample_interval:
                time.sleep(0.03)
                continue
            last_sample = now
            embedding = embedder.embedding(image, face.raw)
            if last_embedding is not None:
                cosine = embedder.match(last_embedding, embedding)
                if cosine > 0.998:
                    if not args.quiet:
                        print(f"skip duplicate cosine={cosine:.4f}")
                    continue
            last_embedding = embedding
            embeddings.append(embedding)

            index = len(embeddings)
            aligned = embedder.align(image, face.raw)
            aligned_path = image_dir / f"{int(time.time())}_{index:02d}.jpg"
            cv2.imwrite(str(aligned_path), aligned)
            saved_paths.append(str(aligned_path))

            preview = image.copy()
            x, y, w, h = (int(round(v)) for v in (face.x, face.y, face.w, face.h))
            cv2.rectangle(preview, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(preview, f"{name} sample {index}/{args.samples} blur={blur:.1f}", (x, max(24, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.imwrite(str(image_dir / "last_enroll.jpg"), preview)
            if not args.quiet:
                print(f"sample {index}/{args.samples} score={face.score:.3f} area={face.area:.0f} blur={blur:.1f}")

        if len(embeddings) < args.min_samples:
            print(f"FAILED only {len(embeddings)} samples (need {args.min_samples}); gallery not changed")
            return 4

        values = np.stack(embeddings).astype(np.float32)
        gallery.add(name, values, replace=not args.append)
        gallery.save()
        print(f"ENROLLED name={name!r} samples={len(embeddings)} gallery={gallery.count} identities={gallery.identities}")
        print(f"images={image_dir}")
        return 0
    finally:
        robot.request_shutdown()
        robot.wait_for_shutdown()
        robot.destroy()
        print(f"released frames={frames} samples={len(embeddings)}")


if __name__ == "__main__":
    raise SystemExit(main())
