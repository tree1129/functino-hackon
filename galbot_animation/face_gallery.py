#!/usr/bin/env python3
"""SFace embeddings and a small on-disk face gallery for G1."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

DEFAULT_SFACE_MODEL = "/home/galbot/galbot_animation/models/face_recognition_sface_2021dec.onnx"
DEFAULT_GALLERY_DIR = "/home/galbot/galbot_animation/faces"


class SFaceEmbedder:
    """Thin wrapper around OpenCV FaceRecognizerSF."""

    def __init__(self, model_path: str = DEFAULT_SFACE_MODEL) -> None:
        if not Path(model_path).is_file():
            raise FileNotFoundError(f"SFace model missing: {model_path}")
        self.model_path = model_path
        self.recognizer = cv2.FaceRecognizerSF.create(model_path, "")

    def align(self, image: np.ndarray, face_row: np.ndarray) -> np.ndarray:
        return self.recognizer.alignCrop(image, face_row)

    def embedding(self, image: np.ndarray, face_row: np.ndarray) -> np.ndarray:
        aligned = self.align(image, face_row)
        feature = self.recognizer.feature(aligned)
        return np.asarray(feature, dtype=np.float32).reshape(-1)

    def embedding_from_aligned(self, aligned: np.ndarray) -> np.ndarray:
        feature = self.recognizer.feature(aligned)
        return np.asarray(feature, dtype=np.float32).reshape(-1)

    def match(self, left: np.ndarray, right: np.ndarray) -> float:
        score = self.recognizer.match(
            left.reshape(1, -1),
            right.reshape(1, -1),
            cv2.FaceRecognizerSF_FR_COSINE,
        )
        return float(score)


class FaceGallery:
    """Multiple embeddings per identity, matched by maximum cosine similarity."""

    def __init__(self, root: str = DEFAULT_GALLERY_DIR) -> None:
        self.root = Path(root)
        self.embeddings = np.empty((0, 128), dtype=np.float32)
        self.labels: list[str] = []

    @property
    def count(self) -> int:
        return len(self.labels)

    @property
    def identities(self) -> list[str]:
        return sorted(set(self.labels))

    def load(self) -> "FaceGallery":
        embedding_path = self.root / "embeddings.npy"
        labels_path = self.root / "labels.json"
        if embedding_path.is_file() and labels_path.is_file():
            embeddings = np.load(embedding_path)
            labels = json.loads(labels_path.read_text(encoding="utf-8"))
            if embeddings.ndim != 2 or embeddings.shape[1] != 128:
                raise ValueError(f"bad gallery embedding shape: {embeddings.shape}")
            if len(labels) != embeddings.shape[0]:
                raise ValueError(f"gallery labels/embeddings mismatch: {len(labels)} vs {embeddings.shape[0]}")
            self.embeddings = embeddings.astype(np.float32)
            self.labels = [str(label) for label in labels]
        return self

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        np.save(self.root / "embeddings.npy", self.embeddings.astype(np.float32))
        (self.root / "labels.json").write_text(
            json.dumps(self.labels, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        meta = {
            "count": self.count,
            "identities": self.identities,
            "model": DEFAULT_SFACE_MODEL,
        }
        (self.root / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def add(self, label: str, embeddings: np.ndarray, replace: bool = True) -> None:
        label = str(label).strip()
        if not label:
            raise ValueError("empty identity label")
        values = np.asarray(embeddings, dtype=np.float32)
        if values.ndim == 1:
            values = values.reshape(1, -1)
        if values.ndim != 2 or values.shape[1] != 128:
            raise ValueError(f"bad embedding shape: {values.shape}")
        if replace and self.labels:
            keep = np.asarray([item != label for item in self.labels], dtype=bool)
            self.embeddings = self.embeddings[keep]
            self.labels = [item for item in self.labels if item != label]
        if self.embeddings.size:
            self.embeddings = np.vstack([self.embeddings, values])
        else:
            self.embeddings = values
        self.labels.extend([label] * values.shape[0])

    def identify(self, embedding: np.ndarray, threshold: float = 0.40) -> tuple[str | None, float]:
        if not self.labels or self.embeddings.size == 0:
            return None, 0.0
        query = np.asarray(embedding, dtype=np.float32).reshape(-1)
        qnorm = float(np.linalg.norm(query))
        if qnorm <= 0:
            return None, 0.0
        gallery = self.embeddings
        norms = np.linalg.norm(gallery, axis=1)
        valid = norms > 0
        if not np.any(valid):
            return None, 0.0
        scores = np.full(len(self.labels), -1.0, dtype=np.float32)
        scores[valid] = (gallery[valid] @ query) / (norms[valid] * qnorm)
        index = int(np.argmax(scores))
        score = float(scores[index])
        if score < threshold:
            return None, score
        return self.labels[index], score
