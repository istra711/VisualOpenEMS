"""Inspect dipol STL bounds and material to find the feed short."""
from pathlib import Path
import struct
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MESH_DIR = ROOT / "work" / "imported" / "Test_dipole_openems_cst"


def stl_bounds(path: Path):
    data = path.read_bytes()
    if data[:5] == b"solid" and b"facet" in data[:1024]:
        # ASCII
        verts = []
        for line in data.decode("utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("vertex"):
                verts.append([float(x) for x in line.split()[1:4]])
        tris = np.array(verts).reshape(-1, 3, 3)
    else:
        n = struct.unpack("<I", data[80:84])[0]
        tris = np.zeros((n, 3, 3), dtype=float)
        off = 84
        for i in range(n):
            # normal(3f) + 3 vertices(3f) + attr(H)
            vals = struct.unpack("<12fH", data[off:off + 50])
            tris[i] = np.array(vals[3:12]).reshape(3, 3)
            off += 50
    return tris


def main() -> int:
    for name in ["Part__Feature", "Part__Feature001"]:
        stl = MESH_DIR / f"{name}.stl"
        tris = stl_bounds(stl)
        lo = tris.reshape(-1, 3).min(axis=0)
        hi = tris.reshape(-1, 3).max(axis=0)
        print(f"\n{name}: {len(tris)} triangles")
        print(f"  bounds min={lo} max={hi}")
        print(f"  z range = [{lo[2]:.9f}, {hi[2]:.9f}]")
        # extent
        print(f"  extent = {hi - lo}")
        # is it a solid cylinder or a shell?
        r = np.sqrt(tris.reshape(-1, 3)[:, 0] ** 2 + tris.reshape(-1, 3)[:, 1] ** 2)
        print(f"  radial extent = [{r.min():.6f}, {r.max():.6f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
