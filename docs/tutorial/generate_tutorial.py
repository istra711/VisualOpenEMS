"""Generate the step-by-step IFA tutorial (screenshots, frames, MP4).

The tutorial shows how to build and simulate an inverted-F antenna on a PCB at
5.8 GHz in VisualOpenEMS.  It uses the real GUI widgets (dialogs, mesh viewer,
S-parameter viewer, antenna viewer, field viewer) and representative result data
so the viewers can be explained without waiting for a full FDTD run.

Run from the repository root:

    python docs/tutorial/generate_tutorial.py

Outputs:
    docs/tutorial/assets/*.png          GUI screenshots + rendered diagrams
    docs/tutorial/frames/*.png          composed 1600x900 video slides
    docs/tutorial/IFA_5p8GHz_Tutorial.mp4
    docs/tutorial/README.md             storyboard / transcript
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TUTORIAL = ROOT / "docs" / "tutorial"
ASSETS = TUTORIAL / "assets"
FRAMES = TUTORIAL / "frames"
VIDEO = TUTORIAL / "IFA_5p8GHz_Tutorial.mp4"
WORK = Path(tempfile.mkdtemp(prefix="ifa_tutorial_"))

W, H = 1600, 900
FPS = 30
HOLD_SECONDS = 4.2
FADE_SECONDS = 0.35

# Palette for the video frame (the screenshots keep their native light style).
BG_TOP = (10, 16, 28)
BG_BOTTOM = (17, 28, 45)
TEXT = (241, 245, 249)
MUTED = (148, 163, 184)
ACCENT = (34, 211, 238)
ACCENT_SOFT = (14, 116, 144)
COPPER = (196, 118, 52)

_PIL_FONTS: dict[str, object] = {}
_FONT_FAMILY = "Segoe UI"


# ---------------------------------------------------------------------------
# Qt / matplotlib setup


def _setup_environment() -> tuple[object, object]:
    """Qt application + a real font (offscreen Qt has no font database)."""
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    global _FONT_FAMILY
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        import vtk

        output = vtk.vtkFileOutputWindow()
        output.SetFileName(str(WORK / "vtk_messages.log"))
        vtk.vtkOutputWindow.SetInstance(output)
    except Exception:  # noqa: BLE001
        pass
    candidates = [
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if not path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            _FONT_FAMILY = families[0]
            app.setFont(QFont(_FONT_FAMILY, 9))
            try:
                import matplotlib

                matplotlib.use("QtAgg")
                from matplotlib import font_manager, rcParams

                font_manager.fontManager.addfont(str(path))
                rcParams["font.family"] = [_FONT_FAMILY, "DejaVu Sans"]
            except Exception:  # noqa: BLE001
                pass
            break
    return app, None


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    key = f"{size}:{bold}"
    if key not in _PIL_FONTS:
        candidates = [
            Path(r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf"),
            Path(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
        for path in candidates:
            if path.exists():
                _PIL_FONTS[key] = ImageFont.truetype(str(path), size)
                break
        else:
            _PIL_FONTS[key] = ImageFont.load_default()
    return _PIL_FONTS[key]


# ---------------------------------------------------------------------------
# IFA project


def _build_project():
    from app.models.project import DumpDef, MaterialDef, NF2FFDef, PortDef, Project

    project = Project.new_default("IFA_5p8GHz")
    project.mesh.drawing_unit = "mm"
    project.mesh.start_frequency = 5.0e9
    project.mesh.stop_frequency = 6.6e9
    project.mesh.target_frequency = 5.8e9
    project.mesh.preset = "high"
    project.mesh.min_cellsize = 0.15
    project.mesh.min_cellsize_z = 0.08
    project.mesh.refined_cellsize = 0.2
    project.mesh.max_cellsize = 1.2
    project.mesh.cells_per_wavelength = 18
    project.mesh.add_graded_mesh_lines_at_material_transitions = True
    project.boundary.zmin = "PEC"
    project.boundary.air_margin = [12.0, 12.0, 12.0, 12.0, 12.0, 12.0]

    project.materials = [
        MaterialDef(name="copper_ground", kind="pec", color=(0.78, 0.48, 0.20, 1.0)),
        MaterialDef(name="copper_antenna", kind="pec", color=(0.95, 0.62, 0.18, 1.0)),
        MaterialDef(
            name="FR4",
            kind="dielectric",
            epsilon=4.4,
            tand=0.02,
            color=(0.18, 0.48, 0.68, 0.55),
        ),
        MaterialDef(name="vacuum", kind="dielectric", epsilon=1.0),
    ]

    primitives = [
        {
            "kind": "box",
            "name": "PCB_FR4",
            "material": "FR4",
            "priority": 1,
            "start": [0.0, 0.0, 0.0],
            "stop": [25.0, 45.0, 1.6],
        },
        {
            "kind": "box",
            "name": "GND_bottom",
            "material": "copper_ground",
            "priority": 10,
            "start": [0.0, 0.0, 0.0],
            "stop": [25.0, 45.0, 0.035],
        },
        {
            "kind": "box",
            "name": "GND_top",
            "material": "copper_ground",
            "priority": 10,
            "start": [0.0, 0.0, 1.6],
            "stop": [25.0, 35.0, 1.635],
        },
        {
            "kind": "box",
            "name": "IFA_short",
            "material": "copper_antenna",
            "priority": 10,
            "start": [10.0, 35.0, 1.6],
            "stop": [12.0, 40.0, 1.635],
        },
        {
            "kind": "box",
            "name": "IFA_arm",
            "material": "copper_antenna",
            "priority": 10,
            "start": [10.0, 38.0, 1.6],
            "stop": [17.0, 40.0, 1.635],
        },
        {
            "kind": "box",
            "name": "IFA_feed",
            "material": "copper_antenna",
            "priority": 10,
            "start": [12.5, 35.5, 1.6],
            "stop": [14.5, 38.0, 1.635],
        },
    ]
    project.primitives = [dict(p) for p in primitives]
    project.ports = [
        PortDef(
            name="in1",
            type="lumped",
            start=[13.5, 35.5, 1.6],
            stop=[13.5, 35.0, 1.6],
            resistance=50.0,
            direction="y",
            excite_port=True,
            lumped_width="x",
            metal="IFA_feed",
            mesh_dirs="xyz",
        )
    ]
    dump = DumpDef(
        name="E_5p8",
        quantity="E",
        start=[-5.0, 28.0, -3.0],
        stop=[30.0, 47.0, 14.0],
        frequency_domain=True,
        frequency=5.8e9,
        file_type=0,
        sub_sampling=[1, 1, 1],
        opt_resolution=[0.2, 0.2, 0.2],
    )
    project.dumps = [dump]
    project.nf2ff = NF2FFDef(
        enabled=True,
        box_mode="auto",
        frequencies=[5.8e9],
        freq_mode="list",
        theta_steps=181,
        phi_steps=361,
    )
    return project, primitives, dump


# ---------------------------------------------------------------------------
# Representative result data (for the viewers)


def _write_synthetic_results() -> tuple[Path, Path]:
    import numpy as np

    s11_path = ASSETS / "IFA_5p8GHz.s1p"
    f = np.linspace(5.0e9, 6.6e9, 321)
    f0 = 5.80e9
    q = 18.0
    r = 54.0
    z0 = 50.0
    w = 2 * np.pi * f
    w0 = 2 * np.pi * f0
    inductance = q * r / w0
    capacitance = 1.0 / (w0 * w0 * inductance)
    zin = r + 1j * (w * inductance - 1.0 / (w * capacitance))
    gamma = (zin - z0) / (zin + z0)
    gamma *= 1.0 + 0.015 * np.sin(2 * np.pi * (f - f[0]) / 0.35e9)
    with s11_path.open("w", encoding="utf-8") as handle:
        handle.write("! Inverted-F antenna on FR4, 5.8 GHz (tutorial example)\n")
        handle.write("# Hz S RI R 50\n")
        for freq, value in zip(f, gamma):
            handle.write(f"{freq:.0f} {value.real:.9e} {value.imag:.9e}\n")

    theta = np.arange(0, 181, 2, dtype=float)
    phi = np.arange(0, 361, 5, dtype=float)
    theta_r = np.deg2rad(theta)[:, None]
    phi_r = np.deg2rad(phi)[None, :]
    # IFA on a PCB: a broad lobe tilted away from the board, with mild ripple.
    main = np.exp(-0.5 * ((theta_r - 32.0) / 30.0) ** 2)
    back = 0.035
    board = 0.74 + 0.26 * np.cos(phi_r - np.deg2rad(90.0)) ** 2
    ripple = 1.0 + 0.055 * np.sin(3 * phi_r) * np.sin(theta_r) ** 2
    pattern = np.maximum((main + back) * board * ripple, 1e-4)
    pattern /= pattern.max()
    gain = np.round(2.6 + 10.0 * np.log10(pattern), 3)
    payload = {
        "frequencies": [5.8e9],
        "theta_deg": theta.tolist(),
        "phi_deg": phi.tolist(),
        "gain_dBi": [gain.tolist()],
        "D_dBi": [(gain + 0.55).tolist()],
        "Prad": [0.68],
        "Dmax_dBi": [3.15],
        "efficiency": [0.68],
    }
    nf2ff_path = ASSETS / "nf2ff_IFA_5p8GHz.json"
    nf2ff_path.write_text(json.dumps(payload), encoding="utf-8")
    return s11_path, nf2ff_path


def _write_field_vtr(path: Path) -> None:
    """A tiny VTK grid so the Field Viewer shows file/array labels.

    The rendered field itself is drawn with matplotlib (the offscreen VTK pane
    has no GL context on this machine); this grid only feeds the real viewer's
    data controls.
    """
    import numpy as np
    import vtk
    from vtk.util import numpy_support

    x = np.linspace(-5.0, 30.0, 36)
    y = np.linspace(28.0, 47.0, 39)
    z = np.linspace(-3.0, 14.0, 35)
    grid = vtk.vtkRectilinearGrid()
    grid.SetDimensions(len(x), len(y), len(z))
    grid.SetXCoordinates(numpy_support.numpy_to_vtk(x, deep=True))
    grid.SetYCoordinates(numpy_support.numpy_to_vtk(y, deep=True))
    grid.SetZCoordinates(numpy_support.numpy_to_vtk(z, deep=True))
    xx, yy = np.meshgrid(x, y, indexing="ij")
    mag = np.exp(-((xx - 13.5) ** 2 + (yy - 36.5) ** 2) / 8.0)
    mag = np.repeat(mag[:, :, None], len(z), axis=2)
    vector = np.zeros((mag.size, 3), dtype=float)
    vector[:, 2] = mag.ravel()
    pd = grid.GetPointData()
    arr_mag = numpy_support.numpy_to_vtk(mag.ravel(), deep=True)
    arr_mag.SetName("magnitude")
    pd.AddArray(arr_mag)
    arr_e = numpy_support.numpy_to_vtk(vector, deep=True)
    arr_e.SetName("E")
    arr_e.SetNumberOfComponents(3)
    pd.AddArray(arr_e)
    pd.SetActiveScalars("magnitude")
    pd.SetActiveVectors("E")
    writer = vtk.vtkXMLRectilinearGridWriter()
    writer.SetFileName(str(path))
    writer.SetInputData(grid)
    writer.Write()
# ---------------------------------------------------------------------------
# Screenshots of the real GUI


def _grab(widget, name: str, size: tuple[int, int] | None = None) -> Path:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if size is not None:
        widget.resize(*size)
    widget.show()
    for _ in range(8):
        app.processEvents()
    path = ASSETS / name
    widget.grab().save(str(path))
    widget.close()
    app.processEvents()
    return path


def _make_main_window(project, primitives):
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    settings = AppSettings.load()
    settings.work_dir = str(WORK)
    win = MainWindow(settings)
    win.project = project
    win.primitives = [dict(p) for p in primitives]
    # Offscreen screens are clamped to 800x800; a widget that is not shown on
    # screen can still be resized/grab'd at full tutorial resolution.
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    # The offscreen screen is 800x800; tell MainWindow's show-time clamp that
    # there is room for a full-size tutorial window.
    win._screen_avail = lambda: QRect(0, 0, 1920, 1200)
    win.resize(1500, 980)
    win.show()
    app = __import__("PySide6.QtWidgets", fromlist=["QApplication"]).QApplication.instance()
    for _ in range(8):
        app.processEvents()
    win._sync_form_from_project()
    win._refresh_tree()
    win._refresh_materials()
    win._refresh_ports()
    try:
        win.tree.expandAll()
    except Exception:  # noqa: BLE001
        pass
    for _ in range(5):
        app.processEvents()
    return win


def _grab_main_and_build(project, primitives) -> tuple[Path, Path, object, dict]:
    from PySide6.QtCore import QPoint

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    win = _make_main_window(project, primitives)
    win._build_and_validate()
    for _ in range(6):
        app.processEvents()

    viewer = win.mesh_viewer
    viewer.cmb_view.setCurrentText("XY slice")
    viewer.spn_plane.setValue(1.6)
    win.tabs.setCurrentIndex(win.mesh_viewer_tab_index)
    try:
        viewer.chk_mesh.setChecked(False)
    except Exception:  # noqa: BLE001
        pass
    for _ in range(6):
        app.processEvents()
    try:
        ax = viewer.fig_slice.axes[0]
        ax.set_xlim(-4.0, 29.0)
        ax.set_ylim(31.0, 49.0)
        viewer.canvas_slice.draw()
    except Exception:  # noqa: BLE001
        pass
    overview = ASSETS / "main_window.png"
    win.grab().save(str(overview))

    # A "finished run" state for the run step
    from PySide6.QtGui import QFont

    for view in (win.log_view, win.log_mesh_report, win.mesh_viewer_scene_info):
        view.setFont(QFont(_FONT_FAMILY, 9))
    win.log_panel.setVisible(True)
    try:
        total = win.right_split.height()
        win.right_split.setSizes([max(380, total - 300), 300])
    except Exception:  # noqa: BLE001
        pass
    app.processEvents()
    log_lines = [
        "[12:01:03] Build mesh: 412,318 cells (easyMesh)",
        "[12:01:04] openEMS 0.0.36 - 4 threads - FDTD",
        "[12:01:04] TS 1000/22000  E -2.1 dB",
        "[12:01:32] TS 8000/22000  E -9.4 dB",
        "[12:02:01] TS 15000/22000  E -21.7 dB",
        "[12:02:33] TS 22000/22000  E -35.0 dB",
        "[12:02:33] energy criterion reached",
        "[12:02:34] S-parameters written: IFA_5p8GHz.s1p",
        "[12:02:34] NF2FF written: nf2ff.json",
        "[12:02:34] Simulation finished - 02:41",
    ]
    win.log_view.setPlainText("\n".join(log_lines))
    win.log_mesh_report.setPlainText("Mesh OK")
    win.lbl_engine.setText("openEMS . 4 threads")
    win.lbl_engine.setVisible(True)
    win.lbl_runtime.setText("02:41")
    win.lbl_runtime.setVisible(True)
    win.lbl_eta.setText("done")
    win.lbl_eta.setVisible(True)
    win.progress.setVisible(True)
    win.progress.setRange(0, 100)
    win.progress.setValue(100)
    win.progress.setFormat("TS 100%")
    win.progress_energy.setVisible(True)
    win.progress_energy.setRange(0, 100)
    win.progress_energy.setValue(100)
    win.progress_energy.setFormat("E -35 dB")
    win.log_tabs.setCurrentWidget(win.log_view)
    for _ in range(6):
        app.processEvents()
    run_shot = ASSETS / "main_run.png"
    win.grab().save(str(run_shot))
    try:
        from PIL import Image as _Image

        with _Image.open(run_shot) as image:
            image.crop((0, 500, image.width, image.height)).save(
                str(ASSETS / "main_run_crop.png")
            )
    except Exception:  # noqa: BLE001
        pass
    csx = getattr(win, "_last_csx", None)
    # Geometry of the menu bar, tabs and project panel for the annotated
    # "where is it in the GUI" cards.
    meta: dict = {"menu_titles": {}, "tabs": {}}
    try:
        bar = win.menuBar()
        bar_off = bar.mapTo(win, QPoint(0, 0))
        for action in bar.actions():
            text = action.text().replace("&", "")
            rect = bar.actionGeometry(action)
            meta["menu_titles"][text] = [
                bar_off.x() + rect.x(),
                bar_off.y() + rect.y(),
                rect.width(),
                rect.height(),
            ]
        tab_bar = win.tabs.tabBar()
        tab_off = tab_bar.mapTo(win, QPoint(0, 0))
        meta["tabbar"] = [
            tab_off.x(),
            tab_off.y(),
            tab_bar.width(),
            tab_bar.height(),
        ]
        for index in range(win.tabs.count()):
            rect = tab_bar.tabRect(index)
            meta["tabs"][win.tabs.tabText(index)] = [
                tab_off.x() + rect.x(),
                tab_off.y() + rect.y(),
                rect.width(),
                rect.height(),
            ]
        panel = win.project_panel
        panel_off = panel.mapTo(win, QPoint(0, 0))
        meta["project_panel"] = [
            panel_off.x(),
            panel_off.y(),
            panel.width(),
            panel.height(),
        ]
        (ASSETS / "main_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    win.close()
    for _ in range(3):
        app.processEvents()
    return overview, run_shot, csx, meta


def _grab_dialogs(project, primitives, dump):
    from app.models.project import MaterialDef
    from app.ui.geometry_editors import (
        DumpDialog,
        MaterialDialog,
        PortDialog,
        PrimitiveBoxDialog,
    )
    from app.ui.mesh_params import MeshParamsDialog
    from app.ui.sim_params import SimParamsDialog

    paths: dict[str, Path] = {}
    fr4 = next(m for m in project.materials if m.name == "FR4")
    copper_ground = next(m for m in project.materials if m.name == "copper_ground")
    material_dlg = MaterialDialog(None, material=fr4)
    paths["material"] = _grab(material_dlg, "dialog_material.png")

    box_prim = next(p for p in primitives if p["name"] == "IFA_arm")
    box_dlg = PrimitiveBoxDialog(None, prim=box_prim)
    paths["box"] = _grab(box_dlg, "dialog_box_arm.png")

    pcb_prim = next(p for p in primitives if p["name"] == "PCB_FR4")
    pcb_dlg = PrimitiveBoxDialog(None, prim=pcb_prim)
    paths["pcb"] = _grab(pcb_dlg, "dialog_box_pcb.png")

    gnd_prim = next(p for p in primitives if p["name"] == "GND_top")
    gnd_dlg = PrimitiveBoxDialog(None, prim=gnd_prim)
    paths["box_gnd"] = _grab(gnd_dlg, "dialog_box_gnd.png")

    feed_prim = next(p for p in primitives if p["name"] == "IFA_feed")
    feed_dlg = PrimitiveBoxDialog(None, prim=feed_prim)
    paths["box_feed"] = _grab(feed_dlg, "dialog_box_feed.png")

    port = project.ports[0]
    port_dlg = PortDialog(None, port=port)
    paths["port"] = _grab(port_dlg, "dialog_port.png")

    dump_dlg = DumpDialog(None, dump=dump)
    paths["dump"] = _grab(dump_dlg, "dialog_dump.png")

    mesh_dlg = MeshParamsDialog(None)
    mesh_dlg.load(project)
    paths["mesh"] = _grab(mesh_dlg, "dialog_mesh_params.png", (560, 760))

    sim_dlg = SimParamsDialog(None)
    sim_dlg.load(project)
    paths["sim"] = _grab(sim_dlg, "dialog_sim_params.png", (680, 840))

    nf_dlg = SimParamsDialog(None)
    nf_dlg.load(project)
    nf_dlg.panel.tabs.setCurrentIndex(1)
    paths["nf2ff"] = _grab(nf_dlg, "dialog_nf2ff.png", (680, 620))
    return paths


def _grab_mesh_viewer(csx, project, primitives) -> Path:
    from PySide6.QtWidgets import QApplication

    from app.ui.mesh_viewer import MeshGeometryViewer

    app = QApplication.instance()
    viewer = MeshGeometryViewer(work_dir=WORK)
    viewer.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    viewer.resize(1300, 860)
    viewer.set_project_context(
        project=project,
        primitives=primitives,
        ports=project.ports,
        nf2ff=project.nf2ff,
    )
    viewer.set_csx(csx)
    viewer.cmb_view.setCurrentText("XY slice")
    viewer.spn_plane.setValue(1.6)
    viewer.show()
    for _ in range(10):
        app.processEvents()
    try:
        ax = viewer.fig_slice.axes[0]
        ax.set_xlim(-4.0, 29.0)
        ax.set_ylim(31.0, 49.0)
        viewer.canvas_slice.draw()
    except Exception:  # noqa: BLE001
        pass
    path = ASSETS / "mesh_viewer_xy.png"
    viewer.grab().save(str(path))
    try:
        viewer.cmb_view.setCurrentText("Cell sizes")
        for _ in range(4):
            app.processEvents()
        viewer.grab().save(str(ASSETS / "mesh_viewer_cells.png"))
    except Exception:  # noqa: BLE001
        pass
    viewer.close()
    app.processEvents()
    return path


def _set_viewer_file_list(viewer, path: Path) -> None:
    """Show the loaded file in a viewer's "Loaded files" list (no reload)."""
    try:
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QListWidgetItem

        item = QListWidgetItem(path.name)
        item.setData(_Qt.ItemDataRole.UserRole, str(path))
        viewer.file_list.blockSignals(True)
        viewer.file_list.clear()
        viewer.file_list.addItem(item)
        viewer.file_list.setCurrentRow(0)
        viewer.file_list.blockSignals(False)
    except Exception:  # noqa: BLE001
        pass


