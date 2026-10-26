"""Slice geometry of polygon / linpoly primitives (no Qt, no matplotlib).

A CSXCAD polygon primitive stores its outline in the plane perpendicular to a
normal axis, using CSXCAD's cyclic mapping:

    normal x -> points are (y, z)
    normal y -> points are (z, x)
    normal z -> points are (x, y)

plus an elevation (the position of that plane) and — for ``AddLinPoly`` — an
extrusion length along the normal.

The Mesh Viewer used to draw the AABB of such a primitive, so every hexagon,
triangle or chamfered patch appeared as a rectangle. These helpers return the
true outline per slice instead, which is what the viewer draws:

* face-on cut   -> the polygon itself (sheet) or its cross-section (prism),
* edge-on cut   -> a segment (sheet) or a quad (extruded prism),
* projection    -> the outline (face-on) or the silhouette (edge-on).

All coordinates are in drawing units; the caller maps them to screen axes.
"""

from __future__ import annotations

#: normal axis letter -> index into (x, y, z)
AXIS_INDEX = {"x": 0, "y": 1, "z": 2}

#: tolerance for "the point lies exactly on the cut line" tests
_EPS = 1e-12


def _clean_points(points) -> list[tuple[float, float]]:
    """``[(u, v), ...]`` as floats, dropping malformed entries."""
    out: list[tuple[float, float]] = []
    for p in points or []:
        try:
            u, v = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        out.append((u, v))
    return out


def polygon_bbox(points, axis: str, coord: float, length: float = 0.0):
    """3-D AABB ``(lo, hi)`` of a polygon sheet / extruded prism, or None.

    ``coord`` is the elevation of the polygon plane, ``length`` the extrusion
    along the normal axis (0 for a flat sheet).
    """
    idx = AXIS_INDEX.get(str(axis or "z").lower(), 2)
    pts = _clean_points(points)
    if not pts:
        return None
    c0, c1 = (idx + 1) % 3, (idx + 2) % 3
    us = [p[0] for p in pts]
    vs = [p[1] for p in pts]
    lo = [0.0, 0.0, 0.0]
    hi = [0.0, 0.0, 0.0]
    lo[c0], hi[c0] = min(us), max(us)
    lo[c1], hi[c1] = min(vs), max(vs)
    lo[idx] = float(coord)
    hi[idx] = float(coord) + max(0.0, float(length or 0.0))
    return lo, hi


def polygon_line_hits(points, axis: int, value: float):
    """Crossings of the closed outline with the line ``u``/``v = value``.

    ``axis`` 0 cuts ``u = value``, 1 cuts ``v = value`` (polygon coordinates).
    An edge lying exactly on the line contributes both of its end points.
    """
    pts = _clean_points(points)
    hits: list[tuple[float, float]] = []
    n = len(pts)
    for i in range(n):
        u0, v0 = pts[i]
        u1, v1 = pts[(i + 1) % n]
        a0, a1 = (u0, u1) if axis == 0 else (v0, v1)
        if abs(a1 - a0) <= _EPS:
            if abs(a0 - value) <= _EPS:  # edge lies on the cut line
                hits.append((u0, v0))
                hits.append((u1, v1))
            continue
        if (a0 - value) * (a1 - value) <= 0.0:
            t = (value - a0) / (a1 - a0)
            hits.append((u0 + t * (u1 - u0), v0 + t * (v1 - v0)))
    return hits


def polygon_cut_paths(
    points,
    axis: str,
    coord: float,
    length: float,
    ia: int,
    ib: int,
    inorm: int,
    plane,
    tol: float,
):
    """Outlines of a polygon cut by a slice plane, in slice coordinates.

    ``ia``/``ib`` are the in-plane axis indices of the slice, ``inorm`` its
    normal axis index (see ``_SLICE_AXES`` in the viewer), ``plane`` the slice
    coordinate along ``inorm``.

    Returns a list of 2-D point lists:

    * >= 3 points -> closed outline (polygon seen face-on),
    * 2 points    -> open segment (edge-on cut through a flat sheet),
    * 4 points    -> quad (edge-on cut through an extruded prism).

    An empty list means the plane does not touch the polygon.
    ``plane=None`` draws the projection (overview): the outline when seen
    face-on, otherwise the silhouette (segment / rectangle).
    """
    idx = AXIS_INDEX.get(str(axis or "z").lower(), 2)
    c0, c1 = (idx + 1) % 3, (idx + 2) % 3
    pts = _clean_points(points)
    if len(pts) < 3:
        return []
    coord = float(coord)
    length = max(0.0, float(length or 0.0))

    def to_slice(u: float, v: float, along: float) -> tuple[float, float]:
        p = [0.0, 0.0, 0.0]
        p[c0], p[c1], p[idx] = u, v, along
        return (p[ia], p[ib])

    if inorm == idx:
        # face-on: the sheet at ``coord`` or any plane inside the prism
        if plane is not None:
            if length > 0.0:
                if plane < coord - tol or plane > coord + length + tol:
                    return []
            elif abs(float(plane) - coord) > tol:
                return []
        if (ia, ib) == (c0, c1):
            return [list(pts)]
        if (ia, ib) == (c1, c0):
            return [[(v, u) for u, v in pts]]
        return []

    # edge-on: the slice plane cuts the polygon plane along a straight line
    if plane is None:
        along = (coord,) if length <= 0.0 else (coord, coord + length)
        mapped = [to_slice(u, v, a) for u, v in pts for a in along]
        xs = [p[0] for p in mapped]
        ys = [p[1] for p in mapped]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if abs(x1 - x0) <= 1e-9 or abs(y1 - y0) <= 1e-9:
            return [[(x0, y0), (x1, y1)]]
        return [[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]

    cut = 0 if inorm == c0 else 1
    hits = polygon_line_hits(pts, cut, float(plane))
    if len(hits) < 2:
        return []
    vary = 1 - cut
    hits.sort(key=lambda p: (p[vary], p[cut]))
    uniq: list[tuple[float, float]] = []
    for p in hits:
        # the cut line may pass through a vertex -> merge the duplicates
        if not uniq or abs(p[vary] - uniq[-1][vary]) > 1e-9:
            uniq.append(p)
    paths: list[list[tuple[float, float]]] = []
    for k in range(0, len(uniq) - 1, 2):
        a, b = uniq[k], uniq[k + 1]
        if length > 0.0:
            paths.append(
                [
                    to_slice(a[0], a[1], coord),
                    to_slice(b[0], b[1], coord),
                    to_slice(b[0], b[1], coord + length),
                    to_slice(a[0], a[1], coord + length),
                ]
            )
        else:
            paths.append(
                [to_slice(a[0], a[1], coord), to_slice(b[0], b[1], coord)]
            )
    return paths
