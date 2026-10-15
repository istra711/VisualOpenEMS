"""Stability check for the rebuilt dipol: mesh sanity + short FDTD run + port waveforms.

Verifies:
1. No nm-scale cells / no spanning gap cell (regression on the easyMesh fixes).
2. openEMS SetupFDTD succeeds and the CFL dt is sane (not collapsed by a thin cell).
3. A short FDTD run produces finite port_ut_1 / port_it_1 (no 1e257 divergence).
"""
import json
import sys
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "third_party" / "easyMesh4openEMS"))
sys.path.insert(0, str(ROOT))

from openEMS import openEMS as OpenEMSClass
from CSXCAD import ContinuousStructure

from app.csxcad_builder.triangle_mesh import add_triangle_mesh
from app.meshing.easy_wrapper import enhance_csx, enhance_fdtd, apply_boundary, generate_mesh
from app.meshing.rules import compute_targets
from app.models.project import Project


def _get_domain_box(CSX):
    from app.meshing.preview import get_sim_area
    area = get_sim_area(CSX)
    if area is None:
        return None
    return [float(v) for v in area[0]], [float(v) for v in area[1]]


def dedupe_grid_lines(CSX, tol: float = 1e-12) -> None:
    grid = CSX.GetGrid()
    dom = _get_domain_box(CSX)
    removed_total = 0
    for axis in range(3):
        raw = grid.GetLines(axis)
        if raw is None:
            continue
        vals = np.array(raw, dtype=float)
        if vals.size < 2:
            continue
        keep = []
        removed = 0
        dom_lo = dom[0][axis] if dom is not None else None
        dom_hi = dom[1][axis] if dom is not None else None
        for v in vals:
            if dom_lo is not None and (abs(v - dom_lo) < tol or abs(v - dom_hi) < tol):
                keep.append(float(v))
                continue
            if any(abs(v - k) < tol for k in keep):
                removed += 1
            else:
                keep.append(float(v))
        if removed:
            grid.SetLines(axis, np.array(keep, dtype=float))
            removed_total += removed
            print(f"  dedupe axis {axis}: removed {removed} zero-size cell(s), {len(keep)} lines left")
    if removed_total == 0:
        print("  dedupe: no zero-size cells found")

PROJ = ROOT / "work" / "Dipol_CST_openems_Test_v2" / "Dipol_CST_openems_Test.fcsim.json"
OUT = Path(__file__).with_name("dipol_stability_out")


def _finite(a: np.ndarray) -> bool:
    return bool(np.all(np.isfinite(a)))