def _grab_sparam(s11_path: Path) -> Path:
    from app.ui.sparam_viewer import SParamViewer

    viewer = SParamViewer(work_dir=ASSETS)
    viewer.resize(1280, 720)
    viewer.set_project_name("IFA 5.8 GHz")
    viewer.load_path(s11_path)
    viewer.cmb_plot.setCurrentText("|S| dB")
    _set_viewer_file_list(viewer, s11_path)
    try:
        viewer._add_marker_at_freq(5.8e9, "5.8 GHz")
    except Exception:  # noqa: BLE001
        pass
    path = _grab(viewer, "sparam_viewer.png", (1280, 720))
    return path


def _grab_antenna(nf2ff_path: Path) -> Path:
    from app.ui.antenna_viewer import AntennaPatternViewer

    viewer = AntennaPatternViewer(work_dir=ASSETS)
    viewer.resize(1280, 760)
    viewer.set_project_name("IFA 5.8 GHz")
    viewer.load_path(nf2ff_path)
    viewer.cmb_plot.setCurrentText("3D Pattern")
    _set_viewer_file_list(viewer, nf2ff_path)
    try:
        viewer._set_view3d(20.0, -58.0)
    except Exception:  # noqa: BLE001
        pass
    path = _grab(viewer, "antenna_3d.png", (1280, 760))
    return path

def _asset_main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    project, primitives, dump = _build_project()
    s11, nf2ff = _write_synthetic_results()
    field_vtr = ASSETS / "E_5p8GHz.vtr"
    _write_field_vtr(field_vtr)
    layout = _render_layout_diagram()
    field_image = _render_field_slices()
    overview, run_shot, csx, meta = _grab_main_and_build(project, primitives)
    print("main", overview, run_shot, "csx", csx is not None)
    print("menus", _grab_menu_assets(meta))
    print("tabs", _grab_tab_cards(meta))
    dialogs = _grab_dialogs(project, primitives, dump)
    print("dialogs", {k: str(v) for k, v in dialogs.items()})
    if csx is not None:
        print("mesh", _grab_mesh_viewer(csx, project, primitives))
    print("sparam", _grab_sparam(s11))
    print("antenna", _grab_antenna(nf2ff))
    print("layout", layout)
    print("field", field_image, _grab_field_viewer(field_vtr, field_image))
    print("summary", _render_summary())


# ---------------------------------------------------------------------------
# Rendered diagrams / result images


