"""NumPy-based PointCloud2 decoding and pre-transmission filtering."""
from typing import Any

import numpy as np

from .transforms import transform_points

_FLOAT_DTYPES = {7: "f4", 8: "f8"}


def decode_xyz(cloud: dict[str, Any]) -> tuple[np.ndarray, int] | None:
    """Decode PointCloud2 XYZ fields into an Nx3 float64 array without per-point unpacking."""
    fields = {field["name"]: field for field in cloud.get("fields", [])}
    if not all(name in fields for name in ("x", "y", "z")):
        return None
    point_step = int(cloud.get("point_step", 0))
    total = int(cloud.get("width", 0)) * int(cloud.get("height", 0))
    data = cloud.get("data", b"")
    if point_step <= 0 or total <= 0 or len(data) < total * point_step:
        return None
    byte_order = ">" if cloud.get("is_bigendian") else "<"
    columns = []
    for name in ("x", "y", "z"):
        field = fields[name]
        datatype = _FLOAT_DTYPES.get(field.get("datatype", 7))
        if datatype is None:
            return None
        dtype = np.dtype(byte_order + datatype)
        offset = int(field["offset"])
        if offset < 0 or offset + dtype.itemsize > point_step:
            return None
        columns.append(np.ndarray(total, dtype=dtype, buffer=data, offset=offset, strides=(point_step,)))
    points = np.column_stack(columns).astype(np.float64, copy=False)
    valid = np.isfinite(points).all(axis=1) & (np.abs(points) < 1e6).all(axis=1)
    return points[valid], total


def transform_crop_and_sample(
    points: np.ndarray,
    *,
    transform: np.ndarray | None,
    crop: dict[str, float] | None,
    center: dict[str, float] | None,
    max_points: int,
) -> tuple[np.ndarray, int]:
    """Batch-transform, crop, then randomly sample the point cloud for transmission."""
    if transform is not None:
        points = transform_points(points, transform)
    if crop:
        crop_center = np.asarray([
            float((center or {}).get("x", 0.0)),
            float((center or {}).get("y", 0.0)),
            float((center or {}).get("z", 0.0)),
        ])
        half_extent = np.asarray([float(crop["half_x"]), float(crop["half_y"]), float(crop["half_z"])])
        points = points[(np.abs(points - crop_center) <= half_extent).all(axis=1)]
    selected_count = len(points)
    limit = min(15000, max(1000, int(max_points)))
    if selected_count > limit:
        points = points[np.random.choice(selected_count, size=limit, replace=False)]
    return points, selected_count
