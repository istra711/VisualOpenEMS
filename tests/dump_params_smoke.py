"""Smoke: Field-dump dialog exposes CSXCAD AddDump params (file_type, dump_mode,
frequencies)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app.models.project import DumpDef
    from app.ui.geometry_editors import DumpDialog

    app = QApplication.instance() or QApplication(sys.argv)

    # new dump: defaults are HDF5 (1) + node interpolation (1)
    dlg = DumpDialog()
    assert dlg.cmb_file_type.currentData() == 1
    assert dlg.cmb_mode.currentData() == 1
    # choose VTK + cell + explicit frequency list
    dlg.cmb_file_type.setCurrentIndex(1)  # VTK (.vtr) -> 0
    dlg.cmb_mode.setCurrentIndex(2)  # Cell -> 2
    dlg.ed_freqs.setText("2.4 GHz, 2.45 GHz")
    dlg.spn_sub[0].setValue(10)  # time-domain sub-sampling
    dlg._accept()
    d = dlg.result
    assert isinstance(d, DumpDef)
    assert d.file_type == 0, d.file_type
    assert d.dump_mode == 2, d.dump_mode
    assert d.frequencies and len(d.frequencies) == 2, d.frequencies
    assert abs(d.frequencies[0] - 2.4e9) < 1.0 and abs(d.frequencies[1] - 2.45e9) < 1.0
    assert d.sub_sampling == [10, 1, 1], d.sub_sampling
    print("  dump dialog -> file_type/dump_mode/frequencies/sub_sampling OK")

    # round trip through Project JSON keeps the new fields
    import tempfile

    from app.models.project import Project

    p = Project.new_default("dump_params")
    p.dumps = [d]
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "p.fcsim.json"
        p.save(path)
        q = Project.load(path)
    assert q.dumps[0].file_type == 0
    assert q.dumps[0].dump_mode == 2
    assert q.dumps[0].frequencies and len(q.dumps[0].frequencies) == 2
    print("  project JSON round trip OK")

    # "Fit to geometry" / "Use NF2FF box" buttons fill the Start/Stop spins
    dlg2 = DumpDialog(
        fit_box=([-5.0, -5.0, -30.0], [5.0, 5.0, 30.0]),
        nf2ff_box=([-40.0, -40.0, -40.0], [40.0, 40.0, 40.0]),
    )
    assert dlg2.btn_fit.isEnabled() and dlg2.btn_nf2ff.isEnabled()
    dlg2.btn_fit.click()
    assert [round(s.value(), 1) for s in dlg2.spn_start] == [-5.0, -5.0, -30.0]
    assert [round(s.value(), 1) for s in dlg2.spn_stop] == [5.0, 5.0, 30.0]
    dlg2.btn_nf2ff.click()
    assert [round(s.value(), 1) for s in dlg2.spn_start] == [-40.0, -40.0, -40.0]
    print("  Fit-to-geometry / Use-NF2FF buttons OK")

    dlg3 = DumpDialog()  # no reference boxes -> buttons disabled
    assert not dlg3.btn_fit.isEnabled() and not dlg3.btn_nf2ff.isEnabled()

    print("DUMP PARAMS SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