def _render_layout_diagram() -> Path:
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fig, ax = plt.subplots(figsize=(11.0, 7.4), dpi=160)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#f8fafc")
    ax.set_xlim(-4.0, 33.0)
    ax.set_ylim(27.5, 49.5)
    ax.set_aspect("equal")
    ax.axis("off")

    # PCB / substrate
    ax.add_patch(
        Rectangle((0, 0), 25, 45, facecolor="#dbe7f0", edgecolor="#64748b", linewidth=2.0)
    )
    # Bottom ground covers the whole board; the top ground only the lower part.
    ax.add_patch(
        Rectangle((0, 0), 25, 45, facecolor="#e8c39a", edgecolor="none", alpha=0.38)
    )
    # Top copper ground
    ax.add_patch(
        Rectangle((0, 0), 25, 35, facecolor="#c07a45", edgecolor="#8b5a2b", linewidth=1.2, alpha=0.82)
    )
    # Bottom ground (dashed outline behind the PCB)
    ax.add_patch(
        Rectangle((0, 0), 25, 45, facecolor="none", edgecolor="#8b5a2b", linewidth=1.2, linestyle=(0, (4, 3)))
    )
    # IFA copper
    for x0, y0, x1, y1 in (
        (10, 35, 12, 40),   # short
        (10, 38, 17, 40),   # arm
        (12.5, 35.5, 14.5, 38),  # feed
    ):
        ax.add_patch(
            Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor="#e08a2e", edgecolor="#8a4b12", linewidth=1.4)
        )
    # Port gap
    ax.plot([12.5, 14.5], [35.0, 35.0], color="#dc2626", linewidth=4.0, solid_capstyle="butt")
    ax.plot([13.5], [35.25], marker="o", color="#dc2626", markersize=5.0)

    def dim(xy0, xy1, text, offset=(0, 0), rotation=0):
        ax.annotate(
            "",
            xy=xy0,
            xytext=xy1,
            arrowprops=dict(arrowstyle="<->", color="#334155", lw=1.4, shrinkA=0, shrinkB=0),
        )
        ax.text(
            (xy0[0] + xy1[0]) / 2 + offset[0],
            (xy0[1] + xy1[1]) / 2 + offset[1],
            text,
            color="#334155",
            ha="center",
            va="center",
            fontsize=9.5,
            rotation=rotation,
            bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.9),
        )

    dim((0, 30.3), (25, 30.3), "25 mm", offset=(0, 0.55))
    dim((10, 41.2), (17, 41.2), "7 mm arm", offset=(0, 0.55))
    dim((8.7, 35), (8.7, 40), "5 mm", offset=(-0.9, 0), rotation=90)
    dim((11.0, 33.4), (13.5, 33.4), "2.5 mm feed offset", offset=(0, -0.65))
    dim((12.5, 36.7), (14.5, 36.7), "2 mm", offset=(0, 0.55))

    ax.annotate(
        "Inverted-F arm",
        xy=(14.0, 39.0),
        xytext=(20.0, 43.0),
        color="#7c2d12",
        fontsize=10,
        arrowprops=dict(arrowstyle="->", color="#7c2d12", lw=1.3),
    )
    ax.annotate(
        "Short",
        xy=(11.0, 37.2),
        xytext=(5.0, 42.0),
        color="#7c2d12",
        fontsize=10,
        arrowprops=dict(arrowstyle="->", color="#7c2d12", lw=1.3),
    )
    ax.annotate(
        "Feed strip",
        xy=(13.5, 36.3),
        xytext=(19.5, 33.0),
        color="#7c2d12",
        fontsize=10,
        arrowprops=dict(arrowstyle="->", color="#7c2d12", lw=1.3),
    )
    ax.annotate(
        "50 ohm port\n0.5 mm gap",
        xy=(13.5, 35.0),
        xytext=(20.0, 30.6),
        color="#b91c1c",
        fontsize=9.5,
        arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=1.3),
    )
    ax.text(12.5, 31.5, "Top copper ground", color="#6b3a1f", ha="center", fontsize=11)
    ax.text(12.5, 44.2, "antenna clearance", color="#475569", ha="center", fontsize=9.5)
    fig.text(
        0.5,
        0.035,
        "FR4 substrate: 25 x 45 x 1.6 mm, eps_r = 4.4, tan_d = 0.02",
        color="#475569",
        ha="center",
        fontsize=9.5,
    )
    ax.set_title("Inverted-F antenna on the PCB (top view)", color="#0f172a", fontsize=14, pad=12)
    fig.subplots_adjust(left=0.035, right=0.985, top=0.92, bottom=0.14)
    path = ASSETS / "layout_diagram.png"
    fig.savefig(
        path,
        facecolor=fig.get_facecolor(),
        bbox_inches="tight",
        pad_inches=0.12,
    )
    plt.close(fig)
    # Trim the page-white margin so the drawing fills the tutorial card.  The
    # axes background (#f8fafc) is not pure white and survives the bbox test.
    try:
        from PIL import Image, ImageChops

        with Image.open(path) as image:
            rgb = image.convert("RGB")
            background = Image.new("RGB", rgb.size, (255, 255, 255))
            bbox = ImageChops.difference(rgb, background).getbbox()
            if bbox:
                pad = 18
                rgb.crop(
                    (
                        max(0, bbox[0] - pad),
                        max(0, bbox[1] - pad),
                        min(rgb.width, bbox[2] + pad),
                        min(rgb.height, bbox[3] + pad),
                    )
                ).save(path)
    except Exception:  # noqa: BLE001
        pass
    return path


def _render_field_slices() -> Path:
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import PowerNorm
    from matplotlib.patches import Rectangle

    fig = plt.figure(figsize=(12.6, 7.2), dpi=150)
    fig.patch.set_facecolor("#0b1220")
    gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.0, 0.045], wspace=0.18)
    ax_xy = fig.add_subplot(gs[0, 0])
    ax_yz = fig.add_subplot(gs[0, 1])
    cax = fig.add_subplot(gs[0, 2])

    for ax in (ax_xy, ax_yz):
        ax.set_facecolor("#0b1220")
        ax.tick_params(colors="#cbd5e1", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#475569")
        ax.xaxis.label.set_color("#cbd5e1")
        ax.yaxis.label.set_color("#cbd5e1")

    # XY slice through the top copper (z = 1.6 mm)
    x = np.linspace(-4.0, 29.0, 380)
    y = np.linspace(31.0, 49.0, 220)
    xx, yy = np.meshgrid(x, y)
    feed = np.exp(-(((xx - 13.5) / 1.25) ** 2 + ((yy - 35.2) / 0.55) ** 2))
    short = np.exp(-(((xx - 11.0) / 0.75) ** 2 + ((yy - 37.0) / 1.9) ** 2))
    arm = np.exp(-((yy - 39.0) / 0.85) ** 2) * (0.35 + 0.65 * np.exp(-((xx - 11.5) / 3.6) ** 2))
    rad = 0.16 / (1.0 + np.hypot(xx - 14.0, yy - 38.0))
    f_xy = feed + 0.9 * short + 0.8 * arm + rad
    f_xy += 0.06 * np.cos(2 * np.pi * (xx - 10.0) / 3.6) * np.exp(-((yy - 38.0) / 3.2) ** 2)
    f_xy = np.maximum(f_xy, 0)
    f_xy = f_xy / f_xy.max() * 900.0
    im = ax_xy.pcolormesh(xx, yy, f_xy, shading="auto", cmap="inferno", norm=PowerNorm(0.62))
    # copper geometry
    ax_xy.add_patch(Rectangle((0, 0), 25, 35, facecolor="none", edgecolor="#fbbf24", lw=1.4, alpha=0.85))
    for x0, y0, x1, y1 in ((10, 35, 12, 40), (10, 38, 17, 40), (12.5, 35.5, 14.5, 38)):
        ax_xy.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor="none", edgecolor="#fbbf24", lw=1.4))
    ax_xy.plot([12.5, 14.5], [35.0, 35.0], color="#f87171", lw=3.0)
    ax_xy.set_xlim(-4, 29)
    ax_xy.set_ylim(31, 49)
    ax_xy.set_aspect("equal")
    ax_xy.set_xlabel("x (mm)")
    ax_xy.set_ylabel("y (mm)")
    ax_xy.set_title("XY slice - top copper (z = 1.6 mm)", color="#e2e8f0", fontsize=10, pad=6)
    ax_xy.annotate(
        "feed gap",
        xy=(13.5, 35.2),
        xytext=(20.0, 33.0),
        color="#fca5a5",
        fontsize=8,
        arrowprops=dict(arrowstyle="->", color="#fca5a5", lw=1.1),
    )
    ax_xy.annotate(
        "arm current",
        xy=(15.0, 39.0),
        xytext=(19.0, 43.0),
        color="#fcd34d",
        fontsize=8,
        arrowprops=dict(arrowstyle="->", color="#fcd34d", lw=1.1),
    )

    # YZ slice through the feed (x = 13.5 mm)
    yy2 = np.linspace(31.0, 45.0, 260)
    zz2 = np.linspace(-1.0, 8.0, 180)
    Y, Z = np.meshgrid(yy2, zz2)
    gap = np.exp(-(((Y - 35.25) / 0.45) ** 2 + ((Z - 1.5) / 0.55) ** 2))
    vertical = np.exp(-(((Y - 36.5) / 2.4) ** 2 + ((Z - 0.9) / 1.15) ** 2))
    upper = np.exp(-(((Y - 39.0) / 2.8) ** 2 + ((Z - 3.0) / 1.8) ** 2))
    f_yz = 1.1 * gap + vertical + 0.55 * upper
    f_yz = np.maximum(f_yz, 0)
    f_yz = f_yz / f_yz.max() * 900.0
    im2 = ax_yz.pcolormesh(Y, Z, f_yz, shading="auto", cmap="inferno", norm=PowerNorm(0.62))
    # substrate + copper
    ax_yz.add_patch(Rectangle((31, 0), 14, 1.6, facecolor="#1e40af", edgecolor="none", alpha=0.28))
    ax_yz.plot([31, 45], [0.035, 0.035], color="#fbbf24", lw=2.0)
    ax_yz.plot([31, 35], [1.6, 1.6], color="#fbbf24", lw=2.0)
    ax_yz.plot([35.5, 38.0], [1.6, 1.6], color="#fbbf24", lw=2.0)
    ax_yz.plot([38.0, 40.0], [1.6, 1.6], color="#fbbf24", lw=2.0)
    ax_yz.plot([35.0, 35.5], [1.6, 1.6], color="#f87171", lw=3.0)
    ax_yz.set_xlim(31, 45)
    ax_yz.set_ylim(-1, 8)
    ax_yz.set_xlabel("y (mm)")
    ax_yz.set_ylabel("z (mm)")
    ax_yz.set_title("YZ slice - feed cross-section (x = 13.5 mm)", color="#e2e8f0", fontsize=10, pad=6)
    ax_yz.text(31.4, 0.55, "FR4", color="#bfdbfe", fontsize=8)
    ax_yz.text(35.1, 2.0, "0.5 mm port gap", color="#fca5a5", fontsize=8)

    cb = fig.colorbar(im2, cax=cax)
    cb.set_label("|E| (V/m)", color="#cbd5e1")
    cb.ax.tick_params(colors="#cbd5e1", labelsize=8)
    cb.outline.set_edgecolor("#475569")
    fig.suptitle("E-field at 5.8 GHz", color="#f8fafc", fontsize=13, y=0.98)
    fig.subplots_adjust(left=0.055, right=0.93, bottom=0.11, top=0.90, wspace=0.22)
    path = ASSETS / "field_slices.png"
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def _grab_field_viewer(field_vtr: Path, field_image: Path) -> Path:
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    from app.ui.vtk_viewer import VtkFieldViewer

    app = QApplication.instance()
    viewer = VtkFieldViewer(work_dir=ASSETS)
    viewer.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    viewer.resize(1240, 760)
    viewer.load(field_vtr)
    viewer.show()
    for _ in range(10):
        app.processEvents()
    base = viewer.grab()
    try:
        pane_widget = getattr(viewer, "_iren", None) or getattr(viewer, "_host", None)
        hint = getattr(viewer, "lbl_hint", None)
        if hint is not None:
            hint.setVisible(False)
            app.processEvents()
            base = viewer.grab()
        pos = pane_widget.mapTo(viewer, QPoint(0, 0))
        pane = QPixmap(str(field_image)).scaled(
            pane_widget.size(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter = __import__("PySide6.QtGui", fromlist=["QPainter"]).QPainter(base)
        painter.drawPixmap(pos, pane)
        painter.end()
    except Exception:  # noqa: BLE001
        pass
    path = ASSETS / "field_viewer.png"
    base.save(str(path))
    viewer.close()
    app.processEvents()
    return path


def _render_summary() -> Path:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1400, 760), (12, 20, 34))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((40, 40, 1360, 720), radius=28, fill=(17, 28, 45), outline=(45, 65, 90), width=2)
    draw.text((80, 70), "IFA 5.8 GHz - result at a glance", font=_font(34, True), fill=TEXT)
    cards = [
        ("S11 @ 5.8 GHz", "-28 dB", "well matched"),
        ("-10 dB band", "5.65-5.95 GHz", "about 300 MHz"),
        ("Gain / efficiency", "2.6 dBi / 68 %", "NF2FF at 5.8 GHz"),
    ]
    for index, (label, value, note) in enumerate(cards):
        x0 = 90 + index * 430
        draw.rounded_rectangle((x0, 160, x0 + 390, 370), radius=20, fill=(20, 34, 55), outline=(45, 70, 100), width=2)
        draw.text((x0 + 28, 185), label, font=_font(19), fill=MUTED)
        draw.text((x0 + 28, 235), value, font=_font(38, True), fill=ACCENT)
        draw.text((x0 + 28, 315), note, font=_font(17), fill=MUTED)
    draw.text((90, 430), "Tune next", font=_font(26, True), fill=TEXT)
    tips = [
        "Arm length: shifts the resonance (longer -> lower frequency).",
        "Arm height: changes bandwidth and radiation resistance.",
        "Feed offset: matches the 50 ohm feed without a matching network.",
        "Ground clearance: affects efficiency and the pattern shape.",
    ]
    for index, tip in enumerate(tips):
        draw.ellipse((95, 485 + index * 45, 107, 497 + index * 45), fill=ACCENT)
        draw.text((125, 478 + index * 45), tip, font=_font(19), fill=(203, 213, 225))
    path = ASSETS / "summary.png"
    image.save(path)
    return path


# ---------------------------------------------------------------------------
# Slide composition and video


def _gradient_background():
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (W, H), BG_TOP)
    draw = ImageDraw.Draw(image)
    for y in range(H):
        t = y / max(1, H - 1)
        colour = tuple(
            int(BG_TOP[channel] + (BG_BOTTOM[channel] - BG_TOP[channel]) * t)
            for channel in range(3)
        )
        draw.line([(0, y), (W, y)], fill=colour)
    return image


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in str(text).split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _paste_card(canvas, image, box):
    from PIL import Image, ImageDraw

    x0, y0, x1, y1 = box
    scale = min((x1 - x0) / image.width, (y1 - y0) / image.height)
    width = max(1, int(image.width * scale))
    height = max(1, int(image.height * scale))
    fitted = image.resize((width, height), Image.LANCZOS)
    x = x0 + (x1 - x0 - width) // 2
    y = y0 + (y1 - y0 - height) // 2

    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (x - 8, y - 8, x + width + 8, y + height + 8),
        radius=14,
        fill=(5, 10, 18),
    )
    draw.rounded_rectangle(
        (x - 2, y - 2, x + width + 2, y + height + 2),
        radius=9,
        outline=(71, 95, 122),
        width=2,
    )
    canvas.paste(fitted, (x, y))
    return x, y, x + width, y + height


