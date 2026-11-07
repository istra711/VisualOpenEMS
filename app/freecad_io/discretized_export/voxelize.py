"""Cell-centre rasterisation and boundary extraction for the MVP export."""

from __future__ import annotations

from typing import Callable, Iterable

import numpy as np

from .project_data import ObjectInfo

Progress = Callable[[int, int], None]
Cancel = Callable[[], bool]


def classify_cells(
    csx,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    names: Iterable[str],
    progress: Progress | None = None,
    cancel: Cancel | None = None,
) -> dict[str, np.ndarray]:
    """Return one boolean cell mask per object.

    A cell belongs to the object whose property wins at the cell centre.  This is
    the simple mesh rule agreed for the first implementation.
    """
    names = list(names)
    nx, ny, nz = len(x) - 1, len(y) - 1, len(z) - 1
    masks = {name: np.zeros((nx, ny, nz), dtype=bool) for name in names}
    wanted = set(names)
    xc = (x[:-1] + x[1:]) * 0.5
    yc = (y[:-1] + y[1:]) * 0.5
    zc = (z[:-1] + z[1:]) * 0.5
    total = nx * ny * nz
    done = 0
    report_step = max(1, total // 100)

    for k, zz in enumerate(zc):
        for j, yy in enumerate(yc):
            for i, xx in enumerate(xc):
                prop = csx.GetPropertyByCoordPriority(
                    [float(xx), float(yy), float(zz)]
                )
                if prop is not None:
                    name = str(prop.GetName())
                    if name in wanted:
                        masks[name][i, j, k] = True
                done += 1
                if progress is not None and done % report_step == 0:
                    progress(done, total)
                if cancel is not None and done % 4096 == 0 and cancel():
                    raise Cancelled()

    if progress is not None:
        progress(total, total)
    return masks


class Cancelled(RuntimeError):
    """Raised when the user cancels an export."""


def extract_boundary(
    mask: np.ndarray, x: np.ndarray, y: np.ndarray, z: np.ndarray
) -> np.ndarray:
    """Triangulated boundary of a boolean cell mask.

    The result is a triangle soup with outward normals, shape ``(N, 3, 3)``.
    Internal faces between occupied cells are omitted.
    """
    nx, ny, nz = mask.shape
    triangles: list[list[tuple[float, float, float]]] = []

    for i, j, k in zip(*np.nonzero(mask)):
        i, j, k = int(i), int(j), int(k)
        x0, x1 = float(x[i]), float(x[i + 1])
        y0, y1 = float(y[j]), float(y[j + 1])
        z0, z1 = float(z[k]), float(z[k + 1])

        if i == 0 or not mask[i - 1, j, k]:
            _add_quad(
                triangles,
                [(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1)],
                (-1.0, 0.0, 0.0),
            )
        if i == nx - 1 or not mask[i + 1, j, k]:
            _add_quad(
                triangles,
                [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],
                (1.0, 0.0, 0.0),
            )
        if j == 0 or not mask[i, j - 1, k]:
            _add_quad(
                triangles,
                [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],
                (0.0, -1.0, 0.0),
            )
        if j == ny - 1 or not mask[i, j + 1, k]:
            _add_quad(
                triangles,
                [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)],
                (0.0, 1.0, 0.0),
            )
        if k == 0 or not mask[i, j, k - 1]:
            _add_quad(
                triangles,
                [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)],
                (0.0, 0.0, -1.0),
            )
        if k == nz - 1 or not mask[i, j, k + 1]:
            _add_quad(
                triangles,
                [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
                (0.0, 0.0, 1.0),
            )

    if not triangles:
        return np.empty((0, 3, 3), dtype=float)
    return np.asarray(triangles, dtype=float)


def sheet_triangles(
    objects: Iterable[ObjectInfo], warnings: list[str]
) -> np.ndarray:
    """Planar triangles for zero-thickness boxes and polygon sheets.

    A zero-thickness sheet has no cell volume.  It is exported as a surface so
    that patch/sheet objects still appear in the STL instead of disappearing.
    """
    triangles: list[list[tuple[float, float, float]]] = []

    for obj in objects:
        for prim in obj.primitives:
            kind = str(prim.get("kind") or "box").lower()
            try:
                if kind == "box":
                    start = _vec3(prim["start"])
                    stop = _vec3(prim["stop"])
                    zero = _zero_axes(start, stop)
                    if len(zero) == 1:
                        axis = zero[0]
                        coord = stop[axis]
                        c0, c1 = (axis + 1) % 3, (axis + 2) % 3
                        lo = [min(start[a], stop[a]) for a in range(3)]
                        hi = [max(start[a], stop[a]) for a in range(3)]
                        verts: list[list[float]] = []
                        for a, b in ((lo[c0], lo[c1]), (hi[c0], lo[c1]),
                                     (hi[c0], hi[c1]), (lo[c0], hi[c1])):
                            p = [0.0, 0.0, 0.0]
                            p[c0], p[c1], p[axis] = a, b, coord
                            verts.append(p)
                        _add_polygon(
                            triangles,
                            [tuple(v) for v in verts],
                            _unit(axis),
                        )
                    elif len(zero) > 1:
                        warnings.append(
                            f"{obj.name}: degenerate box (line/point) - skipped"
                        )
                elif kind == "polygon":
                    length = float(prim.get("length") or 0.0)
                    if length > 0.0:
                        continue
                    axis = str(prim.get("axis") or "z").lower()
                    axis_idx = {"x": 0, "y": 1, "z": 2}.get(axis)
                    if axis_idx is None:
                        warnings.append(f"{obj.name}: unknown polygon axis - skipped")
                        continue
                    points = prim.get("points") or []
                    if len(points) < 3:
                        warnings.append(f"{obj.name}: polygon needs >= 3 points - skipped")
                        continue
                    coord = float(prim.get("coord") or 0.0)
                    c0, c1 = (axis_idx + 1) % 3, (axis_idx + 2) % 3
                    verts = []
                    for pt in points:
                        p = [0.0, 0.0, 0.0]
                        p[c0], p[c1], p[axis_idx] = float(pt[0]), float(pt[1]), coord
                        verts.append(tuple(p))
                    _add_polygon(triangles, verts, _unit(axis_idx))
            except (KeyError, TypeError, ValueError, IndexError) as exc:
                warnings.append(f"{obj.name}: invalid sheet primitive ({exc}) - skipped")

    if not triangles:
        return np.empty((0, 3, 3), dtype=float)
    return np.asarray(triangles, dtype=float)


def cell_edges(
    mask: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    *,
    boundary_only: bool = True,
) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """Edges of occupied Cartesian cells, deduplicated.

    ``boundary_only`` keeps cells on the surface of the occupied volume.  Their
    twelve cell edges form the visible voxel grid on the surface.  With
    ``boundary_only=False`` every occupied cell contributes its edges (also
    the internal grid).
    """
    nx, ny, nz = mask.shape
    keys: set[tuple[tuple[int, int, int], tuple[int, int, int]]] = set()

    for i, j, k in zip(*np.nonzero(mask)):
        i, j, k = int(i), int(j), int(k)
        if boundary_only and not _is_boundary_cell(mask, i, j, k):
            continue
        for jj in (j, j + 1):
            for kk in (k, k + 1):
                keys.add(_edge_key((i, jj, kk), (i + 1, jj, kk)))
        for ii in (i, i + 1):
            for kk in (k, k + 1):
                keys.add(_edge_key((ii, j, kk), (ii, j + 1, kk)))
        for ii in (i, i + 1):
            for jj in (j, j + 1):
                keys.add(_edge_key((ii, jj, k), (ii, jj, k + 1)))

    def point(node: tuple[int, int, int]) -> tuple[float, float, float]:
        return (float(x[node[0]]), float(y[node[1]]), float(z[node[2]]))

    return [(point(a), point(b)) for a, b in sorted(keys)]


def _is_boundary_cell(mask: np.ndarray, i: int, j: int, k: int) -> bool:
    nx, ny, nz = mask.shape
    if i == 0 or i == nx - 1 or j == 0 or j == ny - 1 or k == 0 or k == nz - 1:
        return True
    return not (
        mask[i - 1, j, k]
        and mask[i + 1, j, k]
        and mask[i, j - 1, k]
        and mask[i, j + 1, k]
        and mask[i, j, k - 1]
        and mask[i, j, k + 1]
    )


def _edge_key(
    a: tuple[int, int, int], b: tuple[int, int, int]
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    return (a, b) if a <= b else (b, a)


def compute_volume(
    mask: np.ndarray, x: np.ndarray, y: np.ndarray, z: np.ndarray
) -> float:
    """Volume of all occupied cells in drawing-unit^3."""
    if not mask.any():
        return 0.0
    dx = np.diff(x)[:, None, None]
    dy = np.diff(y)[None, :, None]
    dz = np.diff(z)[None, None, :]
    return float(np.sum(mask * dx * dy * dz))


def _add_quad(
    triangles: list[list[tuple[float, float, float]]],
    points: list[tuple[float, float, float]],
    outward: tuple[float, float, float],
) -> None:
    _add_polygon(triangles, points, outward)


def _add_polygon(
    triangles: list[list[tuple[float, float, float]]],
    points: list[tuple[float, float, float]],
    outward: tuple[float, float, float],
) -> None:
    if len(points) < 3:
        return
    p0, p1, p2 = points[0], points[1], points[2]
    normal = np.cross(np.subtract(p1, p0), np.subtract(p2, p0))
    if float(np.dot(normal, outward)) < 0.0:
        points = list(reversed(points))
    for index in range(1, len(points) - 1):
        triangles.append([points[0], points[index], points[index + 1]])


def _vec3(values) -> list[float]:
    values = list(values)
    if len(values) != 3:
        raise ValueError(f"expected 3 coordinates, got {len(values)}")
    return [float(v) for v in values]


def _zero_axes(start: list[float], stop: list[float]) -> list[int]:
    scale = max(1.0, *(abs(v) for v in start + stop))
    return [
        axis for axis in range(3)
        if abs(stop[axis] - start[axis]) <= 1e-12 * scale
    ]


def _unit(axis: int) -> tuple[float, float, float]:
    return tuple(1.0 if i == axis else 0.0 for i in range(3))  # type: ignore[return-value]
