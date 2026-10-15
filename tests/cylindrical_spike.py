"""Phase-0 spike: cylindrical (polar) FDTD with the installed openEMS 0.0.36.

Standalone checks BEFORE integrating polar meshing into the app:

  grid  -- CSXCAD grid API on a cylindrical mesh (line names, GetSimArea)
  t1    -- closed cylindrical cavity (PEC on all 6 faces, air, Q~100 via
           kappa background loss); the driven |S11| dip must sit at the
           analytic TM010 frequency. Also proves the full-2*pi azimuthal
           wrap ("closed Cylinder" in the operator log): a broken seam
           would force m>=1 and push the lowest TM dip to ~1.83 GHz.
  t2    -- dielectric wedge box (AddBox in r/alpha/z, eps=10 post) shifts
           the dip down as expected (~0.7 GHz)
  t3    -- same post as AddCylinder primitive (coordinate semantics) ->
           similar shift above t2 (~0.82 GHz); wrong coordinates would not
           shift the dip the same way

Run:  python tests/cylindrical_spike.py [--only grid|t1|t2|t3]
      [--nrts N] [--keep]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

C0 = 299792458.0
UNIT = 1e-3          # drawing unit: mm
A_RAD = 100.0        # cavity radius [mm]      -> TM010 = 2.4048 c /(2 pi a)
H = 100.0            # cavity height along z [mm] (TM010 is z-independent)
P_J01 = 2.404825557695773

F0, F_BW = 1.2e9, 1.6e9      # Gaussian excitation (covers 0.4 .. 2.0 GHz)
RES_MM = 15.0                # max cell size (lambda/10 @ 2 GHz)
RES_ALPHA = RES_MM / A_RAD   # max angular step (arc at outer radius)
RES_Z = 4.0                  # z cells = 25
KAPPA_BG = 0.01              # background loss -> Q ~ 1/kappa = 100
EPS_POST = 10.0
R_BOX = 20.0        # wedge box post radius [mm]
R_CYL = 15.0        # cylinder primitive radius [mm]

FC_AIR = P_J01 * C0 / (2 * np.pi * A_RAD * UNIT)   # ~1.1475 GHz


def _f32(x) -> float:
    return float(np.float32(x))


def probe_grid() -> bool:
    from CSXCAD import ContinuousStructure

    csx = ContinuousStructure(CoordSystem=1)
    g = csx.GetGrid()
    ok = True
    print(f"[grid] mesh type after CoordSystem=1: {g.GetMeshType()}")
    g.SetLines("r", [0.0, A_RAD])
    g.SmoothMeshLines("r", RES_MM)
    g.SetLines("a", [0.0, 2 * np.pi])
    g.SmoothMeshLines("a", RES_ALPHA)
    g.SetLines("z", [0.0, H])
    g.SmoothMeshLines("z", RES_Z)
    for name in ("r", "a", "z", "x", "y", "z", "alpha", "rho"):
        try:
            lines = g.GetLines(name)
            print(f"[grid] GetLines('{name}') -> {len(lines)} lines")
        except Exception as e:  # noqa: BLE001
            print(f"[grid] GetLines('{name}') FAILED: {e}")
            if name in ("x", "y"):
                ok = False
    for i in (0, 1, 2):
        print(f"[grid] GetLines({i}) -> {len(g.GetLines(i))} lines")
    try:
        print(f"[grid] GetSimArea() = {g.GetSimArea()}")
    except Exception as e:  # noqa: BLE001
        print(f"[grid] GetSimArea FAILED: {e}")
        ok = False
    nr = len(g.GetLines(0))
    na = len(g.GetLines(1))
    nz = len(g.GetLines(2))
    print(f"[grid] cells: r={nr - 1} a={na - 1} z={nz - 1}")
    ok = ok and nr > 4 and na > 16 and nz > 8
    print(f"[grid] {'PASS' if ok else 'FAIL'}")
    return ok


def _build_mesh(g) -> None:
    g.SetDeltaUnit(UNIT)
    g.SetLines("r", [0.0, A_RAD])
    g.SmoothMeshLines("r", RES_MM)
    g.SetLines("a", [0.0, 2 * np.pi])
    g.SmoothMeshLines("a", RES_ALPHA)
    g.SetLines("z", [0.0, H])
    g.SmoothMeshLines("z", RES_Z)


def _run(case: str, out_root: Path, nrts: int = 100_000,
         verbose: int = 2) -> float | None:
    """Run one cavity case, return |S11| dip frequency (Hz) or None."""
    from app.models.project import PortDef
    from app.postproc import sparams as sp
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS

    out_dir = out_root / case
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # kappa damps the ring-down fast: let it run the full record anyway,
    # the frequency resolution comes from the RECORD LENGTH (DFT window)
    FDTD = openEMS(NrTS=nrts, EndCriteria=1e-12, CoordSystem=1)
    FDTD.SetGaussExcite(F0, F_BW)
    FDTD.SetBoundaryCond([0, 0, 0, 0, 0, 0])  # closed cavity: PEC everywhere
    CSX = ContinuousStructure(CoordSystem=1)
    FDTD.SetCSX(CSX)

    # lossy background fills the domain (Q ~ 1/kappa)
    bg = CSX.AddMaterial("bg", epsilon=1.0, kappa=KAPPA_BG)
    bg.AddBox([_f32(0), _f32(0), _f32(0)],
              [_f32(A_RAD), _f32(2 * np.pi), _f32(H)], priority=1)

    feature_lines_r: list[float] = []
    if case in ("t2", "t3"):
        diel = CSX.AddMaterial("diel", epsilon=EPS_POST)
        if case == "t2":
            diel.AddBox([_f32(0), _f32(0), _f32(0)],
                        [_f32(R_BOX), _f32(2 * np.pi), _f32(H)],
                        priority=10)
            feature_lines_r = [R_BOX]
        else:
            diel.AddCylinder([_f32(0), _f32(0), _f32(0)],
                             [_f32(0), _f32(0), _f32(H)],
                             _f32(R_CYL), priority=10)
            feature_lines_r = [R_CYL]

    pdef = PortDef(name="p1", type="lumped",
                   start=[50.0, 0.6, 20.0], stop=[50.0, 0.6, 40.0],
                   resistance=50.0, direction="z", excite_port=True,
                   mesh_dirs="xy")
    p2def = PortDef(name="p2", type="lumped",
                    start=[50.0, 0.6, 60.0], stop=[50.0, 0.6, 80.0],
                    resistance=50.0, direction="z", excite_port=False,
                    mesh_dirs="xy")

    g = CSX.GetGrid()
    _build_mesh(g)
    for r in feature_lines_r:
        g.AddLine("r", [_f32(r)])
    g.SmoothMeshLines("r", RES_MM)

    # Snap ports onto EXISTING grid lines (adding an alpha line mid-cell
    # near the axis would create micro cells -> timestep collapse).
    def snap(axis: str, v: float) -> float:
        lines = np.asarray(g.GetLines(axis), dtype=float)
        return float(lines[int(np.argmin(np.abs(lines - v)))])

    r_p = snap("r", 50.0)
    a_p = snap("a", 0.6)
    pdef.start = [r_p, a_p, snap("z", 20.0)]
    pdef.stop = [r_p, a_p, snap("z", 40.0)]
    p2def.start = [r_p, a_p, snap("z", 60.0)]
    p2def.stop = [r_p, a_p, snap("z", 80.0)]

    pobj = FDTD.AddLumpedPort(
        1, 50.0, pdef.start, pdef.stop, pdef.direction, 1.0, priority=15)
    pobj2 = FDTD.AddLumpedPort(
        2, 50.0, p2def.start, p2def.stop, p2def.direction, 0.0, priority=15)

    print(f"[{case}] mesh: r={len(g.GetLines(0)) - 1} "
          f"a={len(g.GetLines(1)) - 1} z={len(g.GetLines(2)) - 1} cells "
          f"-> running cavity ...", flush=True)
    t0 = time.time()
    FDTD.Run(str(out_dir), verbose=verbose, cleanup=True, numThreads=8)
    print(f"[{case}] run took {time.time() - t0:.1f} s", flush=True)

    freqs = np.linspace(0.4e9, 2.4e9, 1001)
    ut = np.loadtxt(out_dir / "port_ut_1", comments="%")
    t_end = float(ut[-1, 0])
    print(f"[{case}] record {t_end * 1e9:.1f} ns -> dF = "
          f"{1 / t_end / 1e6:.1f} MHz", flush=True)
    table = sp.compute_sparams_from_ports(
        out_dir, [(pdef, pobj), (p2def, pobj2)], freqs
    )
    s11 = np.abs(table["S11"])
    if not np.all(np.isfinite(s11)):
        print(f"[{case}] FAIL: non-finite S11")
        return None

    # Resonance detector: FIRST prominent local |S11| minimum in the
    # TM010 band. Raw local minima are polluted by MHz-level late-time
    # wiggle, so require a real prominence (real dips are 0.2..0.7 deep
    # in contrast terms, wiggles <0.001).
    from scipy.signal import find_peaks

    smooth = np.convolve(s11, np.ones(5) / 5.0, mode="same")
    lo, hi = 0.45e9, 1.65e9
    yv = np.where((freqs >= lo) & (freqs <= hi), smooth, np.inf)
    peaks, props = find_peaks(-yv, prominence=0.0012, distance=10)
    dips = [float(freqs[p]) for p in peaks]
    print(f"[{case}] prominent dips in band: "
          f"{[round(d / 1e9, 3) for d in dips] or 'none'}")
    if not len(peaks):
        print(f"[{case}] FAIL: no resonance dip in band")
        return None
    fc_est = float(freqs[peaks[0]])
    dip = float(smooth[peaks[0]])

    def curve(a: np.ndarray) -> str:
        return " ".join(f"{f / 1e9:.2f}:{v:.3f}"
                        for f, v in zip(freqs[::10], a[::10]))

    print(f"[{case}] |S11| curve: {curve(s11)}")
    print(f"[{case}] dip={dip:.3f} @ {fc_est / 1e9:.4f} GHz "
          f"(TM010 air analytic {FC_AIR / 1e9:.4f} GHz)")
    return fc_est


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["grid", "t1", "t2", "t3"], default=None)
    ap.add_argument("--keep", action="store_true", help="keep run directories")
    ap.add_argument("--nrts", type=int, default=100_000)
    ap.add_argument("--verbose", type=int, default=2)
    args = ap.parse_args()

    results: dict[str, str] = {}

    if args.only in (None, "grid"):
        results["grid"] = "PASS" if probe_grid() else "FAIL"

    out_root = Path(tempfile.gettempdir()) / "openems_cyl_spike"

    fc: dict[str, float | None] = {}
    for case in ("t1", "t2", "t3"):
        if args.only not in (None, case):
            continue
        try:
            fc[case] = _run(case, out_root, nrts=args.nrts,
                            verbose=args.verbose)
        except Exception as e:  # noqa: BLE001
            print(f"[{case}] EXCEPTION: {type(e).__name__}: {e}")
            fc[case] = None

    if "t1" in fc:
        v = fc["t1"]
        if v is None:
            results["t1"] = "FAIL (no result)"
        else:
            err = abs(v - FC_AIR) / FC_AIR
            # a broken azimuthal seam (PEC fin) pushes the lowest TM dip
            # to TM110 ~1.83 GHz (outside the window) or clearly above 1.6
            results["t1"] = (
                f"{'PASS' if err < 0.07 and v < 1.6e9 else 'FAIL'} "
                f"(dip={v / 1e9:.4f} GHz, err={err * 100:.1f}%)"
            )
    if "t2" in fc:
        v = fc["t2"]
        results["t2"] = (
            "FAIL (no result)" if v is None else
            f"{'PASS' if 0.5 * FC_AIR < v < 0.90 * FC_AIR else 'FAIL'} "
            f"(dip={v / 1e9:.4f} GHz, expect in "
            f"[{0.5 * FC_AIR / 1e9:.3f}, {0.90 * FC_AIR / 1e9:.3f}] GHz)"
        )
    if "t3" in fc and fc["t3"] is not None and fc["t2"] is not None:
        v = fc["t3"]
        t2 = fc["t2"]
        # both posts load the cavity well below air; the smaller cylinder
        # must sit HIGHER than the bigger box (correct coordinate semantics)
        band = 0.5 * FC_AIR < v < 0.90 * FC_AIR
        results["t3"] = (
            f"{'PASS' if band and v > t2 else 'FAIL'} "
            f"(dip={v / 1e9:.4f} GHz, t2={t2 / 1e9:.4f} GHz)"
        )
    elif "t3" in fc:
        results["t3"] = "FAIL (no result)"

    print("\n=== SPIKE SUMMARY ===")
    rc = 0
    for k, v in results.items():
        print(f"  {k}: {v}")
        if not v.startswith("PASS"):
            rc = 1

    if not args.keep and out_root.exists():
        shutil.rmtree(out_root, ignore_errors=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