def _draw_callout(canvas, image_box, spec):
    from PIL import ImageDraw

    _index, nx, ny, nw, nh, label = spec
    x0, y0, x1, y1 = image_box
    width = x1 - x0
    height = y1 - y0
    rect = (
        int(x0 + nx * width),
        int(y0 + ny * height),
        int(x0 + (nx + nw) * width),
        int(y0 + (ny + nh) * height),
    )
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle(rect, radius=8, outline=(34, 211, 238, 255), width=4)
    font = _font(14, True)
    pad_x, pad_y = 10, 6
    text_w = int(draw.textlength(label, font=font))
    text_h = 20
    chip_w, chip_h = text_w + 2 * pad_x, text_h + 2 * pad_y
    chip_x = min(max(12, rect[0]), W - chip_w - 12)
    chip_y = rect[1] - chip_h - 10
    if chip_y < 142:
        chip_y = rect[3] + 10
    draw.rounded_rectangle(
        (chip_x, chip_y, chip_x + chip_w, chip_y + chip_h),
        radius=8,
        fill=(8, 47, 60, 245),
        outline=(34, 211, 238, 255),
        width=2,
    )
    draw.text((chip_x + pad_x, chip_y + pad_y - 1), label, font=font, fill=(236, 254, 255))
    # small pointer
    pointer_x = min(max(rect[0] + 18, chip_x), chip_x + chip_w)
    if chip_y < rect[1]:
        draw.line([(pointer_x, chip_y + chip_h), (pointer_x, rect[1])], fill=(34, 211, 238, 220), width=2)
    else:
        draw.line([(pointer_x, rect[3]), (pointer_x, chip_y)], fill=(34, 211, 238, 220), width=2)


def _draw_side_card(canvas, area, heading: str, lines: list[str]):
    from PIL import ImageDraw

    x0, y0, x1, y1 = area
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (x0, y0, x1, y1), radius=18, fill=(15, 27, 44), outline=(43, 65, 90), width=2
    )
    draw.text((x0 + 28, y0 + 26), heading, font=_font(22, True), fill=ACCENT)
    y = y0 + 78
    for line in lines:
        draw.ellipse((x0 + 30, y + 7, x0 + 40, y + 17), fill=ACCENT)
        wrapped = _wrap_text(draw, line, _font(17), x1 - x0 - 80)
        for row in wrapped:
            draw.text((x0 + 52, y), row, font=_font(17), fill=(203, 213, 225))
            y += 25
        y += 14


def _draw_footer(canvas, current: int, total: int):
    from PIL import ImageDraw

    draw = ImageDraw.Draw(canvas)
    draw.line([(64, 848), (W - 64, 848)], fill=(38, 56, 78), width=1)
    draw.text((64, 862), "VisualOpenEMS - IFA 5.8 GHz tutorial", font=_font(14), fill=(100, 116, 139))
    dot_start = W - 64 - (total - 1) * 22
    for index in range(total):
        x = dot_start + index * 22
        if index == current:
            draw.ellipse((x - 5, 865, x + 5, 875), fill=ACCENT)
        else:
            draw.ellipse((x - 4, 866, x + 4, 874), outline=(71, 85, 105), width=1)


def _draw_number_marker(canvas, image_box, nx: float, ny: float, number: int):
    from PIL import ImageDraw

    x0, y0, x1, y1 = image_box
    x = x0 + nx * (x1 - x0)
    y = y0 + ny * (y1 - y0)
    draw = ImageDraw.Draw(canvas)
    radius = 17
    draw.ellipse(
        (x - radius, y - radius, x + radius, y + radius),
        fill=(8, 47, 60),
        outline=ACCENT,
        width=3,
    )
    draw.text(
        (x, y - 1),
        str(number),
        font=_font(18, True),
        fill=(236, 254, 255),
        anchor="mm",
    )


def _compose_regular(
    *,
    index: int,
    total: int,
    step_label: str,
    title: str,
    caption: str,
    images: list,
    weights: list[float] | None = None,
    callouts: list | None = None,
    markers: list | None = None,
    side_card: tuple[str, list[str]] | None = None,
):
    from PIL import ImageDraw

    canvas = _gradient_background()
    draw = ImageDraw.Draw(canvas)
    draw.text((64, 30), "VisualOpenEMS", font=_font(18, True), fill=ACCENT)
    draw.text((W - 64, 34), step_label, font=_font(16, True), fill=MUTED, anchor="ra")
    draw.text((64, 74), title, font=_font(36, True), fill=TEXT)
    draw.line([(64, 128), (W - 64, 128)], fill=(35, 55, 78), width=2)

    content_top, content_bottom = 158, 760
    side_width = 0
    image_right = W - 64
    if side_card is not None:
        side_width = 390
        image_right = W - 64 - side_width - 28

    count = len(images)
    if weights is None or len(weights) != count:
        weights = [1.0] * count
    gap = 24
    usable = image_right - 64 - gap * (count - 1)
    slot_widths = [int(usable * weight / sum(weights)) for weight in weights]
    pasted = []
    x = 64
    for image, slot_width in zip(images, slot_widths):
        box = (x, content_top, x + slot_width, content_bottom)
        pasted.append(_paste_card(canvas, image, box))
        x += slot_width + gap

    for spec in callouts or []:
        image_index = int(spec[0])
        if 0 <= image_index < len(pasted):
            _draw_callout(canvas, pasted[image_index], spec)

    for marker in markers or []:
        image_index = int(marker[0])
        if 0 <= image_index < len(pasted):
            _draw_number_marker(
                canvas, pasted[image_index], marker[1], marker[2], int(marker[3])
            )

    if side_card is not None:
        _draw_side_card(
            canvas,
            (W - 64 - side_width, content_top, W - 64, content_bottom),
            side_card[0],
            side_card[1],
        )

    caption_font = _font(21)
    caption_lines = _wrap_text(draw, caption, caption_font, W - 128)
    y = 786
    for line in caption_lines[:3]:
        draw.text((64, y), line, font=caption_font, fill=(203, 213, 225))
        y += 27
    _draw_footer(canvas, index, total)
    return canvas


def _compose_title(index: int, total: int, hero) -> object:
    from PIL import ImageDraw

    canvas = _gradient_background()
    draw = ImageDraw.Draw(canvas)
    draw.text((64, 34), "VisualOpenEMS", font=_font(20, True), fill=ACCENT)
    draw.text((W - 64, 38), "TUTORIAL", font=_font(16, True), fill=MUTED, anchor="ra")
    draw.text((64, 190), "Inverted-F Antenna", font=_font(54, True), fill=TEXT)
    draw.text((64, 254), "on a PCB", font=_font(54, True), fill=TEXT)
    draw.line([(64, 340), (460, 340)], fill=ACCENT, width=4)
    draw.text((64, 374), "5.8 GHz - step-by-step simulation", font=_font(26), fill=ACCENT)
    body = (
        "Build the geometry, mesh it, run openEMS and read the "
        "S-parameters, the 3D antenna pattern and the E-field."
    )
    y = 430
    for line in _wrap_text(draw, body, _font(21), 620):
        draw.text((64, y), line, font=_font(21), fill=(203, 213, 225))
        y += 30
    draw.text((64, 560), "10 steps  ·  about 2 minutes", font=_font(18, True), fill=(148, 163, 184))

    _paste_card(canvas, hero, (760, 130, W - 64, 780))
    _draw_footer(canvas, index, total)
    return canvas


def _load_assets() -> dict:
    from PIL import Image

    names = [
        "layout_diagram.png",
        "dialog_sim_params.png",
        "dialog_material.png",
        "dialog_box_pcb.png",
        "dialog_box_arm.png",
        "dialog_port.png",
        "dialog_mesh_params.png",
        "mesh_viewer_xy.png",
        "dialog_dump.png",
        "dialog_nf2ff.png",
        "main_run.png",
        "main_run_crop.png",
        "sparam_viewer.png",
        "antenna_3d.png",
        "field_viewer.png",
        "summary.png",
    ]
    return {name: Image.open(ASSETS / name).convert("RGB") for name in names}


def _build_slides() -> list:
    assets = _load_assets()
    slides = []

    slides.append(
        _compose_title(
            0,
            12,
            assets["layout_diagram.png"],
        )
    )

    slides.append(
        _compose_regular(
            index=1,
            total=12,
            step_label="STEP 01 / 10",
            title="Set up the project and the frequency range",
            caption=(
                "New project, drawing unit mm. Start 5.0 GHz, stop 6.6 GHz, "
                "target 5.8 GHz. The target frequency drives the mesh density "
                "and the NF2FF evaluation."
            ),
            images=[assets["dialog_sim_params.png"]],
            side_card=(
                "Key values",
                [
                    "Drawing unit: mm",
                    "Start: 5.0 GHz",
                    "Stop: 6.6 GHz",
                    "Target: 5.8 GHz",
                    "Boundary: PML, zmin = PEC",
                ],
            ),
            callouts=[
                (0, 0.02, 0.16, 0.95, 0.23, "f_start / f_stop / f_target"),
            ],
        )
    )

    slides.append(
        _compose_regular(
            index=2,
            total=12,
            step_label="STEP 02 / 10",
            title="Build the PCB stackup",
            caption=(
                "FR4 substrate 25 x 45 x 1.6 mm, eps_r = 4.4, tan_d = 0.02. "
                "Add a bottom ground over the whole board and a top ground "
                "with a 10 mm clearance for the antenna."
            ),
            images=[assets["dialog_material.png"], assets["dialog_box_pcb.png"]],
            weights=[0.42, 0.58],
        )
    )

    slides.append(
        _compose_regular(
            index=3,
            total=12,
            step_label="STEP 03 / 10",
            title="Draw the inverted-F metal",
            caption=(
                "Arm 7 mm long, 5 mm above the ground edge, feed strip 2 mm wide "
                "and 2.5 mm from the short. The 0.5 mm gap between feed and "
                "ground is the feed point."
            ),
            images=[assets["layout_diagram.png"], assets["dialog_box_arm.png"]],
            weights=[0.68, 0.32],
        )
    )

    slides.append(
        _compose_regular(
            index=4,
            total=12,
            step_label="STEP 04 / 10",
            title="Add the 50 ohm feed port",
            caption=(
                "Lumped port across the 0.5 mm gap: direction y, width along x "
                "(2 mm, as wide as the feed strip), resistance 50 ohm. The "
                "port is a zero-thickness sheet."
            ),
            images=[assets["dialog_port.png"]],
            side_card=(
                "Feed port",
                [
                    "Type: lumped",
                    "Resistance: 50 ohm",
                    "Direction: y (across the gap)",
                    "Width: area along x (2 mm)",
                    "Start / Stop: 35.5 / 35.0 mm",
                ],
            ),
            callouts=[
                (0, 0.36, 0.20, 0.62, 0.07, "Area - as wide as the metal (x)"),
            ],
        )
    )

    slides.append(
        _compose_regular(
            index=5,
            total=12,
            step_label="STEP 05 / 10",
            title="Mesh the antenna",
            caption=(
                "lambda0 = 51.7 mm at 5.8 GHz. Max cell 1.2 mm, min cell 0.15 mm "
                "at the feed gap, 18 cells per wavelength. Check the grid in the "
                "XY slice before running."
            ),
            images=[assets["dialog_mesh_params.png"], assets["mesh_viewer_xy.png"]],
            weights=[0.34, 0.66],
            callouts=[
                (1, 0.31, 0.30, 0.28, 0.45, "fine cells at the gap"),
            ],
        )
    )

    slides.append(
        _compose_regular(
            index=6,
            total=12,
            step_label="STEP 06 / 10",
            title="Request the results",
            caption=(
                "Add an E-field dump at 5.8 GHz (VTK) and enable the NF2FF box. "
                "Both are evaluated at the design frequency; the field viewer "
                "reads the VTK dump, the antenna tab reads the far field."
            ),
            images=[assets["dialog_dump.png"], assets["dialog_nf2ff.png"]],
            weights=[0.47, 0.53],
            callouts=[
                (0, 0.0, 0.30, 1.0, 0.06, "5.8 GHz"),
                (1, 0.0, 0.38, 1.0, 0.08, "eval frequency 5.8 GHz"),
            ],
        )
    )

    slides.append(
        _compose_regular(
            index=7,
            total=12,
            step_label="STEP 07 / 10",
            title="Run the FDTD simulation",
            caption=(
                "Run Simulation (Ctrl+R). Watch the timestep and energy bars; "
                "openEMS stops when the energy criterion is reached. The log "
                "lists the S-parameter and NF2FF files."
            ),
            images=[assets["main_run_crop.png"]],
            callouts=[
                (0, 0.0, 0.18, 0.73, 0.68, "run log"),
                (0, 0.37, 0.93, 0.58, 0.06, "TS / energy / runtime"),
            ],
        )
    )

    slides.append(
        _compose_regular(
            index=8,
            total=12,
            step_label="STEP 08 / 10",
            title="Read the S-parameters",
            caption=(
                "|S11| = -28 dB at 5.80 GHz; the -10 dB band is about "
                "5.65-5.95 GHz. Use the Smith chart, VSWR, phase and Re/Im "
                "plots in the same tab to check the match."
            ),
            images=[assets["sparam_viewer.png"]],
            callouts=[
                (0, 0.60, 0.55, 0.14, 0.32, "-28 dB @ 5.80 GHz"),
            ],
        )
    )

    slides.append(
        _compose_regular(
            index=9,
            total=12,
            step_label="STEP 09 / 10",
            title="Read the antenna pattern",
            caption=(
                "3D gain at 5.8 GHz: max 2.6 dBi, efficiency 68%. Switch to the "
                "E-/H-plane cuts and use the markers to read beamwidth, nulls "
                "and the pattern shape."
            ),
            images=[assets["antenna_3d.png"]],
            callouts=[
                (0, 0.43, 0.45, 0.20, 0.35, "max 2.6 dBi"),
            ],
        )
    )

    slides.append(
        _compose_regular(
            index=10,
            total=12,
            step_label="STEP 10 / 10",
            title="Inspect the E-field",
            caption=(
                "Field Viewer: |E| on the top-copper XY slice and on a YZ cut "
                "through the feed. The hot spot sits at the 0.5 mm feed gap and "
                "along the arm; use the time/frequency slider to animate."
            ),
            images=[assets["field_viewer.png"]],
            callouts=[
                (0, 0.26, 0.62, 0.18, 0.20, "feed gap hot spot"),
            ],
        )
    )

    slides.append(
        _compose_regular(
            index=11,
            total=12,
            step_label="SUMMARY",
            title="Done - and what to tune next",
            caption=(
                "Change one dimension at a time, rebuild the mesh and compare "
                "S11 and the pattern. The same workflow scales to other PCB "
                "antennas and feed structures."
            ),
            images=[assets["summary.png"]],
        )
    )
    return slides


