"""Small binary STL writer/reader helpers (no external mesh dependency)."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


def write_binary_stl(path: str | Path, triangles: np.ndarray) -> Path:
    """Write a triangle soup as a binary STL in millimetres."""
    tris = np.asarray(triangles, dtype=float)
    if tris.size == 0:
        tris = np.empty((0, 3, 3), dtype=float)
    if tris.ndim != 3 or tris.shape[1:] != (3, 3):
        raise ValueError(f"triangles must be (N,3,3), got {tris.shape}")

    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(
        normals, lengths, out=np.zeros_like(normals), where=lengths > 0.0
    )

    records = np.zeros(
        len(tris),
        dtype=[("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attr", "<u2")],
    )
    records["normal"] = normals.astype("<f4")
    records["vertices"] = tris.astype("<f4")

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = b"VisualOpenEMS discretized export (units: mm)".ljust(80, b"\0")
    with open(out, "wb") as fh:
        fh.write(header)
        fh.write(struct.pack("<I", len(records)))
        fh.write(records.tobytes())
    return out


def read_binary_stl_count(path: str | Path) -> int:
    """Number of triangles in a binary STL (used by tests and the GUI)."""
    with open(Path(path), "rb") as fh:
        header = fh.read(80)
        if len(header) != 80:
            raise ValueError("not a binary STL")
        raw = fh.read(4)
        if len(raw) != 4:
            raise ValueError("truncated STL header")
        return int(struct.unpack("<I", raw)[0])
