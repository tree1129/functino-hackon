"""Vectorized rigid-transform helpers using NumPy arrays."""
from typing import Any

import numpy as np

POSE_SIZE = 7


def pose_array(pose: Any) -> np.ndarray | None:
    """Return a normalized [x, y, z, qx, qy, qz, qw] pose, or None if invalid."""
    if isinstance(pose, dict):
        values = [pose.get(key) for key in ("x", "y", "z", "qx", "qy", "qz", "qw")]
    else:
        try:
            values = list(pose)
        except TypeError:
            return None
    if len(values) != POSE_SIZE:
        return None
    try:
        result = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(result).all():
        return None
    norm = np.linalg.norm(result[3:])
    if norm <= np.finfo(np.float64).eps:
        return None
    result[3:] /= norm
    return result


def rotate_points(points: np.ndarray, quaternion: np.ndarray) -> np.ndarray:
    """Rotate an Nx3 point array by a normalized xyzw quaternion."""
    q_xyz = np.asarray(quaternion[:3], dtype=np.float64)
    q_w = float(quaternion[3])
    points = np.asarray(points, dtype=np.float64)
    twice_cross = 2.0 * np.cross(q_xyz, points)
    return points + q_w * twice_cross + np.cross(q_xyz, twice_cross)


def transform_points(points: np.ndarray, pose: np.ndarray) -> np.ndarray:
    """Apply an xyzw pose to all Nx3 points."""
    return rotate_points(points, pose[3:]) + pose[:3]


def compose_poses(outer: np.ndarray, inner: np.ndarray) -> np.ndarray:
    """Compose outer_T_inner as [translation, quaternion] using xyzw convention."""
    ax, ay, az, aw = outer[3:]
    bx, by, bz, bw = inner[3:]
    quaternion = np.asarray([
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ], dtype=np.float64)
    quaternion /= np.linalg.norm(quaternion)
    return np.concatenate((transform_points(inner[:3][None, :], outer)[0], quaternion))