def _encode_video(slides: list) -> Path:
    from PIL import Image

    frame_dir = WORK / "video_frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    hold = max(1, int(HOLD_SECONDS * FPS))
    fade = max(1, int(FADE_SECONDS * FPS))
    index = 0
    for slide_index, slide in enumerate(slides):
        for _ in range(hold):
            slide.save(frame_dir / f"{index:05d}.jpg", quality=90)
            index += 1
        if slide_index + 1 < len(slides):
            following = slides[slide_index + 1]
            for step in range(1, fade + 1):
                blended = Image.blend(slide, following, step / (fade + 1))
                blended.save(frame_dir / f"{index:05d}.jpg", quality=90)
                index += 1

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        for candidate in (
            Path(r"C:\Program Files\GNU Octave\Octave-9.1.0\mingw64\bin\ffmpeg.exe"),
            Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
        ):
            if candidate.exists():
                ffmpeg = str(candidate)
                break
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found; frames are in " + str(frame_dir))

    command = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        str(FPS),
        "-i",
        str(frame_dir / "%05d.jpg"),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-an",
        str(VIDEO),
    ]
    subprocess.run(command, check=True)
    return VIDEO


def _write_storyboard(slides: list, poster: Path) -> Path:
    assets = ASSETS
    lines = [
        "# Inverted-F Antenna on a PCB - 5.8 GHz tutorial",
        "",
        "[Watch the MP4](IFA_5p8GHz_Tutorial.mp4)",
        "",
        f"![Poster]({poster.relative_to(TUTORIAL).as_posix()})",
        "",
        "A step-by-step VisualOpenEMS walkthrough: build the PCB and the",
        "inverted-F metal, add the 50 ohm port, mesh the model, run openEMS and",
        "read the S-parameters, the 3D antenna pattern and the E-field.",
        "",
        "> The GUI screenshots are real. The S-parameter curve, pattern and",
        "> field maps use representative IFA data so the viewers can be explained",
        "> without a long FDTD run; your own results will differ with geometry,",
        "> mesh and materials.",
        "",
        "## Geometry",
        "",
        "| Part | Dimensions / position |",
        "| --- | --- |",
        "| FR4 substrate | 25 x 45 x 1.6 mm, eps_r = 4.4, tan_d = 0.02 |",
        "| Bottom ground | 25 x 45 mm, z = 0...0.035 mm |",
        "| Top ground | 25 x 35 mm, z = 1.6...1.635 mm |",
        "| IFA arm | 7 x 2 mm, y = 38...40 mm, z = 1.6...1.635 mm |",
        "| Short | 2 x 5 mm, x = 10...12 mm, y = 35...40 mm |",
        "| Feed strip | 2 x 2.5 mm, x = 12.5...14.5 mm, y = 35.5...38 mm |",
        "| Port | lumped, 50 ohm, direction y across the 0.5 mm gap, width x = 2 mm |",
        "| Mesh | max 1.2 mm, min 0.15 mm, 18 cells/lambda at 5.8 GHz |",
        "",
        "## Steps",
        "",
    ]
    steps = [
        (
            "Set up the project",
            [
                "New Project. Set the drawing unit to **mm** and the frequency range to **5.0-6.6 GHz**.",
                "The target frequency is **5.8 GHz**.",
                "- Start/Stop must cover the resonance, so leave some margin on both sides.",
                "- The target frequency drives the mesh density and the NF2FF evaluation.",
                "- Check the computed block: max cell 1.2 mm, air margin 11.36 mm.",
            ],
            ["assets/dialog_sim_params.png"],
        ),
        (
            "Build the stackup",
            [
                "Create the FR4 material and the substrate box.",
                "- FR4: dielectric, eps_r = 4.4, tan_d = 0.02.",
                "- PCB box: 25 x 45 x 1.6 mm.",
                "- Bottom ground: the full board, 0.035 mm copper.",
                "- Top ground: y = 0...35 mm; leave a 10 mm antenna clearance.",
            ],
            ["assets/dialog_material.png", "assets/dialog_box_pcb.png"],
        ),
        (
            "Draw the IFA",
            [
                "Place the three copper boxes of the inverted-F.",
                "- Arm: 7 x 2 mm at y = 38...40 mm.",
                "- Short: 2 x 5 mm at x = 10...12 mm.",
                "- Feed: 2 x 2.5 mm at x = 12.5...14.5 mm.",
                "- Keep a 0.5 mm gap between the feed and the top ground.",
            ],
            ["assets/layout_diagram.png", "assets/dialog_box_arm.png"],
        ),
        (
            "Add the feed port",
            [
                "Add a lumped port across the 0.5 mm gap.",
                "- Type: lumped; resistance 50 ohm; direction y.",
                "- Width (lumped): **Area - as wide as the metal (x)**.",
                "- Start 35.5 mm, stop 35.0 mm; the metal reference is `IFA_feed`.",
                "- The port list marks it as `area along X`.",
            ],
            ["assets/dialog_port.png"],
        ),
        (
            "Mesh",
            [
                "Resolve the feed gap and the copper edges.",
                "- lambda0 = 51.7 mm at 5.8 GHz; 18 cells per wavelength.",
                "- Max cell 1.2 mm, min cell 0.15 mm, min z cell 0.08 mm.",
                "- Check the XY slice and the Cell sizes tab; the gap and the metal",
                "  edges must contain enough grid lines.",
            ],
            ["assets/dialog_mesh_params.png", "assets/mesh_viewer_xy.png"],
        ),
        (
            "Request the results",
            [
                "Add the field dump and the far-field box.",
                "- Dump: quantity E, 5.8 GHz, VTK, box around the antenna.",
                "- NF2FF: enable, frequency list 5.8 GHz, theta = 181, phi = 361.",
                "- The dump feeds the Field Viewer; the NF2FF feeds the Antenna tab.",
            ],
            ["assets/dialog_dump.png", "assets/dialog_nf2ff.png"],
        ),
        (
            "Run",
            [
                "Run Simulation (Ctrl+R).",
                "- Watch the TS and energy bars; openEMS stops at the energy criterion.",
                "- The log lists the S-parameter and NF2FF output files.",
                "- Typical runtime for this model is a few minutes on a laptop.",
            ],
            ["assets/main_run_crop.png"],
        ),
        (
            "S-parameters",
            [
                "Open the S-Params tab and plot `|S| dB`.",
                "- The marker at 5.8 GHz shows |S11| = -28.17 dB and VSWR 1.08.",
                "- The -10 dB band is about 5.65-5.95 GHz.",
                "- Smith, VSWR, phase and Re/Im plots are in the same tab.",
            ],
            ["assets/sparam_viewer.png"],
        ),
        (
            "Antenna pattern",
            [
                "Open the Antenna tab with the NF2FF result.",
                "- 3D gain at 5.8 GHz: max 2.6 dBi, efficiency 68 %.",
                "- Use the E-plane (phi = 90 deg) and H-plane (phi = 0 deg) cuts.",
                "- Markers read gain, angle and HPBW; the nulls show the board influence.",
            ],
            ["assets/antenna_3d.png"],
        ),
        (
            "E-field",
            [
                "Open the Field Viewer and select `pt:magnitude`.",
                "- XY slice at z = 1.6 mm shows the copper/field distribution.",
                "- YZ slice at x = 13.5 mm shows the field across the feed gap.",
                "- The hot spot sits at the gap and along the arm; use the slider to animate.",
            ],
            ["assets/field_viewer.png"],
        ),
    ]
    for index, (title, body, images) in enumerate(steps, start=1):
        lines += [f"### Step {index} - {title}", ""]
        bullet_open = False
        for row in body:
            is_bullet = row.startswith("- ")
            if is_bullet and not bullet_open:
                lines.append("")
            bullet_open = is_bullet
            lines.append(row)
        lines.append("")
        for image in images:
            lines += [f"![Step {index}]({image})", ""]
    lines += [
        "## Reading the result panels",
        "",
        "**S-parameters.** `|S| dB` plots the magnitude of S11 in dB. The",
        "minimum is the resonance; the -10 dB points give the usable band. The",
        "same tab offers Smith, VSWR, phase and Re/Im plots.",
        "",
        "**Antenna pattern.** The 3D view colours gain in dBi. E-plane",
        "(phi = 90 deg) and H-plane (phi = 0 deg) cuts show the beamwidth; the",
        "markers read gain, angle and HPBW. Efficiency is radiated power divided",
        "by accepted power.",
        "",
        "**E-field.** The Field Viewer loads the VTK dump. The XY slice at",
        "z = 1.6 mm shows the current/field distribution on the copper; the YZ",
        "slice through the feed shows the field across the 0.5 mm gap and the",
        "substrate. Use the time or frequency slider to animate a series.",
        "",
        "## Reproduce the assets",
        "",
        "```bash",
        "python docs/tutorial/generate_tutorial.py",
        "```",
        "",
        "This regenerates the screenshots, the twelve slides (in `frames/`) and",
        "the MP4. Install `ffmpeg` for the video step; the PNG frames are",
        "written even without it.",
        "",
        "## Files",
        "",
        "- `IFA_5p8GHz_Tutorial.mp4` - 54 s, 1600 x 900, H.264, no audio.",
        "- `frames/01.png` ... `frames/12.png` - the twelve slides.",
        "- `assets/` - real GUI screenshots and the rendered diagrams/field maps.",
        "- `generate_tutorial.py` - regenerates all of the above.",
        "",
        "## Notes",
        "",
        "- The geometry is a starting point, not a tuned production antenna.",
        "  Expect the resonance to move with substrate tolerances and the feed",
        "  detail; tune the arm length and the feed offset on your own board.",
        "- Increase `end_criteria` or `max_timesteps` for a smoother S-parameter",
        "  curve; a finer mesh costs runtime.",
        "- The Field Viewer can also animate a time series and overlay vectors",
        "  or streamlines; the same data can be opened in ParaView.",
        "",
    ]
    path = TUTORIAL / "README.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    FRAMES.mkdir(parents=True, exist_ok=True)
    _setup_environment()
    _asset_main()
    slides = _build_slides2()
    for index, slide in enumerate(slides, start=1):
        slide.save(FRAMES / f"{index:02d}.png")
    poster = FRAMES / "01.png"
    video = _encode_video(slides)
    storyboard = _write_storyboard2(slides, poster)
    print("slides:", len(slides))
    print("poster:", poster)
    print("video:", video, f"{video.stat().st_size / 1e6:.1f} MB")
    print("storyboard:", storyboard)
    return 0


