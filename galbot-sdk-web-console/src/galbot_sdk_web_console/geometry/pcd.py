"""Small PCD XYZ reader used to embed static maps in Rerun recordings."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


def _lzf_decompress(source: bytes, output_length: int) -> bytes:
    output = bytearray(output_length)
    source_index = output_index = 0
    while source_index < len(source):
        control = source[source_index]
        source_index += 1
        if control < 32:
            length = control + 1
            output[output_index:output_index + length] = source[source_index:source_index + length]
            source_index += length
            output_index += length
            continue
        length = control >> 5
        reference = output_index - ((control & 31) << 8) - 1
        if length == 7:
            length += source[source_index]
            source_index += 1
        reference -= source[source_index]
        source_index += 1
        for _ in range(length + 2):
            if reference < 0 or reference >= output_index or output_index >= output_length:
                raise ValueError("invalid LZF back-reference")
            output[output_index] = output[reference]
            output_index += 1
            reference += 1
    if output_index != output_length:
        raise ValueError("LZF output length does not match PCD header")
    return bytes(output)


def load_pcd_xyz(path: Path, max_points: int = 250_000) -> np.ndarray:
    raw = path.read_bytes()
    marker = raw.find(b"\nDATA ")
    if marker < 0:
        marker = raw.find(b"\r\nDATA ")
    if marker < 0:
        raise ValueError("PCD DATA header is missing")
    line_end = raw.find(b"\n", marker + 1)
    if line_end < 0:
        raise ValueError("PCD header is incomplete")
    header_text = raw[:line_end].decode("ascii", errors="strict")
    payload = raw[line_end + 1:]
    header: dict[str, list[str]] = {}
    for line in header_text.replace("\r", "").split("\n"):
        parts = line.strip().split()
        if parts and not parts[0].startswith("#"):
            header[parts[0].upper()] = parts[1:]
    fields = header.get("FIELDS") or header.get("FIELD") or []
    sizes = [int(value) for value in header.get("SIZE", [])]
    types = header.get("TYPE", [])
    counts = [int(value) for value in header.get("COUNT", ["1"] * len(fields))]
    points = int((header.get("POINTS") or [str(int(header.get("WIDTH", ["0"])[0]) * int(header.get("HEIGHT", ["1"])[0]))])[0])
    mode = header.get("DATA", [""])[0].lower()
    if not {"x", "y", "z"}.issubset(fields) or not (len(fields) == len(sizes) == len(types) == len(counts)):
        raise ValueError("PCD XYZ field layout is unsupported")
    if mode == "ascii":
        rows = np.loadtxt(payload.decode("ascii").splitlines(), dtype=np.float64, ndmin=2)
        offsets = np.cumsum([0, *counts[:-1]])
        xyz = rows[:, [int(offsets[fields.index(axis)]) for axis in ("x", "y", "z")]]
    elif mode == "binary":
        formats = []
        names = []
        type_codes = {("F", 4): "<f4", ("F", 8): "<f8", ("I", 1): "i1", ("I", 2): "<i2", ("I", 4): "<i4", ("U", 1): "u1", ("U", 2): "<u2", ("U", 4): "<u4"}
        for name, kind, size, count in zip(fields, types, sizes, counts):
            code = type_codes.get((kind.upper(), size))
            if code is None:
                raise ValueError(f"unsupported PCD field type: {kind}{size}")
            names.append(name)
            formats.append((code, (count,)) if count > 1 else code)
        data = np.frombuffer(payload, dtype=np.dtype({"names": names, "formats": formats}), count=points)
        xyz = np.column_stack([data[axis] for axis in ("x", "y", "z")]).astype(np.float64)
    elif mode == "binary_compressed":
        if len(payload) < 8:
            raise ValueError("compressed PCD payload is incomplete")
        compressed_size, uncompressed_size = struct.unpack_from("<II", payload)
        decompressed = _lzf_decompress(payload[8:8 + compressed_size], uncompressed_size)
        offsets: dict[str, int] = {}
        offset = 0
        for name, size, count in zip(fields, sizes, counts):
            offsets[name] = offset
            offset += size * count * points
        columns = []
        for axis in ("x", "y", "z"):
            index = fields.index(axis)
            code = {("F", 4): "<f4", ("F", 8): "<f8"}.get((types[index].upper(), sizes[index]))
            if code is None:
                raise ValueError("compressed PCD XYZ fields must be floating point")
            columns.append(np.frombuffer(decompressed, dtype=code, count=points, offset=offsets[axis]))
        xyz = np.column_stack(columns).astype(np.float64)
    else:
        raise ValueError(f"unsupported PCD DATA mode: {mode}")
    xyz = xyz[np.isfinite(xyz).all(axis=1)]
    if len(xyz) > max_points:
        xyz = xyz[np.linspace(0, len(xyz) - 1, max_points, dtype=np.int64)]
    return xyz
