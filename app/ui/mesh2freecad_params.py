"""Mesh2FreeCAD Parameters dialog.

Every option here belongs to the project and is written to the
``.fcsim.json`` file by the normal project save.  The export itself always
uses a temporary folder; "Keep temporary files" only exists for debugging.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.project import Mesh2FreeCADSpec, Project


class Mesh2FreeCADParamsDialog(QDialog):
    """Modal editor for the project's Mesh2FreeCAD export options."""

    def __init__(self, parent: QWidget | None = None, project: Project | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mesh2FreeCAD Parameters")
        self.resize(380, 440)
        self._project = project
        spec = project.mesh2freecad if project is not None else Mesh2FreeCADSpec()

        outer = QVBoxLayout(self)

        intro = QLabel(
            "The built mesh is exported as discretised geometry (one mesh-cell "
            "solid per object) and opened directly in FreeCAD."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        g_doc = QGroupBox("FreeCAD document")
        fd = QFormLayout(g_doc)
        self.cmb_type = QComboBox()
        self.cmb_type.addItem("Part (solid)", "part")
        self.cmb_type.addItem("Mesh (fast)", "mesh")
        self.cmb_type.setToolTip(
            "Part::Feature sews the triangles into a solid/shell (good for CAD).\n"
            "Mesh::Feature stores the exact STL mesh (faster for large models)."
        )
        self.chk_step = QCheckBox("Also write a STEP file")
        self.chk_step.setToolTip(
            "Write a STEP file next to the temporary FCStd document. STEP uses "
            "the same per-object names as the FreeCAD document."
        )
        fd.addRow("FCStd object type", self.cmb_type)
        fd.addRow("", self.chk_step)
        outer.addWidget(g_doc)

        g_grid = QGroupBox("OpenEMS voxel grid")
        fg = QFormLayout(g_grid)
        self.chk_grid = QCheckBox("Add the OpenEMS Cartesian cell wireframe")
        self.chk_grid.setToolTip(
            "Adds an 'OpenEMS Voxel Grid' wireframe so the actual FDTD cells "
            "are visible in FreeCAD."
        )
        self.cmb_grid_mode = QComboBox()
        self.cmb_grid_mode.addItem("Boundary cells only", "boundary")
        self.cmb_grid_mode.addItem("All occupied cells", "all")
        self.cmb_grid_mode.setToolTip(
            "Boundary: only cells on the object surface.\n"
            "All: also internal cell edges (useful with a transparent solid)."
        )
        fg.addRow("", self.chk_grid)
        fg.addRow("Grid scope", self.cmb_grid_mode)
        outer.addWidget(g_grid)

        g_misc = QGroupBox("Geometry and files")
        fm = QFormLayout(g_misc)
        self.chk_sheets = QCheckBox("Include zero-thickness boxes and polygon sheets")
        self.chk_sheets.setToolTip(
            "Patch/ground planes have no cell volume. Export them as planar "
            "triangles so they do not disappear."
        )
        self.chk_ports = QCheckBox("Export port points")
        self.chk_ports.setToolTip(
            "Add the start and stop point of every port as PORT_<name>_start / "
            "PORT_<name>_stop, plus a connecting line."
        )
        self.chk_keep = QCheckBox("Keep temporary files after FreeCAD closes")
        self.chk_keep.setToolTip(
            "Debugging only. Normally the temporary export folder is deleted "
            "when FreeCAD closes (or when VisualopenEMS exits)."
        )
        fm.addRow("", self.chk_sheets)
        fm.addRow("", self.chk_ports)
        fm.addRow("", self.chk_keep)
        outer.addWidget(g_misc)

        note = QLabel(
            "These settings are stored in the project file (.fcsim.json) and "
            "restored when the project is opened again."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#666;")
        outer.addWidget(note)
        outer.addStretch(1)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_defaults = QPushButton("Restore defaults")
        btn_defaults.clicked.connect(self._restore_defaults)
        bb.addButton(btn_defaults, QDialogButtonBox.ButtonRole.ResetRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

        self._load(spec)
        self.chk_grid.toggled.connect(self._update_grid_state)

    def _load(self, spec: Mesh2FreeCADSpec) -> None:
        self.cmb_type.setCurrentIndex(
            max(0, self.cmb_type.findData(str(spec.fcstd_object_type or "part")))
        )
        self.chk_step.setChecked(bool(spec.export_step))
        self.chk_grid.setChecked(bool(spec.voxel_grid))
        self.cmb_grid_mode.setCurrentIndex(
            max(0, self.cmb_grid_mode.findData(str(spec.voxel_grid_mode or "boundary")))
        )
        self.chk_sheets.setChecked(bool(spec.include_sheets))
        self.chk_ports.setChecked(bool(spec.export_ports))
        self.chk_keep.setChecked(bool(spec.keep_temp))
        self._update_grid_state()

    def _update_grid_state(self) -> None:
        self.cmb_grid_mode.setEnabled(self.chk_grid.isChecked())

    def _restore_defaults(self) -> None:
        self._load(Mesh2FreeCADSpec())

    def _accept(self) -> None:
        if self._project is not None:
            spec = self._project.mesh2freecad
            spec.fcstd_object_type = str(self.cmb_type.currentData() or "part")
            spec.export_step = bool(self.chk_step.isChecked())
            spec.voxel_grid = bool(self.chk_grid.isChecked())
            spec.voxel_grid_mode = str(self.cmb_grid_mode.currentData() or "boundary")
            spec.include_sheets = bool(self.chk_sheets.isChecked())
            spec.export_ports = bool(self.chk_ports.isChecked())
            spec.keep_temp = bool(self.chk_keep.isChecked())
        self.accept()