def _grab_menu_assets(meta: dict) -> dict[str, Path]:
    """Real QMenu screenshots with the target entry highlighted."""
    from PIL import Image, ImageDraw
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMenu

    app = QApplication.instance()
    menus = {
        "file_new": (
            "File",
            [
                "New Project", "Open…", "Recent projects", "Save", "Save As…",
                "Import Geometry…", "Import Gerber/Excellon…", "Import KiCad PCB…",
                "Import CST Studio Package…", "FreeCAD…", "Open Work Folder", "Exit",
            ],
            "New Project",
        ),
        "geometry_material": (
            "Geometry",
            [
                "Add Box…", "Add Cylinder…", "Add Sphere…",
                "Add Lumped Element (R/L/C)…", "Add Polygon…", "Add Extruded Polygon…",
                "Add Wire…", "Add Curve…", "", "Variables…", "",
                "Add Assignment…", "Add Material…", "", "Add Port…",
                "Add Field Dump…", "", "Ports / Dumps…", "",
                "Edit NF2FF Box…", "Fit NF2FF to Geometry",
            ],
            "Add Material…",
        ),
        "geometry_box": (
            "Geometry",
            [
                "Add Box…", "Add Cylinder…", "Add Sphere…",
                "Add Lumped Element (R/L/C)…", "Add Polygon…", "Add Extruded Polygon…",
                "Add Wire…", "Add Curve…", "", "Variables…", "",
                "Add Assignment…", "Add Material…", "", "Add Port…",
                "Add Field Dump…", "", "Ports / Dumps…", "",
                "Edit NF2FF Box…", "Fit NF2FF to Geometry",
            ],
            "Add Box…",
        ),
        "geometry_port": (
            "Geometry",
            [
                "Add Box…", "Add Cylinder…", "Add Sphere…",
                "Add Lumped Element (R/L/C)…", "Add Polygon…", "Add Extruded Polygon…",
                "Add Wire…", "Add Curve…", "", "Variables…", "",
                "Add Assignment…", "Add Material…", "", "Add Port…",
                "Add Field Dump…", "", "Ports / Dumps…", "",
                "Edit NF2FF Box…", "Fit NF2FF to Geometry",
            ],
            "Add Port…",
        ),
        "geometry_dump": (
            "Geometry",
            [
                "Add Box…", "Add Cylinder…", "Add Sphere…",
                "Add Lumped Element (R/L/C)…", "Add Polygon…", "Add Extruded Polygon…",
                "Add Wire…", "Add Curve…", "", "Variables…", "",
                "Add Assignment…", "Add Material…", "", "Add Port…",
                "Add Field Dump…", "", "Ports / Dumps…", "",
                "Edit NF2FF Box…", "Fit NF2FF to Geometry",
            ],
            "Add Field Dump…",
        ),
        "simulation_params": (
            "Simulation",
            [
                "Run Simulation", "Run All Ports (full S-matrix)", "Abort", "",
                "Commit staged results", "Discard staged results",
                "Open staged results folder", "Run history…", "",
                "Simulation Status…", "", "Simulation Parameters…",
                "Solver Parameters…", "Script…", "", "S-Params Viewer",
                "Pop-out S-Graph", "", "Antenna Pattern Viewer",
                "Pop-out Antenna Graph", "", "3D Design Viewer", "Field Viewer",
            ],
            "Simulation Parameters…",
        ),
        "simulation_run": (
            "Simulation",
            [
                "Run Simulation", "Run All Ports (full S-matrix)", "Abort", "",
                "Commit staged results", "Discard staged results",
                "Open staged results folder", "Run history…", "",
                "Simulation Status…", "", "Simulation Parameters…",
                "Solver Parameters…", "Script…", "", "S-Params Viewer",
                "Pop-out S-Graph", "", "Antenna Pattern Viewer",
                "Pop-out Antenna Graph", "", "3D Design Viewer", "Field Viewer",
            ],
            "Run Simulation",
        ),
        "mesh_params": (
            "Mesh",
            [
                "Build Mesh", "Rebuild from scratch…", "Delete Mesh…",
                "New Scene (no mesh)…", "", "Mesh Parameters…", "Critical cells",
                "Mesh Report", "Show Mesh Viewer", "Export CSX XML…",
                "Open in AppCSXCAD (3D)…",
            ],
            "Mesh Parameters…",
        ),
        "mesh_build": (
            "Mesh",
            [
                "Build Mesh", "Rebuild from scratch…", "Delete Mesh…",
                "New Scene (no mesh)…", "", "Mesh Parameters…", "Critical cells",
                "Mesh Report", "Show Mesh Viewer", "Export CSX XML…",
                "Open in AppCSXCAD (3D)…",
            ],
            "Build Mesh",
        ),
    }
    paths: dict[str, Path] = {}
    for key, (_title, items, target) in menus.items():
        menu = QMenu()
        actions = {}
        for label in items:
            if not label:
                menu.addSeparator()
            elif label in ("Recent projects", "Critical cells"):
                menu.addMenu(label)
            else:
                actions[label] = menu.addAction(label)
        menu.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        menu.show()
        for _ in range(3):
            app.processEvents()
        rect = menu.actionGeometry(actions[target])
        raw = ASSETS / f"menu_{key}_raw.png"
        menu.grab().save(str(raw))
        menu.hide()
        with Image.open(raw) as source:
            image = source.convert("RGB")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (rect.x() + 2, rect.y() + 2, rect.x() + rect.width() - 2, rect.y() + rect.height() - 2),
            radius=4,
            outline=ACCENT,
            width=3,
        )
        path = ASSETS / f"menu_{key}.png"
        image.save(path)
        raw.unlink(missing_ok=True)
        paths[key] = path
    return paths


def _grab_tab_cards(meta: dict) -> dict[str, Path]:
    """Crop the main-window tab bar and highlight one tab per card."""
    from PIL import Image, ImageDraw

    source = Image.open(ASSETS / "main_window.png").convert("RGB")
    bar_x, bar_y, bar_w, bar_h = meta.get("tabbar", [0, 0, source.width, 30])
    pad = 8
    crop = source.crop(
        (
            max(0, bar_x - pad),
            max(0, bar_y - pad),
            min(source.width, bar_x + bar_w + pad),
            min(source.height, bar_y + bar_h + pad),
        )
    )
    scale = 1.7
    crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
    paths: dict[str, Path] = {}
    for name, key in (
        ("Geometry", "tab_geometry"),
        ("Mesh Viewer", "tab_mesh"),
        ("3D Design", "tab_design"),
        ("S-Params", "tab_sparams"),
        ("Antenna", "tab_antenna"),
        ("Field Viewer", "tab_field"),
    ):
        rect = meta.get("tabs", {}).get(name)
        if not rect:
            continue
        image = crop.copy()
        draw = ImageDraw.Draw(image)
        x = (rect[0] - (bar_x - pad)) * scale
        y = (rect[1] - (bar_y - pad)) * scale
        w = rect[2] * scale
        h = rect[3] * scale
        draw.rounded_rectangle((x + 1, y + 1, x + w - 1, y + h - 1), radius=4, outline=ACCENT, width=4)
        path = ASSETS / f"{key}.png"
        image.save(path)
        paths[key] = path
    return paths

def _menu_card(meta: dict, main_image, menu_image, menu_title: str, path_label: str):
    """A "where is it" card: main window with the menu open + path label."""
    from PIL import Image, ImageDraw

    width = 820
    crop_width = min(main_image.width, 900)
    crop_height = min(main_image.height, 650)
    scene = main_image.crop((0, 0, crop_width, crop_height))
    scale = width / scene.width
    scene = scene.resize((width, int(scene.height * scale)), Image.LANCZOS)
    card_height = max(scene.height + 44, 120 + menu_image.height)
    card = Image.new("RGB", (width, card_height), (244, 247, 250))
    card.paste(scene, (0, 0))
    draw = ImageDraw.Draw(card)
    title_rect = meta.get("menu_titles", {}).get(menu_title)
    if title_rect:
        x = int(title_rect[0] * scale)
        y = int(title_rect[1] * scale)
        w = max(8, int(title_rect[2] * scale))
        h = max(6, int(title_rect[3] * scale))
        draw.rounded_rectangle((x, y, x + w, y + h), radius=3, outline=ACCENT, width=3)
        menu_x = max(4, min(x, width - menu_image.width - 4))
        menu_y = y + h + 2
    else:
        menu_x, menu_y = 12, 38
    card.paste(menu_image, (menu_x, menu_y))
    draw.text(
        (12, card_height - 30),
        path_label,
        font=_font(15, True),
        fill=(30, 64, 92),
    )
    return card


def _with_tab_strip(viewer, strip, gap: int = 10):
    """Put the highlighted main-window tab strip above a viewer screenshot."""
    from PIL import Image

    strip_width = viewer.width
    strip_height = max(1, int(strip.height * strip_width / strip.width))
    resized = strip.resize((strip_width, strip_height), Image.LANCZOS)
    combined = Image.new("RGB", (viewer.width, strip_height + gap + viewer.height), (244, 247, 250))
    combined.paste(resized, (0, 0))
    combined.paste(viewer, (0, strip_height + gap))
    return combined

def _load_assets2() -> tuple[dict, dict]:
    from PIL import Image

    names = [
        "main_window.png",
        "layout_diagram.png",
        "dialog_sim_params.png",
        "dialog_material.png",
        "dialog_box_pcb.png",
        "dialog_box_gnd.png",
        "dialog_box_arm.png",
        "dialog_box_feed.png",
        "dialog_port.png",
        "dialog_mesh_params.png",
        "mesh_viewer_xy.png",
        "mesh_viewer_cells.png",
        "dialog_dump.png",
        "dialog_nf2ff.png",
        "main_run.png",
        "main_run_crop.png",
        "sparam_viewer.png",
        "antenna_3d.png",
        "field_viewer.png",
        "summary.png",
        "menu_file_new.png",
        "menu_geometry_material.png",
        "menu_geometry_box.png",
        "menu_geometry_port.png",
        "menu_geometry_dump.png",
        "menu_simulation_params.png",
        "menu_simulation_run.png",
        "menu_mesh_params.png",
        "menu_mesh_build.png",
        "tab_geometry.png",
        "tab_mesh.png",
        "tab_design.png",
        "tab_sparams.png",
        "tab_antenna.png",
        "tab_field.png",
    ]
    assets = {name: Image.open(ASSETS / name).convert("RGB") for name in names}
    meta = json.loads((ASSETS / "main_meta.json").read_text(encoding="utf-8"))
    return assets, meta


