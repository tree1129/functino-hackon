#!/usr/bin/env python3
"""Enroll a face identity from existing image files (no robot access)."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import cv2
import numpy as np

from face_gallery import DEFAULT_GALLERY_DIR, DEFAULT_SFACE_MODEL, FaceGallery, SFaceEmbedder
from face_track_head import DEFAULT_MODEL, detect_faces, make_detector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enroll a face gallery identity from image files")
    parser.add_argument("--name", required=True)
    parser.add_argument("--image", nargs="+", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--recognition-model", default=DEFAULT_SFACE_MODEL)
    parser.add_argument("--gallery-dir", default=DEFAULT_GALLERY_DIR)
    parser.add_argument("--score", type=float, default=0.65)
    parser.add_argument("--min-area-ratio", type=float, default=0.004)
    parser.add_argument("--min-blur", type=float, default=10.0)
    parser.add_argument("--append", action="store_true")
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
    embeddings: list[np.ndarray] = []
    saved: list[str] = []

    for source_index, source in enumerate(args.image):
        image_path = Path(source)
        image = cv2.imread(str(image_path))
        if image is None:
            print(f"skip unreadable image: {source}")
            continue
        height, width = image.shape[:2]
        faces = detect_faces(detector, image, args.score)
        min_area = args.min_area_ratio * width * height
        candidates = []
        for face in faces:
            if face.area < min_area:
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
            print(f"skip no usable face: {source} faces={len(faces)}")
            continue
        face, blur = max(candidates, key=lambda item: item[0].area)
        embedding = embedder.embedding(image, face.raw)
        embeddings.append(embedding)
        aligned = embedder.align(image, face.raw)
        out_path = image_dir / f"{image_path.stem}_{source_index + 1:02d}.jpg"
        cv2.imwrite(str(out_path), aligned)
        saved.append(str(out_path))
        print(f"image={source} face_score={face.score:.3f} area={face.area:.0f} blur={blur:.1f} aligned={out_path}")

    if not embeddings:
        print("FAILED: no usable faces")
        return 3
    gallery.add(name, np.stack(embeddings).astype(np.float32), replace=not args.append)
    gallery.save()
    print(f"ENROLLED name={name!r} samples={len(embeddings)} gallery={gallery.count} identities={gallery.identities}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
