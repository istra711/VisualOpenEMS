"""Embedded VTK viewer for openEMS field dumps (ParaView-style controls).

Mirrors the openEMS "Field Dumps Analysis via ParaView" workflow:
  * choose the displayed field array (point/cell),
  * represent it as Surface / Wireframe / Slice / Iso-surface,
  * rescale the colour range (data range / manual min-max / logarithmic),
  * animate the time steps (Play) when a per-step series exists.

Uses VTK's Qt interactor (``QVTKRenderWindowInteractor``) so the 3D view lives
inside the GUI. The GL window is not created on a headless/offscreen platform
(VTK would crash), but the data logic still runs.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.postproc.vtk_export import dump_to_vtr, export_dumps_to_vtk  # noqa: F401

_AXIS_NORMAL = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}


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
    """A VTK render window + ParaView-style controls for field dumps."""

    def __init__(self, work_dir: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.work_dir = Path(work_dir) if work_dir else None
        self._vtk = None
        self._iren = None
        self._renderer = None
        self._grid = None
        self._data_range = (0.0, 1.0)
        self._scalar_name = None  # kept for compatibility / status text
        self._scalar_range = (0.0, 1.0)
        self._series: list[Path] = []
        self._series_labels: list[str] = []
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._on_play_tick)
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        bar = QHBoxLayout()
        self.btn_open = QPushButton("Open…")
        self.btn_open.clicked.connect(self._on_open)
        self.btn_export = QPushButton("Export project dumps → VTK")
        self.btn_export.setToolTip(
            "Convert every field dump (simulation/*.h5) of the current project to\n"
            "VTK in <project>/vtk (incl. one file per time step) and load the first."
        )
        self.btn_export.clicked.connect(self.export_and_load)
        self.cmb_rep = QComboBox()
        self.cmb_rep.addItems(["Surface", "Wireframe", "Slice", "Iso-surface"])
        self.cmb_rep.setCurrentText("Surface")  # ParaView-like default
        self.cmb_rep.currentIndexChanged.connect(self._refresh_display)
        self.cmb_array = QComboBox()
        self.cmb_array.setMinimumWidth(150)
        self.cmb_array.setToolTip("Field array to display (point or cell data).")
        self.cmb_array.currentIndexChanged.connect(self._on_array_changed)
        self.chk_outline = QCheckBox("Outline")
        self.chk_outline.setChecked(True)
        self.chk_outline.toggled.connect(self._refresh_display)
        self.btn_reset = QPushButton("Reset view")
        self.btn_reset.clicked.connect(self._reset_camera)
        for w in (self.btn_open, self.btn_export, QLabel("Representation"), self.cmb_rep,
                  QLabel("Array"), self.cmb_array, self.chk_outline):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.btn_reset)
        root.addLayout(bar)

        # slice + iso level
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Slice axis"))
        self.cmb_slice_axis = QComboBox()
        self.cmb_slice_axis.addItems(["X", "Y", "Z"])
        self.cmb_slice_axis.currentIndexChanged.connect(self._refresh_display)
        self.slider_slice = QSlider(Qt.Orientation.Horizontal)
        self.slider_slice.setRange(0, 1000)
        self.slider_slice.setValue(500)
        self.slider_slice.valueChanged.connect(self._refresh_display)
        row2.addWidget(self.cmb_slice_axis)
        row2.addWidget(self.slider_slice, 1)
        row2.addWidget(QLabel("Iso-level"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setValue(500)
        self.slider.valueChanged.connect(self._on_slider)
        row2.addWidget(self.slider, 1)
        self.lbl_level = QLabel("—")
        self.lbl_level.setMinimumWidth(160)
        row2.addWidget(self.lbl_level)
        root.addLayout(row2)

        # colour range (ParaView "Rescale to ... / log scale")
        row3 = QHBoxLayout()
        self.chk_auto = QCheckBox("Auto (data range)")
        self.chk_auto.setChecked(True)
        self.chk_auto.toggled.connect(self._on_auto_toggled)
        self.spn_min = QDoubleSpinBox()
        self.spn_max = QDoubleSpinBox()
        for s in (self.spn_min, self.spn_max):
            s.setDecimals(6)
            s.setRange(-1e30, 1e30)
            s.setEnabled(False)
            s.valueChanged.connect(self._refresh_display)
        self.chk_log = QCheckBox("Log scale")
        self.chk_log.toggled.connect(self._refresh_display)
        self.btn_rescale = QPushButton("Rescale to data range")
        self.btn_rescale.clicked.connect(self._rescale_to_data)
        row3.addWidget(QLabel("Colour"))
        row3.addWidget(self.chk_auto)
        row3.addWidget(QLabel("min"))
        row3.addWidget(self.spn_min)
        row3.addWidget(QLabel("max"))
        row3.addWidget(self.spn_max)
        row3.addWidget(self.chk_log)
        row3.addWidget(self.btn_rescale)
        row3.addStretch(1)
        root.addLayout(row3)

        # time series (animation)
        row4 = QHBoxLayout()
        self.lbl_time = QLabel("Time")
        self.slider_time = QSlider(Qt.Orientation.Horizontal)
        self.slider_time.setRange(0, 0)
        self.slider_time.valueChanged.connect(self._on_time_slider)
        self.btn_play = QPushButton("▶ Play")
        self.btn_play.setCheckable(True)
        self.btn_play.toggled.connect(self._on_play_toggled)
        self.lbl_time_info = QLabel("")
        row4.addWidget(self.lbl_time)
        row4.addWidget(self.slider_time, 1)
        row4.addWidget(self.btn_play)
        row4.addWidget(self.lbl_time_info)
        root.addLayout(row4)
        self._set_time_visible(False)

        # VTK render window (created lazily so importing vtk is deferred)
        self._host = QWidget()
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_hint = QLabel(
            "No VTK data. Use “Export project dumps → VTK” (field dumps must cover "
            "a volume/plane, not a single point) or “Open…”."
        )
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#666;")
        self._host_layout.addWidget(self.lbl_hint)
        root.addWidget(self._host, 1)

    def _set_time_visible(self, on: bool) -> None:
        for w in (self.lbl_time, self.slider_time, self.btn_play, self.lbl_time_info):
            w.setVisible(on)

    def _ensure_render_window(self) -> bool:
        """Create the VTK Qt interactor once; False if unavailable/cannot init."""
        if self._iren is not None:
            return True
        if getattr(self, "_vtk_failed", False):
            return False
        if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
            self._vtk_failed = True
            self.lbl_hint.setText(
                "VTK 3D view unavailable on the offscreen platform. The converted "
                "files can be opened in ParaView."
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
                f"VTK 3D view unavailable (OpenGL init failed): {e}\n"
                "The converted files can still be opened in ParaView."
            )
            self.lbl_hint.setVisible(True)
            print(f"VTK viewer disabled: {e}")
            return False

    # --------------------------------------------------------------- actions
    def set_work_dir(self, work_dir: Path | None) -> None:
        self.work_dir = Path(work_dir) if work_dir else None

    def _on_open(self) -> None:
        start = ""
        if self.work_dir:
            vtk_dir = Path(self.work_dir) / "vtk"
            # the converted files live in <project>/vtk — start there
            start = str(vtk_dir if vtk_dir.is_dir() else self.work_dir)
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
        written = export_dumps_to_vtk(sim, per_step=True)
        if not written:
            QMessageBox.information(
                self,
                "VTK",
                "No usable field dump found. Check that the dumps cover a volume/plane "
                "(Edit Dump: start≠stop), then run again.",
            )
            return
        self.load(written[0])
        print(f"VTK: converted {len(written)} file(s) -> {written[0].parent}")

    def load(self, path: Path | str) -> None:
        path = Path(path)
        if not path.exists():
            return
        self._detect_series(path)
        if not self._read_into_grid(path, show_errors=True):
            return
        if self._series:
            self.slider_time.blockSignals(True)
            self.slider_time.setRange(0, len(self._series) - 1)
            self.slider_time.setValue(self._series.index(path) if path in self._series else 0)
            self.slider_time.blockSignals(False)
            self._set_time_visible(True)
        else:
            self._set_time_visible(False)
        self._populate_arrays()
        if self._ensure_render_window():
            self._refresh_display()
            self._reset_camera()
        self._log_title(path)

    def _read_into_grid(self, path: Path, show_errors: bool = False) -> bool:
        vtk = _vtk()
        try:
            reader = _make_reader(vtk, path)
            reader.SetFileName(str(path))
            reader.Update()
            grid = reader.GetOutput()
        except Exception as e:  # noqa: BLE001
            if show_errors:
                QMessageBox.warning(self, "VTK", f"Could not read {path.name}:\n{e}")
            return False
        if grid is None or grid.GetNumberOfPoints() == 0:
            if show_errors:
                QMessageBox.warning(
                    self, "VTK", f"Could not read {path.name} ({path.suffix} unsupported?)."
                )
            return False
        self._grid = grid
        self._vtk = vtk
        return True

    def _detect_series(self, path: Path) -> None:
        self._series, self._series_labels = [], []
        m = re.match(r"(?P<base>.+)_(?P<idx>\d+)$", path.stem)
        if not m:
            return
        base = m.group("base")
        files = sorted(
            path.parent.glob(base + "_*.vtr"),
            key=lambda p: int(re.search(r"_(\d+)$", p.stem).group(1)),  # noqa: B023
        )
        if len(files) < 2:
            return
        labels = [p.stem.rsplit("_", 1)[1] for p in files]
        sidecar = path.parent / (base + ".series.json")
        if sidecar.exists():
            try:
                labels = list(json.loads(sidecar.read_text(encoding="utf-8")).get("labels", labels))
            except Exception:  # noqa: BLE001
                pass
        self._series = files
        self._series_labels = labels

    def _populate_arrays(self) -> None:
        self.cmb_array.blockSignals(True)
        self.cmb_array.clear()
        if self._grid is not None:
            pd = self._grid.GetPointData()
            cd = self._grid.GetCellData()
            for i in range(pd.GetNumberOfArrays()):
                self.cmb_array.addItem(f"pt:{pd.GetArrayName(i)}", ("point", pd.GetArrayName(i)))
            for i in range(cd.GetNumberOfArrays()):
                self.cmb_array.addItem(f"cell:{cd.GetArrayName(i)}", ("cell", cd.GetArrayName(i)))
            # default: our magnitude, else the first array
            idx = 0
            for i in range(self.cmb_array.count()):
                d = self.cmb_array.itemData(i)
                if d and d[1] == "magnitude":
                    idx = i
                    break
            self.cmb_array.setCurrentIndex(idx if self.cmb_array.count() else -1)
        self.cmb_array.blockSignals(False)
        self._on_array_changed()

    def _array(self):
        d = self.cmb_array.currentData()
        return d if d else (None, None)

    def _on_array_changed(self, *_a) -> None:
        loc, name = self._array()
        self._scalar_name = name
        if self._grid is None or not name:
            self._data_range = (0.0, 1.0)
        else:
            data = self._grid.GetPointData() if loc == "point" else self._grid.GetCellData()
            arr = data.GetArray(name)
            rng = arr.GetRange() if arr else (0.0, 1.0)
            self._data_range = (float(rng[0]), float(rng[1]))
        self._scalar_range = self._data_range  # compatibility alias
        self._rescale_to_data()
        self._refresh_display()

    def _rescale_to_data(self, *_a) -> None:
        lo, hi = self._data_range
        if hi <= lo:
            hi = lo + 1.0
        self.spn_min.blockSignals(True)
        self.spn_max.blockSignals(True)
        self.spn_min.setValue(lo)
        self.spn_max.setValue(hi)
        self.spn_min.blockSignals(False)
        self.spn_max.blockSignals(False)
        self._update_color_widgets()
        self._refresh_display()

    def _on_auto_toggled(self, *_a) -> None:
        self._update_color_widgets()
        self._refresh_display()

    def _update_color_widgets(self) -> None:
        man = not self.chk_auto.isChecked()
        self.spn_min.setEnabled(man)
        self.spn_max.setEnabled(man)

    def _color_range(self) -> tuple[float, float]:
        if self.chk_auto.isChecked():
            lo, hi = self._data_range
        else:
            lo, hi = float(self.spn_min.value()), float(self.spn_max.value())
        if hi <= lo:
            hi = lo + 1.0
        return lo, hi

    def _lookup_table(self):
        vtk = self._vtk
        lut = vtk.vtkLookupTable()
        lut.SetNumberOfTableValues(256)
        lut.SetHueRange(0.667, 0.0)  # blue -> red (ParaView "jet"-ish)
        lut.Build()
        lo, hi = self._color_range()
        if self.chk_log.isChecked() and lo > 0 and hi > lo:
            lut.SetScaleToLog10()
            lut.SetTableRange(lo, hi)
        else:
            lut.SetScaleToLinear()
            lut.SetTableRange(lo, hi)
        lut.Build()
        return lut

    def _on_slider(self, _v: int) -> None:
        self._refresh_display()

    def _iso_value(self) -> float:
        lo, hi = self._color_range()
        return lo + (hi - lo) * (self.slider.value() / 1000.0)

    def _slice_origin(self):
        b = self._grid.GetBounds()  # x0,x1,y0,y1,z0,z1
        axis = self.cmb_slice_axis.currentText()
        frac = self.slider_slice.value() / 1000.0
        if axis == "X":
            return (b[0] + (b[1] - b[0]) * frac, 0.0, 0.0)
        if axis == "Y":
            return (0.0, b[2] + (b[3] - b[2]) * frac, 0.0)
        return (0.0, 0.0, b[4] + (b[5] - b[4]) * frac)

    # ------------------------------------------------------------- rendering
    def _make_mapper(self, vtk, lut, loc, name):
        """Return (mapper, lut) for the current representation (None if none)."""
        if self._grid is None:
            return None
        rep = self.cmb_rep.currentText()
        if rep == "Iso-surface" and name:
            flt = vtk.vtkContourFilter()
            flt.SetInputData(self._grid)
            flt.SetInputArrayToProcess(0, 0, 0, 0, name)
            flt.SetValue(0, self._iso_value())
            flt.Update()
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(flt.GetOutputPort())
        elif rep == "Slice":
            nx, ny, nz = _AXIS_NORMAL[self.cmb_slice_axis.currentText()]
            plane = vtk.vtkPlane()
            plane.SetNormal(nx, ny, nz)
            plane.SetOrigin(*self._slice_origin())
            cutter = vtk.vtkCutter()
            cutter.SetInputData(self._grid)
            cutter.SetCutFunction(plane)
            cutter.Update()
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(cutter.GetOutputPort())
        else:
            mapper = vtk.vtkDataSetMapper()
            mapper.SetInputData(self._grid)
            if rep == "Wireframe":
                mapper.SetRepresentationToWireframe()
        if name:
            mapper.SetLookupTable(lut)
            mapper.SetScalarModeToUsePointFieldData() if loc == "point" else mapper.SetScalarModeToUseCellFieldData()
            mapper.SelectColorArray(name)
            mapper.SetScalarRange(*self._color_range())
            mapper.ScalarVisibilityOn()
        else:
            mapper.ScalarVisibilityOff()
        return mapper

    def _refresh_display(self, *_args) -> None:
        if self._renderer is None or self._grid is None:
            return
        vtk = self._vtk
        self._renderer.RemoveAllViewProps()

        rep = self.cmb_rep.currentText()
        loc, name = self._array()
        self.slider.setEnabled(rep == "Iso-surface")
        self.slider_slice.setEnabled(rep == "Slice")
        self.cmb_slice_axis.setEnabled(rep == "Slice")

        lut = self._lookup_table()
        mapper = self._make_mapper(vtk, lut, loc, name)
        if mapper is not None:
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            if name:
                actor.GetMapper().SetLookupTable(lut)
            self._renderer.AddActor(actor)
            if name:
                bar = vtk.vtkScalarBarActor()
                bar.SetLookupTable(lut)
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

        if self._iren is not None:
            self._iren.GetRenderWindow().Render()

    def _reset_camera(self) -> None:
        if self._renderer is not None:
            self._renderer.ResetCamera()
            if self._iren is not None:
                self._iren.GetRenderWindow().Render()

    # ------------------------------------------------------------ time series
    def _on_time_slider(self, idx: int) -> None:
        if not self._series or idx < 0 or idx >= len(self._series):
            return
        if self._read_into_grid(self._series[idx]):
            self._populate_arrays()  # rebuilds arrays + rescales + refreshes
            self._log_title(self._series[idx])

    def _on_play_toggled(self, on: bool) -> None:
        self.btn_play.setText("■ Stop" if on else "▶ Play")
        if on and self._series:
            self._timer.start()
        else:
            self._timer.stop()

    def _on_play_tick(self) -> None:
        if not self._series:
            return
        nxt = (self.slider_time.value() + 1) % len(self._series)
        self.slider_time.setValue(nxt)

    def _log_title(self, path: Path) -> None:
        n = self._grid.GetNumberOfPoints() if self._grid else 0
        name = self._scalar_name
        lo, hi = self._data_range
        if name:
            self.lbl_level.setText(f"{name} {lo:.3g} … {hi:.3g}  ({n} pts)")
        else:
            self.lbl_level.setText(f"{n} points")
        if self._series:
            i = self.slider_time.value()
            lab = self._series_labels[i] if i < len(self._series_labels) else str(i)
            self.lbl_time_info.setText(f"{i + 1}/{len(self._series)}  ({lab})")
        print(f"VTK viewer: loaded {path.name} ({n} points, scalar={name})")
