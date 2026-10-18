"""Embedded VTK viewer for openEMS field dumps (``.vtr`` rectilinear grids).

Uses the VTK Python bindings' Qt interactor (``QVTKRenderWindowInteractor``) so
the 3D view lives inside the GUI (no external ParaView needed). Shows the field
magnitude as an iso-surface (slider for the level) plus the domain outline and
an orientation-axes marker; the vector field is available as point data.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.postproc.vtk_export import dump_to_vtr, export_dumps_to_vtk


def _vtk():
    """Import vtk lazily (heavy) and make the render backend available."""
    import vtk
    import vtkmodules.vtkRenderingOpenGL2  # noqa: F401 — register OpenGL factory
    import vtkmodules.vtkInteractionStyle  # noqa: F401
    import vtkmodules.vtkRenderingFreeType  # noqa: F401

    return vtk


def _make_reader(vtk, path: Path):
    """Pick a VTK reader from the file extension (not just our own .vtr)."""
    cls = {
        ".vtr": "vtkXMLRectilinearGridReader",
        ".vti": "vtkXMLImageDataReader",
        ".vtu": "vtkXMLUnstructuredGridReader",
        ".vtp": "vtkXMLPolyDataReader",
        ".vts": "vtkXMLStructuredGridReader",
        ".vtk": "vtkGenericDataObjectReader",  # legacy .vtk
    }.get(path.suffix.lower(), "vtkXMLGenericDataObjectReader")
    return getattr(vtk, cls)()


class VtkFieldViewer(QWidget):
    """A VTK render window + a small toolbar for field dumps."""

    def __init__(self, work_dir: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.work_dir = Path(work_dir) if work_dir else None
        self._vtk = None
        self._iren = None
        self._renderer = None
        self._grid = None
        self._contour = None
        self._scalar_range = (0.0, 1.0)
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        bar = QHBoxLayout()
        self.btn_open = QPushButton("Open .vtr…")
        self.btn_open.clicked.connect(self._on_open)
        self.btn_export = QPushButton("Export project dumps → VTK")
        self.btn_export.setToolTip(
            "Convert every field dump (simulation/*.h5) of the current project to\n"
            "VTK (.vtr) in <project>/vtk and load the first one."
        )
        self.btn_export.clicked.connect(self.export_and_load)
        bar.addWidget(QLabel("Representation"))
        self.cmb_rep = QComboBox()
        self.cmb_rep.addItems(["Surface", "Iso-surface", "Wireframe"])
        self.cmb_rep.setCurrentText("Surface")  # ParaView-like default
        self.cmb_rep.setToolTip(
            "Surface = the dataset's surface coloured by the scalar (like ParaView).\n"
            "Iso-surface = a contour of the scalar (use the Iso-level slider).\n"
            "Wireframe = the surface edges."
        )
        self.cmb_rep.currentIndexChanged.connect(self._refresh_display)
        self.chk_outline = QCheckBox("Outline")
        self.chk_outline.setChecked(True)
        self.chk_outline.toggled.connect(self._refresh_display)
        self.btn_reset = QPushButton("Reset view")
        self.btn_reset.clicked.connect(self._reset_camera)
        bar.addWidget(self.btn_open)
        bar.addWidget(self.btn_export)
        bar.addWidget(self.cmb_rep)
        bar.addWidget(self.chk_outline)
        bar.addStretch(1)
        bar.addWidget(self.btn_reset)
        root.addLayout(bar)

        row = QHBoxLayout()
        row.addWidget(QLabel("Iso-level"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setValue(500)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self._on_slider)
        row.addWidget(self.slider, 1)
        self.lbl_level = QLabel("—")
        self.lbl_level.setMinimumWidth(140)
        row.addWidget(self.lbl_level)
        root.addLayout(row)

        # VTK render window (created lazily so importing vtk is deferred)
        self._host = QWidget()
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_hint = QLabel(
            "No VTK data. Use “Export project dumps → VTK” (field dumps must cover "
            "a volume/plane, not a single point) or “Open .vtr…”."
        )
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#666;")
        self._host_layout.addWidget(self.lbl_hint)
        root.addWidget(self._host, 1)

    def _ensure_render_window(self) -> bool:
        """Create the VTK Qt interactor once. Returns False if it cannot init.

        On a headless/offscreen session (or without a GPU) OpenGL may be
        unavailable; that must not crash the GUI, so failures are caught.
        """
        if self._iren is not None:
            return True
        if getattr(self, "_vtk_failed", False):
            return False
        # VTK's OpenGL render window hard-crashes on a headless/offscreen Qt
        # platform (access violation) — never create it there. The .vtr files
        # are still produced; only the embedded 3D view is unavailable.
        if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
            self._vtk_failed = True
            self.lbl_hint.setText(
                "VTK 3D view unavailable on the offscreen platform. The converted "
                ".vtr files can be opened in ParaView."
            )
            self.lbl_hint.setVisible(True)
            return False
        try:
            vtk = _vtk()
            self._vtk = vtk
            from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

            self.lbl_hint.setVisible(False)
            self._iren = QVTKRenderWindowInteractor(self._host)
            self._host_layout.addWidget(self._iren, 1)
            self._renderer = vtk.vtkRenderer()
            self._renderer.SetBackground(0.15, 0.15, 0.18)
            self._iren.GetRenderWindow().AddRenderer(self._renderer)
            self._iren.Initialize()
            return True
        except Exception as e:  # noqa: BLE001 — e.g. no OpenGL in this session
            self._vtk_failed = True
            self.lbl_hint.setText(
                "VTK 3D view unavailable in this session (OpenGL could not be "
                f"initialised): {e}\nThe converted .vtr files can still be opened "
                "in ParaView."
            )
            self.lbl_hint.setVisible(True)
            print(f"VTK viewer disabled: {e}")
            return False

    # --------------------------------------------------------------- actions
    def set_work_dir(self, work_dir: Path | None) -> None:
        self.work_dir = Path(work_dir) if work_dir else None

    def _on_open(self) -> None:
        start = str(self.work_dir) if self.work_dir else ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open VTK file",
            start,
            "VTK files (*.vtr *.vti *.vtu *.vtp *.vts *.vtk);;All files (*)",
        )
        if path:
            self.load(path)

    def export_and_load(self) -> None:
        if self.work_dir is None:
            QMessageBox.information(self, "VTK", "No work directory set.")
            return
        sim = Path(self.work_dir) / "simulation"
        if not sim.is_dir():
            QMessageBox.information(
                self, "VTK", f"No simulation folder found:\n{sim}\n\nRun a simulation first."
            )
            return
        written = export_dumps_to_vtk(sim)
        if not written:
            QMessageBox.information(
                self,
                "VTK",
                "No usable field dump found. Check that the dumps cover a volume/plane "
                "(Edit Dump: start≠stop), then run again.",
            )
            return
        self.load(written[0])
        print(f"VTK: converted {len(written)} dump(s) -> {written[0].parent}")

    def load(self, path: Path | str) -> None:
        vtk = _vtk()
        path = Path(path)
        if not path.exists():
            return
        try:
            reader = _make_reader(vtk, path)
            reader.SetFileName(str(path))
            reader.Update()
            grid = reader.GetOutput()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "VTK", f"Could not read {path.name}:\n{e}")
            return
        if grid is None or grid.GetNumberOfPoints() == 0:
            QMessageBox.warning(
                self,
                "VTK",
                f"Could not read {path.name} ({path.suffix} not supported, or file empty).",
            )
            return
        self._grid = grid
        pd = grid.GetPointData()
        # pick a scalar: prefer our own "magnitude", else the active / first array
        name = None
        if pd.GetArray("magnitude") is not None:
            name = "magnitude"
        elif pd.GetScalars() is not None:
            name = pd.GetScalars().GetName()
        elif pd.GetNumberOfArrays() > 0:
            name = pd.GetArrayName(0)
        self._scalar_name = name
        if name:
            pd.SetActiveScalars(name)
            rng = pd.GetArray(name).GetRange()
        else:
            rng = (0.0, 0.0)
        self._scalar_range = (float(rng[0]), float(rng[1]))
        self.slider.setEnabled(self._scalar_range[1] > self._scalar_range[0])
        if self._ensure_render_window():
            self._refresh_display()
            self._reset_camera()
        self._log_title(path)

    def _log_title(self, path: Path) -> None:
        n = self._grid.GetNumberOfPoints() if self._grid else 0
        rng = self._scalar_range
        name = getattr(self, "_scalar_name", None)
        if name and rng[1] > rng[0]:
            self.lbl_level.setText(f"{name} {rng[0]:.3g} … {rng[1]:.3g}  ({n} pts)")
        else:
            self.lbl_level.setText(f"{n} points")
        print(f"VTK viewer: loaded {path} ({n} points, scalar={name})")

    def _on_slider(self, _v: int) -> None:
        self._refresh_display()

    def _iso_value(self) -> float:
        lo, hi = self._scalar_range
        if hi <= lo:
            return lo
        return lo + (hi - lo) * (self.slider.value() / 1000.0)

    def _refresh_display(self, *_args) -> None:
        if self._renderer is None or self._grid is None:
            return
        vtk = self._vtk
        self._renderer.RemoveAllViewProps()

        name = getattr(self, "_scalar_name", None)
        has_scal = bool(name) and self._scalar_range[1] > self._scalar_range[0]
        rep = self.cmb_rep.currentText() if hasattr(self, "cmb_rep") else "Surface"
        # the iso slider only matters for the contour representation
        self.slider.setEnabled(rep == "Iso-surface" and has_scal)

        if rep == "Iso-surface" and has_scal:
            contour = vtk.vtkContourFilter()
            contour.SetInputData(self._grid)
            contour.SetInputArrayToProcess(0, 0, 0, 0, name)
            contour.SetValue(0, self._iso_value())
            contour.Update()
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(contour.GetOutputPort())
            mapper.SetScalarModeToUsePointFieldData()
            mapper.SelectColorArray(name)
            mapper.SetScalarRange(*self._scalar_range)
        else:
            # Surface / Wireframe: the dataset itself, coloured by the scalar
            dm = vtk.vtkDataSetMapper()
            dm.SetInputData(self._grid)
            if has_scal:
                dm.SetScalarModeToUsePointFieldData()
                dm.SelectColorArray(name)
                dm.SetScalarRange(*self._scalar_range)
                dm.ScalarVisibilityOn()
            else:
                dm.ScalarVisibilityOff()
            if rep == "Wireframe":
                dm.SetRepresentationToWireframe()
            mapper = dm
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        self._renderer.AddActor(actor)

        # ParaView-style colour bar when a scalar is shown
        if has_scal:
            bar = vtk.vtkScalarBarActor()
            bar.SetLookupTable(actor.GetMapper().GetLookupTable())
            bar.SetTitle(name)
            bar.SetNumberOfLabels(5)
            self._renderer.AddActor2D(bar)

        if self.chk_outline.isChecked():
            outline = vtk.vtkOutlineFilter()
            outline.SetInputData(self._grid)
            om = vtk.vtkPolyDataMapper()
            om.SetInputConnection(outline.GetOutputPort())
            oa = vtk.vtkActor()
            oa.SetMapper(om)
            oa.GetProperty().SetColor(0.8, 0.8, 0.8)
            self._renderer.AddActor(oa)

        axes = vtk.vtkAxesActor()
        axes.SetTotalLength(10, 10, 10)
        self._renderer.AddActor(axes)

        self._iren.GetRenderWindow().Render()

    def _reset_camera(self) -> None:
        if self._renderer is not None:
            self._renderer.ResetCamera()
            if self._iren is not None:
                self._iren.GetRenderWindow().Render()
