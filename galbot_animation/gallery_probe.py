#!/usr/bin/env python3
from __future__ import annotations

import cv2

from face_gallery import FaceGallery, SFaceEmbedder
from face_track_head import DEFAULT_MODEL, detect_faces, make_detector

MODEL = "/home/galbot/galbot_animation/models/face_recognition_sface_2021dec.onnx"
GALLERY = "/tmp/recognition_test"
IMAGES = [
    "/tmp/vision_latest2.jpg",
    "/home/galbot/galbot_animation/out/vision/smoke_raw.jpg",
]

detector = make_detector(DEFAULT_MODEL, 0.55)
embedder = SFaceEmbedder(MODEL)
gallery = FaceGallery(GALLERY).load()
print("gallery", gallery.count, gallery.identities)

for path in IMAGES:
    image = cv2.imread(path)
    if image is None:
        print("unreadable", path)
        continue
    faces = detect_faces(detector, image, 0.55)
    if not faces:
        print("no faces", path)
        continue
    face = max(faces, key=lambda item: item.area)
    embedding = embedder.embedding(image, face.raw)
    label, score = gallery.identify(embedding, 0.40)
    print("image", path, "face", round(face.score, 3), "area", round(face.area), "label", label, "cosine", round(score, 4))