def main() -> int:
    project = Project.from_dict(json.loads(PROJ.read_text(encoding="utf-8")))
    mesh = project.mesh
    boundary = project.boundary

    f0 = mesh.target_frequency or 0.5 * (mesh.start_frequency + mesh.stop_frequency)
    fc = max(1e6, 0.5 * abs(mesh.stop_frequency - mesh.start_frequency))
    print(f"f0={f0/1e9:.4f} GHz  fc={fc/1e9:.4f} GHz")

    # Short run: enough steps to see the Gaussian pulse peak and check stability.
    FDTD = OpenEMSClass(NrTS=2000, EndCriteria=0)
    FDTD.SetGaussExcite(f0, fc)
    CSX = ContinuousStructure()
    FDTD.SetCSX(CSX)

    primitives_mesh_setup: dict = {}
    properties_mesh_setup: dict = {}
    CSX = enhance_csx(CSX, primitives_mesh_setup)
    FDTD = enhance_fdtd(FDTD, primitives_mesh_setup)

    targets = compute_targets(mesh, eps_max=1.0)
    apply_boundary(FDTD, boundary, mesh, targets)
    CSX.GetGrid().SetDeltaUnit(mesh.unit_to_meters())

    props = {}
    for mat in project.materials:
        if mat.kind == "pec":
            props[mat.name] = CSX.AddMetal(mat.name)
        elif mat.kind == "conducting_sheet":
            props[mat.name] = CSX.AddConductingSheet(mat.name, conductivity=mat.kappa)
        elif mat.kind == "dielectric":
            kwargs = {"epsilon": mat.epsilon}
            if mat.kappa:
                kwargs["kappa"] = mat.kappa
            props[mat.name] = CSX.AddMaterial(mat.name, **kwargs)
        else:
            props[mat.name] = CSX.AddMaterial(mat.name, epsilon=1.0)

    assign = {a.object_name: a.material for a in project.assignments}
    for pr_def in project.primitives:
        name = pr_def["name"]
        mat = assign.get(name, pr_def.get("material", "copper"))
        prop = props[mat]
        npy = Path(pr_def.get("path") or pr_def.get("mesh_path") or "")
        stl = Path(pr_def.get("stl_path") or "")
        add_triangle_mesh(
            prop,
            name=name,
            triangles=np.load(npy) if npy.exists() else None,
            stl_path=str(stl) if stl.exists() else None,
            mesh_path=str(npy) if npy.exists() else None,
            priority=int(pr_def.get("priority", 10)),
        )
        print(f"prim {name}: mat={mat}")

    port = project.ports[0]
    pobj = FDTD.AddLumpedPort(
        1,
        port.resistance,
        [float(x) for x in port.start],
        [float(x) for x in port.stop],
        port.direction,
        1.0 if port.excite_port else 0.0,
        priority=15,
        edges2grid=port.mesh_dirs or "xy",
    )
    print(f"port registered: {pobj is not None}")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        generate_mesh(
            CSX,
            mesh,
            boundary,
            targets,
            primitives_mesh_setup=primitives_mesh_setup,
            properties_mesh_setup=properties_mesh_setup,
        )

    # The simulation script_gen.py removes duplicate grid lines after mesh
    # generation; mirror that so this test measures the real FDTD grid.
    dedupe_grid_lines(CSX)

    # --- Mesh sanity: cell size per axis. Zero-size cells (exact duplicates)
    # collapse the Courant timestep, so those are a hard FAIL, like nm-scale. ---
    grid = CSX.GetGrid()
    ok = True
    for axis, nm in enumerate("xyz"):
        lines = np.sort(np.array(grid.GetLines(axis), dtype=float))
        cells = np.diff(lines)
        pos = cells[cells > 0]
        n_zero = int(np.sum(cells < 1e-12))
        n_nm = int(np.sum((cells >= 1e-12) & (cells < 1e-6)))
        min_cell = pos.min() if pos.size else float("nan")
        print(f"axis {nm}: {len(lines)} lines, min cell={min_cell:.6e} mm, "
              f"zero cells={n_zero}, nm cells={n_nm}, "
              f"range=[{lines.min():.4f},{lines.max():.4f}]")
        if n_zero:
            print(f"  FAIL: {n_zero} zero-size cell(s) on {nm} (duplicate grid line)")
            ok = False
        if n_nm:
            print(f"  FAIL: {n_nm} nm-scale cells on {nm}")
            ok = False
        if not np.isfinite(min_cell) or min_cell < 1e-3:
            print(f"  FAIL: min cell {min_cell:.3e} mm is sub-micron")
            ok = False

    gap_lo, gap_hi = sorted([float(port.start[2]), float(port.stop[2])])
    z = np.sort(np.array(grid.GetLines(2), dtype=float))
    in_gap = z[(z > gap_lo + 1e-9) & (z < gap_hi - 1e-9)]
    spanning = any(a < gap_lo - 1e-9 and b > gap_hi + 1e-9 for a, b in zip(z[:-1], z[1:]))
    print(f"feed gap [{gap_lo:.6f},{gap_hi:.6f}]: in_gap={len(in_gap)} spanning={spanning}")
    if spanning:
        print("  FAIL: a single cell spans the feed gap")
        ok = False

    # --- Short FDTD run ---
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"\nRunning short FDTD into {OUT}")
    try:
        FDTD.Run(str(OUT), verbose=3, cleanup=True)
        print("FDTD run finished")
    except Exception as e:
        print(f"FAIL: FDTD run raised: {e}")
        return 1

    # --- Port waveform sanity ---
    for fn in ("port_ut_1", "port_it_1"):
        f = OUT / fn
        if not f.exists():
            print(f"FAIL: {fn} missing")
            ok = False
            continue
        data = np.loadtxt(f, comments="%")
        t, val = data[:, 0], data[:, 1]
        finite = _finite(val)
        peak = float(np.max(np.abs(val))) if val.size else 0.0
        print(f"{fn}: n={len(t)} t=[{t.min():.3e},{t.max():.3e}] "
              f"peak={peak:.6e} finite={finite}")
        if not finite:
            print(f"  FAIL: {fn} has non-finite values")
            ok = False
        if peak > 1e12:
            print(f"  FAIL: {fn} peak {peak:.3e} indicates divergence")
            ok = False
        if peak == 0.0:
            print(f"  FAIL: {fn} is all-zero (port not excited / not probed)")
            ok = False

    print("\nSTABILITY:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