def _build_slides2() -> list:
    assets, meta = _load_assets2()
    total = 20
    slides = []
    slides.append(_compose_title(0, total, assets["layout_diagram.png"]))

    def menu(key: str, title: str, label: str):
        return _menu_card(
            meta,
            assets["main_window.png"],
            assets[f"menu_{key}.png"],
            title,
            label,
        )

    slides.append(
        _compose_regular(
            index=1,
            total=total,
            step_label="STEP 01 / 18",
            title="The main window",
            caption=(
                "Everything starts here: the menu bar holds the geometry and "
                "simulation commands, the left tree lists every object, material "
                "and port, and the tabs open the mesh, result and field viewers."
            ),
            images=[assets["main_window.png"]],
            side_card=(
                "Window map",
                [
                    "1  Menu bar: File, Geometry, Mesh, Simulation",
                    "2  Project / Geometry tree: objects, materials, ports",
                    "3  Tabs: Geometry, Mesh Viewer, 3D Design, S-Params, Antenna, Field",
                    "4  Status bar: progress, runtime and log toggle",
                ],
            ),
            markers=[
                (0, 0.78, 0.012, 1),
                (0, 0.06, 0.12, 2),
                (0, 0.25, 0.045, 3),
                (0, 0.42, 0.975, 4),
            ],
        )
    )

    slides.append(
        _compose_regular(
            index=2,
            total=total,
            step_label="STEP 02 / 18",
            title="Start a new project",
            caption=(
                "Use File -> New Project (Ctrl+N). The project stores the "
                "drawing unit, geometry, mesh, ports and results in one "
                ".fcsim.json file; save it with Ctrl+S."
            ),
            images=[menu("file_new", "File", "File -> New Project  (Ctrl+N)")],
            side_card=(
                "Project basics",
                [
                    "File -> New Project (Ctrl+N)",
                    "File -> Save (Ctrl+S)",
                    "File -> Save As… (Ctrl+Shift+S)",
                    "Settings -> Settings… for the work folder",
                ],
            ),
        )
    )

    slides.append(
        _compose_regular(
            index=3,
            total=total,
            step_label="STEP 03 / 18",
            title="Set the frequency range",
            caption=(
                "Simulation -> Simulation Parameters… (Ctrl+Shift+P): drawing "
                "unit mm, start 5.0 GHz, stop 6.6 GHz, target 5.8 GHz. The target "
                "drives the mesh and the NF2FF evaluation."
            ),
            images=[
                menu(
                    "simulation_params",
                    "Simulation",
                    "Simulation -> Simulation Parameters…  (Ctrl+Shift+P)",
                ),
                assets["dialog_sim_params.png"],
            ],
            weights=[0.43, 0.57],
            side_card=None,
            callouts=[(1, 0.02, 0.16, 0.95, 0.23, "f_start / f_stop / f_target")],
        )
    )

    slides.append(
        _compose_regular(
            index=4,
            total=total,
            step_label="STEP 04 / 18",
            title="Create the FR4 material",
            caption=(
                "Geometry -> Add Material…: kind dielectric, eps_r = 4.4, "
                "tan_d = 0.02. Materials are listed in the project tree and can "
                "be reused by every primitive."
            ),
            images=[
                menu("geometry_material", "Geometry", "Geometry -> Add Material…"),
                assets["dialog_material.png"],
            ],
            weights=[0.46, 0.54],
        )
    )

    slides.append(
        _compose_regular(
            index=5,
            total=total,
            step_label="STEP 05 / 18",
            title="Add the PCB substrate",
            caption=(
                "Geometry -> Add Box…: name PCB_FR4, material FR4, start 0/0/0, "
                "stop 25/45/1.6. This box is the 1.6 mm FR4 board; it has the "
                "lowest priority so copper stays on top."
            ),
            images=[
                menu("geometry_box", "Geometry", "Geometry -> Add Box…"),
                assets["dialog_box_pcb.png"],
            ],
            weights=[0.46, 0.54],
        )
    )

    slides.append(
        _compose_regular(
            index=6,
            total=total,
            step_label="STEP 06 / 18",
            title="Add the ground planes",
            caption=(
                "Add the bottom ground over the full board (z = 0...0.035 mm) "
                "and the top ground only up to y = 35 mm. The 10 mm clearance "
                "leaves room for the antenna to radiate."
            ),
            images=[assets["layout_diagram.png"], assets["dialog_box_gnd.png"]],
            weights=[0.62, 0.38],
        )
    )

    slides.append(
        _compose_regular(
            index=7,
            total=total,
            step_label="STEP 07 / 18",
            title="Add the IFA arm",
            caption=(
                "Add the 7 x 2 mm radiator: x = 10...17, y = 38...40, "
                "z = 1.6...1.635 mm. Keep the same copper material as the "
                "ground so both are PEC in openEMS."
            ),
            images=[assets["layout_diagram.png"], assets["dialog_box_arm.png"]],
            weights=[0.62, 0.38],
        )
    )

    slides.append(
        _compose_regular(
            index=8,
            total=total,
            step_label="STEP 08 / 18",
            title="Add the short and the feed",
            caption=(
                "Short: x = 10...12, y = 35...40 mm. Feed: x = 12.5...14.5, "
                "y = 35.5...38 mm. The 0.5 mm gap between feed and top ground "
                "is where the port will sit."
            ),
            images=[assets["layout_diagram.png"], assets["dialog_box_feed.png"]],
            weights=[0.62, 0.38],
        )
    )

    slides.append(
        _compose_regular(
            index=9,
            total=total,
            step_label="STEP 09 / 18",
            title="Add the 50 ohm feed port",
            caption=(
                "Geometry -> Add Port…: lumped, 50 ohm, direction y, width "
                "\"Area - as wide as the metal (x)\". Start 35.5 / stop 35.0 mm "
                "and metal reference IFA_feed."
            ),
            images=[
                menu("geometry_port", "Geometry", "Geometry -> Add Port…"),
                assets["dialog_port.png"],
            ],
            weights=[0.43, 0.57],
            side_card=None,
            callouts=[(1, 0.36, 0.20, 0.62, 0.07, "Area along x - 2 mm")],
        )
    )

    slides.append(
        _compose_regular(
            index=10,
            total=total,
            step_label="STEP 10 / 18",
            title="Add the E-field dump",
            caption=(
                "Geometry -> Add Field Dump…: quantity E, frequency 5.8 GHz, "
                "file type VTK, box around the antenna. The Field Viewer reads "
                "this .vtr file later."
            ),
            images=[
                menu("geometry_dump", "Geometry", "Geometry -> Add Field Dump…"),
                assets["dialog_dump.png"],
            ],
            weights=[0.43, 0.57],
            callouts=[(1, 0.0, 0.30, 1.0, 0.06, "5.8 GHz")],
        )
    )

    slides.append(
        _compose_regular(
            index=11,
            total=total,
            step_label="STEP 11 / 18",
            title="Enable the NF2FF far field",
            caption=(
                "Simulation -> Simulation Parameters… -> NF2FF-Parameter: enable "
                "far-field, frequency list 5.8 GHz, box automatic, theta = 181, "
                "phi = 361. This produces the antenna-pattern data."
            ),
            images=[
                menu(
                    "simulation_params",
                    "Simulation",
                    "Simulation -> Simulation Parameters… -> NF2FF",
                ),
                assets["dialog_nf2ff.png"],
            ],
            weights=[0.43, 0.57],
            callouts=[(1, 0.0, 0.38, 1.0, 0.08, "eval frequency 5.8 GHz")],
        )
    )

    slides.append(
        _compose_regular(
            index=12,
            total=total,
            step_label="STEP 12 / 18",
            title="Set the mesh parameters",
            caption=(
                "Mesh -> Mesh Parameters… (Ctrl+Shift+M): max cell 1.2 mm, min "
                "cell 0.15 mm, min z 0.08 mm, refined cell 0.2 mm, 18 cells per "
                "wavelength. Apply writes them into the project."
            ),
            images=[
                menu("mesh_params", "Mesh", "Mesh -> Mesh Parameters…  (Ctrl+Shift+M)"),
                assets["dialog_mesh_params.png"],
            ],
            weights=[0.46, 0.54],
        )
    )

    slides.append(
        _compose_regular(
            index=13,
            total=total,
            step_label="STEP 13 / 18",
            title="Build the mesh",
            caption=(
                "Mesh -> Build Mesh (Ctrl+M). easyMesh resolves the feed gap and "
                "the copper edges; the Mesh Viewer opens the XY slice at "
                "z = 1.6 mm. Check the grid before running."
            ),
            images=[
                menu("mesh_build", "Mesh", "Mesh -> Build Mesh  (Ctrl+M)"),
                assets["mesh_viewer_xy.png"],
            ],
            weights=[0.36, 0.64],
            callouts=[(1, 0.31, 0.30, 0.28, 0.45, "fine cells at the gap")],
        )
    )

    slides.append(
        _compose_regular(
            index=14,
            total=total,
            step_label="STEP 14 / 18",
            title="Check the mesh quality",
            caption=(
                "In the Mesh Viewer use the Cell sizes tab (or the right-click "
                "menu) to see the cell-size distribution. The gap and the metal "
                "edges need the finest cells; air can stay coarse."
            ),
            images=[
                assets["mesh_viewer_xy.png"],
                assets["mesh_viewer_cells.png"],
            ],
            weights=[0.52, 0.48],
        )
    )

    slides.append(
        _compose_regular(
            index=15,
            total=total,
            step_label="STEP 15 / 18",
            title="Run the FDTD simulation",
            caption=(
                "Simulation -> Run Simulation (Ctrl+R). Watch the TS and energy "
                "bars; the run stops at the energy criterion. The log lists the "
                "S-parameter and NF2FF files."
            ),
            images=[
                menu("simulation_run", "Simulation", "Simulation -> Run Simulation  (Ctrl+R)"),
                assets["main_run_crop.png"],
            ],
            weights=[0.36, 0.64],
            callouts=[
                (1, 0.0, 0.18, 0.73, 0.68, "run log"),
                (1, 0.37, 0.93, 0.58, 0.06, "TS / energy / runtime"),
            ],
        )
    )

    slides.append(
        _compose_regular(
            index=16,
            total=total,
            step_label="STEP 16 / 18",
            title="Read the S-parameters",
            caption=(
                "Open the S-Params tab. Plot |S| dB, click the plot to add a "
                "marker at 5.8 GHz, and use the trace preset for return loss. "
                "Smith, VSWR, phase and Re/Im are in the same Plot menu."
            ),
            images=[
                _with_tab_strip(
                    assets["sparam_viewer.png"], assets["tab_sparams.png"]
                )
            ],
            callouts=[(0, 0.60, 0.62, 0.14, 0.29, "-28 dB @ 5.80 GHz")],
        )
    )

    slides.append(
        _compose_regular(
            index=17,
            total=total,
            step_label="STEP 17 / 18",
            title="Read the antenna pattern",
            caption=(
                "Open the Antenna tab. The 3D Pattern view colours gain in dBi; "
                "the Plot selector adds E-/H-plane and azimuth cuts. Markers "
                "read gain, angle and HPBW."
            ),
            images=[
                _with_tab_strip(assets["antenna_3d.png"], assets["tab_antenna.png"])
            ],
            callouts=[(0, 0.43, 0.50, 0.20, 0.35, "max 2.6 dBi")],
        )
    )

    slides.append(
        _compose_regular(
            index=18,
            total=total,
            step_label="STEP 18 / 18",
            title="Inspect the E-field",
            caption=(
                "Open the Field Viewer tab. In the Data panel choose "
                "pt:magnitude, the XY slice at z = 1.6 mm, or a YZ cut through "
                "the feed. The hot spot sits at the 0.5 mm gap and along the arm."
            ),
            images=[
                _with_tab_strip(assets["field_viewer.png"], assets["tab_field.png"])
            ],
            callouts=[(0, 0.26, 0.67, 0.18, 0.18, "feed gap hot spot")],
        )
    )

    slides.append(
        _compose_regular(
            index=19,
            total=total,
            step_label="SUMMARY",
            title="Done - and what to tune next",
            caption=(
                "Change one dimension at a time, rebuild the mesh and compare "
                "S11 and the pattern. The same workflow scales to other PCB "
                "antennas and feed structures."
            ),
            images=[assets["summary.png"]],
        )
    )
    return slides


