"""FreeCAD tools: launch GUI and export CAD → openEMS meshes."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class FreeCADToolsDialog(QDialog):
    """Launch FreeCAD GUI or export FCStd/STEP for openEMS."""

    def __init__(
        self,
        parent=None,
        *,
        freecad_cmd: str = "",
        freecad_gui: str = "",
        work_dir: str = "",
        default_source: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("FreeCAD Tools")
        self.setMinimumWidth(520)

        self._freecad_cmd = freecad_cmd
        self._freecad_gui = freecad_gui
        self._work_dir = work_dir
        self.last_source: str = ""
        self.last_out_dir: str = ""
        self.last_action: str = ""  # "launch" | "export"

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.e_cmd = QLineEdit(freecad_cmd)
        self.e_gui = QLineEdit(freecad_gui)
        self.e_work = QLineEdit(work_dir)
        form.addRow("FreeCADCmd", self.e_cmd)
        form.addRow("FreeCAD GUI", self.e_gui)
        form.addRow("Work dir", self.e_work)
        root.addLayout(form)

        # Source document
        src_row = QHBoxLayout()
        self.e_source = QLineEdit(default_source)
        self.e_source.setPlaceholderText("Path to .FCStd / .STEP / .STP (export source)")
        btn_src = QPushButton("Browse…")
        btn_src.clicked.connect(self._browse_source)
        src_row.addWidget(self.e_source, 1)
        src_row.addWidget(btn_src)
        root.addLayout(src_row)

        # Export options
        self.e_defl = QDoubleSpinBox()
        self.e_defl.setRange(0.01, 10.0)
        self.e_defl.setSingleStep(0.05)
        self.e_defl.setValue(0.1)
        self.e_defl.setToolTip("Tessellation deflection [mm] for FreeCAD STEP/STL export")
        self.e_defl.setSuffix(" mm")
        self.chk_import = QCheckBox("Import meshes into project after export")
        self.chk_import.setChecked(True)

        self.e_format = QComboBox()
        self.e_format.addItem("STEP (tessellate in app)", "step")
        self.e_format.addItem("STL (one mesh per FreeCAD object)", "stl")
        self.e_format.setToolTip(
            "STEP: keeps BREP CAD for exact re-tessellation.\n"
            "STL: uses the per-object meshes from the export (openems_export.json) — "
            "faster, works without FreeCAD at simulation time."
        )

        opt = QFormLayout()
        opt.addRow("Deflection", self.e_defl)
        opt.addRow("Import as", self.e_format)
        opt.addRow("", self.chk_import)
        root.addLayout(opt)

        self.info = QLabel(
            "Import as: STEP (tessellated later) or STL (one mesh per FreeCAD object, "
            "from openems_export.json).\n"
            "Tip: FreeCAD GUI opens with the work folder; draw solids, export STEP "
            "(or save FCStd), then Export to openEMS.\n"
            "Ports (optional): create points named PORT_<name>_start / PORT_<name>_stop "
            "(Part→Vertex), or set custom properties openems_port_start/stop on a solid, "
            "or document property openems_ports (JSON). Export/Import reads them automatically."
        )
        self.info.setWordWrap(True)
        root.addWidget(self.info)

        bb = QDialogButtonBox()
        self.btn_launch = bb.addButton("Launch FreeCAD", QDialogButtonBox.ActionRole)
        self.btn_export = bb.addButton("Export CAD…", QDialogButtonBox.ActionRole)
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.clicked.connect(self._on_button)
        root.addWidget(bb)

        # Status
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.result_source = ""
        self.result_out_dir = ""
        self.result_import = False

    def _browse_source(self) -> None:
        start = self.e_source.text().strip()
        if start and Path(start).is_file():
            start = str(Path(start).parent)
        elif self._work_dir:
            start = self._work_dir
        path, _ = QFileDialog.getOpenFileName(
            self,
            "FreeCAD source",
            start,
            "CAD (*.FCStd *.fcstd *.step *.stp *.STEP *.STP);;All files (*)",
        )
        if path:
            self.e_source.setText(path)

    def _collect(self) -> tuple[str, str, str]:
        return (
            self.e_cmd.text().strip(),
            self.e_gui.text().strip(),
            self.e_work.text().strip(),
        )

    def _on_button(self, btn) -> None:
        if btn is self.btn_launch:
            self._do_launch()
        elif btn is self.btn_export:
            self._do_export()

    def _do_launch(self) -> None:
        from app.freecad_io.freecad_launcher import launch_freecad

        cmd, gui, work = self._collect()
        src = self.e_source.text().strip() or None
        ok, msg = launch_freecad(cmd, gui, src, work)
        self.status.setText(msg)
        if ok:
            self.last_action = "launch"
            self.last_source = src or ""
            self.accept()
        else:
            QMessageBox.warning(self, "FreeCAD", msg)

    def _do_export(self) -> None:
        from app.freecad_io.freecad_launcher import export_cad_for_openems, list_export_files

        cmd, gui, work = self._collect()
        src = self.e_source.text().strip()
        if not src:
            QMessageBox.warning(self, "Export", "Select a FreeCAD FCStd or STEP source first.")
            return
        out = Path(work) if work else Path(src).parent / "freecad_export"
        if work:
            out = Path(work) / "freecad_export"
        ok, log, out_dir = export_cad_for_openems(
            src,
            out,
            freecad_cmd=cmd,
            freecad_gui=gui,
            deflection=float(self.e_defl.value()),
        )
        files = list_export_files(out_dir)
        summary = f"out={out_dir} files={len(files)}"
        if files:
            names = ", ".join(p.name for p in files[:8])
            if len(files) > 8:
                names += ", …"
            summary += f" ({names})"
        self.status.setText(f"ok={ok} {summary}\n{log[-800:] if log else ''}")
        if not ok:
            QMessageBox.warning(self, "FreeCAD export failed", log[-1500:] if log else summary)
            return
        # STEP for the BREP tessellation path, else STL meshes (per-object preferred)
        step = next((p for p in files if p.suffix.lower() in {".step", ".stp"}), None)
        manifest = out_dir / "openems_export.json"
        stls = [p for p in files if p.suffix.lower() == ".stl"]
        combined = out_dir / f"{Path(src).stem}.stl"
        per_object = [p for p in stls if p.resolve() != combined.resolve()]
        if self.e_format.currentData() == "stl":
            # manifest lists every solid (name + bounds) — import all objects at once
            self.result_source = str(
                manifest
                if manifest.exists()
                else (per_object[0] if per_object else (stls[0] if stls else out_dir))
            )
        else:
            self.result_source = str(
                step or (stls[0] if stls else (files[0] if files else out_dir))
            )
        self.result_out_dir = str(out_dir)
        self.result_import = bool(self.chk_import.isChecked())
        self.last_action = "export"
        self.last_source = self.result_source
        self.last_out_dir = self.result_out_dir
        self.accept()
