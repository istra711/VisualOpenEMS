import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from PySide6.QtWidgets import QApplication

from app.settings import AppSettings
from app.ui.main_window import MainWindow
from app.ui.sparam_viewer import SParamViewer


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow(AppSettings.load())
    tabs = [w.sparam_tabs.tabText(i) for i in range(w.sparam_tabs.count())]
    print("tabs:", tabs)
    assert "S-Params" in tabs
    assert isinstance(w.sparam_viewer, SParamViewer)
    # load prod results without show()
    touch = APP_ROOT / "work" / "patch_prod" / "simulation" / "sparams.s1p"
    w.sparam_viewer.load_path(touch)
    print("viewer info:", w.sparam_viewer.lbl_info.text())
    w.sparam_viewer._goto_min()
    print("markers:", w.sparam_viewer.markers, "rows", w.sparam_viewer.marker_list.count())

    # Fit NF2FF must push the new box to the Mesh Viewer (no rebuild needed)
    w._on_load_patch()
    calls: list = []
    w.mesh_viewer.set_nf2ff = lambda box=None: calls.append(box)
    w._on_fit_nf2ff()  # no built mesh yet -> hint only, viewer untouched
    assert not calls, "fit without a built mesh must not call set_nf2ff"
    w.mesh_viewer._csx = object()  # pretend the mesh is built
    w._on_fit_nf2ff()
    assert calls and calls[-1] is w.project.nf2ff, "Fit NF2FF did not update the viewer"
    print("fit nf2ff -> viewer:", calls[-1].start, "->", calls[-1].stop)
    w.mesh_viewer._csx = None
    print("MAINWINDOW SPARAM OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