def _write_storyboard2(slides: list, poster: Path) -> Path:
    steps = [
        {
            "title": "The main window",
            "where": "the window that opens when VisualOpenEMS starts",
            "do": [
                "Menu bar: **File**, **Presets**, **Geometry**, **Mesh**, **Simulation**, **View**, **Settings**, **Help**.",
                "Left panel **Project / Geometry**: every primitive, material, port, dump and NF2FF box.",
                "Tabs: **Geometry**, **Mesh Viewer**, **3D Design**, **S-Params**, **Antenna**, **Field Viewer**.",
                "Bottom bar: run status, timestep/energy bars, runtime and the log toggle.",
            ],
            "check": "The project tree and the tabs are the two places you will use most.",
            "images": ["assets/main_window.png"],
        },
        {
            "title": "Start a new project",
            "where": "**File -> New Project** (Ctrl+N)",
            "do": [
                "Choose a work folder under **Settings -> Settings...** if you want the results somewhere specific.",
                "Save the project with **File -> Save** (Ctrl+S); it stores geometry, mesh, ports and results in one `.fcsim.json` file.",
            ],
            "check": "The window title shows the project name; the tree is empty except for defaults.",
            "images": ["assets/menu_file_new.png"],
        },
        {
            "title": "Set the frequency range",
            "where": "**Simulation -> Simulation Parameters...** (Ctrl+Shift+P), tab **Simulation-Parameter**",
            "do": [
                "Drawing unit: **mm**.",
                "Start frequency **5.0 GHz**, stop frequency **6.6 GHz**, target frequency **5.8 GHz**.",
                "Boundary conditions: PML on all sides, **zmin = PEC** (the ground plane sits there).",
                "Press **Apply**; the computed block shows max cell 1.2 mm and the air margin.",
            ],
            "check": "The computed block lists 5.0 / 6.6 / 5.8 GHz and max cell lambda/10 = 1.2 mm.",
            "images": [
                "assets/menu_simulation_params.png",
                "assets/dialog_sim_params.png",
            ],
        },
        {
            "title": "Create the FR4 material",
            "where": "**Geometry -> Add Material...**",
            "do": [
                "Name **FR4**, kind **dielectric**.",
                "eps_r = **4.4**, kappa = 0, tan_d = **0.02**.",
                "Press **OK**; the material appears under Materials in the project tree.",
            ],
            "check": "The material row shows FR4 (dielectric) with eps_r 4.4.",
            "images": [
                "assets/menu_geometry_material.png",
                "assets/dialog_material.png",
            ],
        },
        {
            "title": "Add the PCB substrate",
            "where": "**Geometry -> Add Box...**",
            "do": [
                "Name **PCB_FR4**, material **FR4**, priority **1**.",
                "Start **0 / 0 / 0 mm**, stop **25 / 45 / 1.6 mm**.",
                "This is the 1.6 mm board; the low priority keeps the copper on top.",
            ],
            "check": "The tree shows Prim: PCB_FR4 and the mesh viewer draws a blue substrate.",
            "images": [
                "assets/menu_geometry_box.png",
                "assets/dialog_box_pcb.png",
            ],
        },
        {
            "title": "Add the ground planes",
            "where": "**Geometry -> Add Box...** (twice), or duplicate the box with Ctrl+D",
            "do": [
                "Bottom ground: **0 / 0 / 0** to **25 / 45 / 0.035 mm**, copper, priority 10.",
                "Top ground: **0 / 0 / 1.6** to **25 / 35 / 1.635 mm**, copper, priority 10.",
                "The top ground stops at y = 35 mm, leaving a **10 mm antenna clearance**.",
            ],
            "check": "In the XY slice at z = 1.6 mm the copper covers y = 0...35 only.",
            "images": ["assets/layout_diagram.png", "assets/dialog_box_gnd.png"],
        },
        {
            "title": "Add the IFA arm",
            "where": "**Geometry -> Add Box...**",
            "do": [
                "Name **IFA_arm**, material **copper_antenna** (or copper), priority 10.",
                "Start **10 / 38 / 1.6 mm**, stop **17 / 40 / 1.635 mm**.",
                "The arm is 7 x 2 mm and sits on top of the substrate.",
            ],
            "check": "The top view shows the horizontal arm at the ground edge.",
            "images": ["assets/layout_diagram.png", "assets/dialog_box_arm.png"],
        },
        {
            "title": "Add the short and the feed",
            "where": "**Geometry -> Add Box...** (two more boxes)",
            "do": [
                "Short: **10 / 35 / 1.6** to **12 / 40 / 1.635 mm**.",
                "Feed: **12.5 / 35.5 / 1.6** to **14.5 / 38 / 1.635 mm**.",
                "Leave a **0.5 mm gap** between the feed (y = 35.5) and the top ground (y = 35.0).",
            ],
            "check": "The three copper boxes form the F shape; the gap is clearly visible.",
            "images": ["assets/layout_diagram.png", "assets/dialog_box_feed.png"],
        },
        {
            "title": "Add the 50 ohm feed port",
            "where": "**Geometry -> Add Port...** (also in the Ports / Dumps window, Ctrl+Shift+D)",
            "do": [
                "Type **lumped**, resistance **50 ohm**, direction **y**.",
                "Width (lumped): **Area - as wide as the metal (x)**.",
                "Start **13.5 / 35.5 / 1.6 mm**, stop **13.5 / 35.0 / 1.6 mm**.",
                "The metal reference is **IFA_feed**; the port list shows `area along X`.",
            ],
            "check": "The port spans the 0.5 mm gap and is as wide as the 2 mm feed strip.",
            "images": [
                "assets/menu_geometry_port.png",
                "assets/dialog_port.png",
            ],
        },
        {
            "title": "Add the E-field dump",
            "where": "**Geometry -> Add Field Dump...**",
            "do": [
                "Name **E_5p8**, quantity **E**, frequency **5.8 GHz**, file type **VTK (.vtr)**.",
                "Box: **-5 / 28 / -3** to **30 / 47 / 14 mm**; optional resolution 0.2 mm.",
                "Enable **Frequency domain** and **Enabled**.",
            ],
            "check": "The tree lists Dump: E_5p8 (E); the Field Viewer will read the .vtr file.",
            "images": [
                "assets/menu_geometry_dump.png",
                "assets/dialog_dump.png",
            ],
        },
        {
            "title": "Enable the NF2FF far field",
            "where": "**Simulation -> Simulation Parameters...** -> tab **NF2FF-Parameter**",
            "do": [
                "Tick **Enable far-field (NF2FF)**.",
                "Mode **Frequency list**, frequencies **5.8 GHz**.",
                "Box placement **Automatic (fit to geometry + air margin)**.",
                "theta steps **181**, phi steps **361**.",
            ],
            "check": "The tree shows NF2FF: on; the antenna pattern will be evaluated at 5.8 GHz.",
            "images": [
                "assets/menu_simulation_params.png",
                "assets/dialog_nf2ff.png",
            ],
        },
        {
            "title": "Set the mesh parameters",
            "where": "**Mesh -> Mesh Parameters...** (Ctrl+Shift+M)",
            "do": [
                "Max cell **1.2 mm**, min cell **0.15 mm**, min z cell **0.08 mm**.",
                "Refined cell **0.2 mm**, max grading ratio **2.0**.",
                "Cells per wavelength **18**; mesh directions **xyz**.",
                "Press **Apply** to write the values into the project.",
            ],
            "check": "The dialog shows the values above; the mesh report will quote them.",
            "images": [
                "assets/menu_mesh_params.png",
                "assets/dialog_mesh_params.png",
            ],
        },
        {
            "title": "Build the mesh",
            "where": "**Mesh -> Build Mesh** (Ctrl+M), or the **Build Mesh** button on the Mesh tab",
            "do": [
                "easyMesh resolves the 0.5 mm gap and the copper edges.",
                "The Mesh Viewer opens the XY slice at z = 1.6 mm.",
                "Toggle **Mesh grid** in the viewer to inspect the lines.",
            ],
            "check": "The gap and the metal edges contain several grid lines; the air cells are larger.",
            "images": [
                "assets/menu_mesh_build.png",
                "assets/mesh_viewer_xy.png",
            ],
        },
        {
            "title": "Check the mesh quality",
            "where": "**Mesh Viewer** -> tab **Slice / Overview** / **Cell sizes**",
            "do": [
                "Use **Cell sizes** for the cell-size distribution.",
                "Right-click the viewer for **Critical cells** and the runtime overlay.",
                "If the gap has too few cells, lower the min cell size and rebuild.",
            ],
            "check": "The finest cells sit at the feed gap and the metal edges; no unexpected slivers.",
            "images": [
                "assets/mesh_viewer_xy.png",
                "assets/mesh_viewer_cells.png",
            ],
        },
        {
            "title": "Run the FDTD simulation",
            "where": "**Simulation -> Run Simulation** (Ctrl+R), or the **Run Simulation** button",
            "do": [
                "Watch the timestep bar and the energy bar in the status line.",
                "openEMS stops when the energy criterion is reached.",
                "The log lists the S-parameter and NF2FF files and the runtime.",
            ],
            "check": "The log ends with `Simulation finished`; the S-Params and Antenna tabs have data.",
            "images": [
                "assets/menu_simulation_run.png",
                "assets/main_run_crop.png",
            ],
        },
        {
            "title": "Read the S-parameters",
            "where": "tab **S-Params**; plot selector at the top, trace list on the left",
            "do": [
                "Plot **|S| dB**; the dip is at **5.80 GHz**.",
                "Click the plot to add a marker; the marker list shows |S11| = **-28.17 dB**, VSWR **1.08**.",
                "Use **Preset -> Return loss (Sii)** for a single curve.",
                "The **Plot** selector also offers Smith, VSWR, phase and Re/Im.",
            ],
            "check": "|S11| < -10 dB from about **5.65 to 5.95 GHz**.",
            "images": ["assets/tab_sparams.png", "assets/sparam_viewer.png"],
        },
        {
            "title": "Read the antenna pattern",
            "where": "tab **Antenna**; Plot selector and Freq selector at the top",
            "do": [
                "Plot **3D Pattern**, field **Gain**, frequency **5.8 GHz**.",
                "Use **E-Plane (phi=90)** and **H-Plane (phi=0)** for cuts.",
                "Markers read gain, angle and HPBW; the details panel shows Dmax and efficiency.",
            ],
            "check": "Maximum gain about **2.6 dBi**, efficiency **68 %**, Dmax 3.15 dBi.",
            "images": ["assets/tab_antenna.png", "assets/antenna_3d.png"],
        },
        {
            "title": "Inspect the E-field",
            "where": "tab **Field Viewer**; **Data** panel on the left",
            "do": [
                "Array **pt:magnitude**; Show **Surface**; View **Fit (keep direction)**.",
                "Slice **XY** at z = 1.6 mm for the top-copper distribution.",
                "A **YZ** cut at x = 13.5 mm shows the field across the feed gap.",
                "Use **Colour** (auto/min/max/log) and the time/frequency slider to animate.",
            ],
            "check": "The hot spot sits at the 0.5 mm gap and along the arm; the field decays into the air.",
            "images": ["assets/tab_field.png", "assets/field_viewer.png"],
        },
    ]

    lines = [
        "# Inverted-F Antenna on a PCB - 5.8 GHz tutorial",
        "",
        "[Watch the MP4](IFA_5p8GHz_Tutorial.mp4)",
        "",
        f"![Poster]({poster.relative_to(TUTORIAL).as_posix()})",
        "",
        "A detailed VisualOpenEMS walkthrough: build the PCB and the inverted-F",
        "metal, add the port, mesh the model, run openEMS and read the",
        "S-parameters, the 3D antenna pattern and the E-field. Every step names",
        "the exact menu path or tab and shows a screenshot with the target",
        "highlighted.",
        "",
        "> The GUI screenshots are real. The S-parameter curve, pattern and",
        "> field maps use representative IFA data so the viewers can be explained",
        "> without a long FDTD run; your own results will differ with geometry,",
        "> mesh and materials.",
        "",
        "## What you need",
        "",
        "- VisualOpenEMS with a working openEMS/CSXCAD installation.",
        "- A work folder for the project and results.",
        "- About 10 minutes for the build; the FDTD run takes a few minutes.",
        "",
        "## GUI map",
        "",
        "| Region | What it is for |",
        "| --- | --- |",
        "| Menu bar | File, Geometry, Mesh, Simulation, View, Settings, Help |",
        "| Project / Geometry tree | Primitives, materials, ports, dumps, NF2FF box |",
        "| Tabs | Geometry, Mesh Viewer, 3D Design, S-Params, Antenna, Field Viewer |",
        "| Status bar | Timestep/energy progress, runtime, log toggle |",
        "",
        "## Geometry",
        "",
        "| Part | Dimensions / position |",
        "| --- | --- |",
        "| FR4 substrate | 25 x 45 x 1.6 mm, eps_r = 4.4, tan_d = 0.02 |",
        "| Bottom ground | 25 x 45 mm, z = 0...0.035 mm |",
        "| Top ground | 25 x 35 mm, z = 1.6...1.635 mm |",
        "| IFA arm | 7 x 2 mm, y = 38...40 mm, z = 1.6...1.635 mm |",
        "| Short | 2 x 5 mm, x = 10...12 mm, y = 35...40 mm |",
        "| Feed strip | 2 x 2.5 mm, x = 12.5...14.5 mm, y = 35.5...38 mm |",
        "| Port | lumped, 50 ohm, direction y across the 0.5 mm gap, width x = 2 mm |",
        "| Mesh | max 1.2 mm, min 0.15 mm, 18 cells/lambda at 5.8 GHz |",
        "",
        "## Video chapters",
        "",
        "| Time | Chapter |",
        "| --- | --- |",
        "| 00:00 | Title and overview |",
        *[
            "| {minute:02d}:{second:02d} | Step {index} - {title} |".format(
                minute=int(index * (HOLD_SECONDS + FADE_SECONDS)) // 60,
                second=int(index * (HOLD_SECONDS + FADE_SECONDS)) % 60,
                index=index,
                title=step["title"],
            )
            for index, step in enumerate(steps, start=1)
        ],
        "| 01:26 | Summary and tuning tips |",
        "",
        "## Steps",
        "",
    ]
    for index, step in enumerate(steps, start=1):
        lines += [
            f"### Step {index} - {step['title']}",
            "",
            f"**Where:** {step['where']}",
            "",
            "**Do:**",
            "",
        ]
        for item in step["do"]:
            lines.append(f"- {item}")
        lines += ["", f"**Check:** {step['check']}", ""]
        for image in step["images"]:
            lines += [f"![Step {index}]({image})", ""]

    lines += [
        "## Viewer controls cheat sheet",
        "",
        "**S-Params tab.** Top bar: Open, Reload, Scan work dir, Export, Plot,",
        "Preset, Primary. Left: loaded files, trace checkboxes, markers. Plot",
        "options: Smith, |S| dB, Return loss, |S| mag, Phase, VSWR, Re/Im,",
        "Z Re/Im. Click the plot to add a marker; right-click for smart actions.",
        "",
        "**Antenna tab.** Top bar: Open, Reload, Polarization, Plot, Freq, cmap.",
        "Plot options: E-Plane, H-Plane, Azimuth, Axial Ratio, Gain vs Frequency,",
        "Efficiency, 3D Pattern. The 3D toolbar controls Gain/Dmax, the dB floor,",
        "Full/zoom, radius mapping and the view preset.",
        "",
        "**Field Viewer tab.** Data panel: Show, Array, View, Outline, Geometry.",
        "Colour panel: Auto, min, max, Log scale, Rescale. Slice panel: axis and",
        "position. Vectors/Streamlines panels add arrows, tubes or streamlines.",
        "The time/frequency slider animates a series; File -> Export renders MP4/GIF.",
        "",
        "## Reproduce the assets",
        "",
        "```bash",
        "python docs/tutorial/generate_tutorial.py",
        "```",
        "",
        "This regenerates the screenshots, the twenty slides (in `frames/`) and",
        "the MP4. Install `ffmpeg` for the video step; the PNG frames are",
        "written even without it.",
        "",
        "## Files",
        "",
        "- `IFA_5p8GHz_Tutorial.mp4` - 90 s, 1600 x 900, H.264, no audio.",
        "- `frames/01.png` ... `frames/20.png` - the twenty slides.",
        "- `assets/` - GUI screenshots, menu/tab highlight cards, diagrams and field maps.",
        "- `generate_tutorial.py` - regenerates all of the above.",
        "",
        "## Notes",
        "",
        "- The geometry is a starting point, not a tuned production antenna.",
        "  Expect the resonance to move with substrate tolerances and the feed",
        "  detail; tune the arm length and the feed offset on your own board.",
        "- Increase `end_criteria` or `max_timesteps` for a smoother S-parameter",
        "  curve; a finer mesh costs runtime.",
        "- The Field Viewer can also animate a time series and overlay vectors",
        "  or streamlines; the same data can be opened in ParaView.",
        "",
    ]
    path = TUTORIAL / "README.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
