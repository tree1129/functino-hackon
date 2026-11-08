#!/usr/bin/env python3
"""Label every detected face in an image with the local face gallery."""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2

from face_gallery import DEFAULT_GALLERY_DIR, DEFAULT_SFACE_MODEL, FaceGallery, SFaceEmbedder
from face_track_head import DEFAULT_MODEL, detect_faces, make_detector


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument("--gallery-dir", default=DEFAULT_GALLERY_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--recognition-model", default=DEFAULT_SFACE_MODEL)
    parser.add_argument("--score", type=float, default=0.55)
    parser.add_argument("--threshold", type=float, default=0.40)
    args = parser.parse_args()

    image = cv2.imread(args.image)
    if image is None:
        print(f"cannot read {args.image}")
        return 2
    detector = make_detector(args.model, args.score)
    embedder = SFaceEmbedder(args.recognition_model)
    gallery = FaceGallery(args.gallery_dir).load()
    faces = detect_faces(detector, image, args.score)
    print(f"faces={len(faces)} gallery={gallery.count} identities={gallery.identities}")
    for index, face in enumerate(faces):
        embedding = embedder.embedding(image, face.raw)
        label, score = gallery.identify(embedding, args.threshold)
        text = f"{label or 'UNKNOWN'} {score:.2f}"
        color = (0, 255, 0) if label else (0, 200, 255)
        x, y, w, h = (int(round(v)) for v in (face.x, face.y, face.w, face.h))
        cv2.rectangle(image, (x, y), (x + w, y + h), color, 2)
        cv2.putText(image, text, (x, max(22, y - 9)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        print(f"face {index}: {text} box={x},{y},{w},{h}")

    output = Path(args.output) if args.output else Path(args.image).with_name(Path(args.image).stem + "_labeled.jpg")
    cv2.imwrite(str(output), image)
    print(f"saved {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
