import struct

import numpy as np

from galbot_sdk_web_console.geometry.pcd import load_pcd_xyz


def test_load_ascii_pcd_xyz(tmp_path) -> None:
    path = tmp_path / "map.pcd"
    path.write_text("""# .PCD v0.7
FIELDS x y z intensity
SIZE 4 4 4 4
TYPE F F F F
COUNT 1 1 1 1
WIDTH 2
HEIGHT 1
POINTS 2
DATA ascii
1 2 3 8
4 5 6 9
""", encoding="ascii")
    np.testing.assert_allclose(load_pcd_xyz(path), [[1, 2, 3], [4, 5, 6]])


def test_load_binary_pcd_xyz(tmp_path) -> None:
    path = tmp_path / "map.pcd"
    header = b"FIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\nWIDTH 2\nHEIGHT 1\nPOINTS 2\nDATA binary\n"
    path.write_bytes(header + struct.pack("<ffffff", 1, 2, 3, 4, 5, 6))
    np.testing.assert_allclose(load_pcd_xyz(path), [[1, 2, 3], [4, 5, 6]])
