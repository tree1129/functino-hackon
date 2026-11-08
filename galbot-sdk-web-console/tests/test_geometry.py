import struct
from math import sqrt

import numpy as np

from galbot_sdk_web_console.geometry.point_cloud import decode_xyz, transform_crop_and_sample
from galbot_sdk_web_console.geometry.transforms import compose_poses, pose_array, transform_points


def test_decode_xyz_supports_padded_point_steps_without_per_point_unpacking() -> None:
    records = b"".join(struct.pack("<fffI", *point, 42) for point in ((1, 2, 3), (4, 5, 6)))
    cloud = {
        "fields": [
            {"name": "x", "offset": 0, "datatype": 7},
            {"name": "y", "offset": 4, "datatype": 7},
            {"name": "z", "offset": 8, "datatype": 7},
        ],
        "point_step": 16,
        "width": 2,
        "height": 1,
        "data": records,
    }

    decoded = decode_xyz(cloud)

    assert decoded is not None
    points, total = decoded
    assert total == 2
    np.testing.assert_array_equal(points, [[1, 2, 3], [4, 5, 6]])


def test_vectorized_transform_applies_rotation_and_translation_to_a_batch() -> None:
    half_turn_z = sqrt(0.5)
    pose = pose_array([10, 20, 1, 0, 0, half_turn_z, half_turn_z])
    assert pose is not None

    result = transform_points(np.asarray([[1, 0, 0], [0, 2, 0]], dtype=np.float64), pose)

    np.testing.assert_allclose(result, [[10, 21, 1], [8, 20, 1]], atol=1e-12)


def test_pose_composition_is_vectorized_and_normalizes_quaternions() -> None:
    outer = pose_array([2, 3, 0, 0, 0, 0, 2])
    inner = pose_array([0.5, 0, 0.25, 0, 0, 0, 3])
    assert outer is not None and inner is not None

    result = compose_poses(outer, inner)

    np.testing.assert_allclose(result, [2.5, 3, 0.25, 0, 0, 0, 1])


def test_crop_runs_before_numpy_sampling() -> None:
    points = np.column_stack((np.arange(2500) % 20, np.zeros(2500), np.zeros(2500)))

    sampled, selected = transform_crop_and_sample(
        points,
        transform=None,
        crop={"half_x": 10, "half_y": 1, "half_z": 1},
        center=None,
        max_points=1000,
    )

    assert selected == 1375
    assert sampled.shape == (1000, 3)
    assert (sampled[:, 0] <= 10).all()
