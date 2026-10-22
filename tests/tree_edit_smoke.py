"""Smoke: Project/Geometry tree entries are editable (double-click / context menu)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _FakeBoxDialog:
    def __init__(self, parent=None, prim=None, coord_system="cartesian", display=None):
        del coord_system  # accepted for API parity (polar wedge editor)
        self.result = {
            "kind": "box",
            "name": "box1_edited",
            "material": "gold",
            "priority": 33,
            "start": [1, 2, 3],
            "stop": [4, 5, 6],
        }

    def display_values(self):
        return {"color": [1.0, 0.0, 0.0], "transparency": 40, "visible": False}

    def exec(self):
        return True


class _FakeCylDialog:
    def __init__(self, parent=None, prim=None, coord_system="cartesian", display=None):
        del coord_system  # accepted for API parity
        self.result = {
            "kind": "cylinder",
            "name": "cyl1_edited",
            "material": "silver",
            "priority": 7,
            "start": [0, 0, 0],
            "stop": [0, 0, 9],
            "radius": 0.25,
        }

    def display_values(self):
        return {"color": None, "transparency": 30, "visible": True}

    def exec(self):
        return True


class _FakeAssignDialog:
    def __init__(
        self, object_names, material_names, parent=None, assignment=None,
        display=None, display_for=None,
    ):
        from app.models.project import GeomAssignment

        self.result = GeomAssignment(
            object_name="patch1",
            role="dielectric",
            material="fr4",
            priority=5,
        )

    def display_values(self):
        return {"color": [0.0, 1.0, 0.0], "transparency": 10, "visible": True}

    def exec(self):
        return True


class _FakeMaterialDialog:
    def __init__(self, parent=None, material=None):
        from app.models.project import MaterialDef

        self.result = MaterialDef(
            name=material.name if material else "m",
            kind="dielectric",
            epsilon=4.4,
            kappa=0.1,
        )

    def exec(self):
        return True


class _FakePortDialog:
    def __init__(self, parent=None, port=None, coord_system="cartesian"):
        del coord_system  # accepted for API parity
        from app.models.project import PortDef

        self.result_port = PortDef(
            name="in1_edited",
            type="lumped",
            resistance=75.0,
            start=[0, 0, 0],
            stop=[0, 0, 1],
        )

    def exec(self):
        return True


class _FakeDumpDialog:
    def __init__(self, parent=None, dump=None, coord_system="cartesian", **kwargs):
        del coord_system, kwargs  # accepted for API parity (fit_box / nf2ff_box)
        from app.models.project import DumpDef

        self.result = DumpDef(name="Ht", quantity="H", frequency=1e9)

    def exec(self):
        return True


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    import app.ui.geometry_editors as ge
    from app.models.project import DumpDef, GeomAssignment, MaterialDef, PortDef
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow(AppSettings.load())

    win.primitives = [
        {"kind": "box", "name": "box1", "material": "copper", "priority": 10,
         "start": [0, 0, 0], "stop": [1, 1, 1], "mesh_path": "keep_me.npy"},
        {"kind": "cylinder", "name": "cyl1", "material": "copper", "priority": 10,
         "start": [0, 0, 0], "stop": [0, 0, 5], "radius": 0.5},
        {"kind": "stl", "name": "patch1", "material": "copper", "priority": 10,
         "path": "p.npy", "mesh_path": "p.npy"},
    ]
    win.project.materials = [MaterialDef(name="substrate", kind="dielectric", epsilon=3.38)]
    win.project.assignments = [GeomAssignment(object_name="patch1", role="pec", material="copper")]
    win.project.ports = [PortDef(name="in1", type="lumped", resistance=50.0,
                                 start=[0, 0, 0], stop=[0, 0, 1])]
    win.project.dumps = [DumpDef(name="Et", quantity="E", frequency=2.45e9)]
    from app.models.project import GeometryFile

    win.project.geometry_files = [
        GeometryFile(path=str(ROOT / "work" / "dummy.step"), format="step")
    ]
    win._refresh_tree()

    kinds = []
    for i in range(win.tree.count()):
        it = win.tree.item(i)
        k = it.data(Qt.UserRole)
        kinds.append(k)
        assert k, f"item {i} has no role: {it.text()}"
        assert it.data(Qt.UserRole + 1) is not None, it.text()
    print("roles:", kinds)
    for need in ("geom", "prim", "assign", "mat", "port", "dump", "nf2ff"):
        assert need in kinds, need

    def index_of(kind: str) -> int:
        for i in range(win.tree.count()):
            if win.tree.item(i).data(Qt.UserRole) == kind:
                return i
        raise AssertionError(kind)

    orig = dict(ge.__dict__)
    try:
        ge.PrimitiveBoxDialog = _FakeBoxDialog
        ge.PrimitiveCylinderDialog = _FakeCylDialog
        ge.AssignmentDialog = _FakeAssignDialog
        ge.MaterialDialog = _FakeMaterialDialog
        ge.PortDialog = _FakePortDialog
        ge.DumpDialog = _FakeDumpDialog

        win._edit_tree_entry("prim", 0)
        assert win.primitives[0]["name"] == "box1_edited"
        assert win.primitives[0]["material"] == "gold"
        # the dialog's display options land in the project
        assert win.project.object_display.get("box1_edited") == {
            "color": [1.0, 0.0, 0.0], "transparency": 40, "visible": False,
        }, win.project.object_display
        # stl mesh_path must survive box-dialog round trip only for box prims
        win._edit_tree_entry("prim", 1)
        assert win.primitives[1]["name"] == "cyl1_edited"
        assert win.primitives[1]["radius"] == 0.25
        win._edit_tree_entry("assign", 0)
        assert win.project.assignments[-1].material == "fr4"
        assert win.project.object_display.get("patch1") == {
            "color": [0.0, 1.0, 0.0], "transparency": 10, "visible": True,
        }, win.project.object_display
        win._edit_tree_entry("mat", 0)
        assert win.project.materials[-1].epsilon == 4.4
        win._edit_tree_entry("port", 0)
        assert win.project.ports[0].name == "in1_edited"
        assert win.project.ports[0].resistance == 75.0
        win._edit_tree_entry("dump", 0)
        assert win.project.dumps[0].name == "Ht"
    finally:
        for k, v in orig.items():
            setattr(ge, k, v)

    # dialog keeps extra material fields on edit
    m0 = MaterialDef(name="x", kind="dielectric", epsilon=2.0, kappa=0.01,
                     permeability=3.5, tand=0.02, color=(0.1, 0.2, 0.3, 1.0))
    dlg = ge.MaterialDialog(win, material=m0)
    dlg._accept()
    assert dlg.result.permeability == 3.5, dlg.result.permeability
    assert abs(dlg.result.tand - 0.02) < 1e-12
    assert dlg.result.color == (0.1, 0.2, 0.3, 1.0)

    # geom entry logs read-only hint, does not raise
    win._edit_tree_entry("geom", 0)

    # tooltip informs about double-click / right-click
    for i in range(win.tree.count()):
        assert win.tree.item(i).toolTip()

    # --- buttons below the tree are gone (Geometry menu / context menu only) ---
    for name in (
        "btn_edit_tree",
        "btn_del_tree",
        "btn_add_prim_box",
        "btn_add_prim_cyl",
        "btn_add_assign",
        "btn_add_material",
        "btn_add_port",
        "btn_edit_nf2ff",
        "btn_fit_nf2ff",
    ):
        assert not hasattr(win, name), name

    # --- duplicate (right-click -> Duplicate / Ctrl+D) ---
    n_prims0 = len(win.primitives)
    win._refresh_tree()
    win.tree.setCurrentRow(index_of("prim"))
    win._on_duplicate_selected_tree()
    assert len(win.primitives) == n_prims0 + 1
    assert win.primitives[1]["name"] == "box1_edited (copy)", win.primitives[1]["name"]
    assert win._current_tree_entry() == ("prim", 1), win._current_tree_entry()
    # duplicating the selected copy chains a unique name
    win._on_duplicate_selected_tree()
    assert len(win.primitives) == n_prims0 + 2
    assert win.primitives[2]["name"] == "box1_edited (copy 2)", win.primitives[2]["name"]
    del win.primitives[1:3]
    win._refresh_tree()

    win.tree.setCurrentRow(index_of("mat"))
    win._on_duplicate_selected_tree()
    assert [m.name for m in win.project.materials] == ["substrate", "substrate (copy)"]
    win.project.materials.pop()
    win._refresh_tree()

    win.tree.setCurrentRow(index_of("port"))
    win._on_duplicate_selected_tree()
    assert [p.name for p in win.project.ports] == ["in1_edited", "in1_edited (copy)"]
    win.project.ports.pop()
    win._refresh_tree()

    win.tree.setCurrentRow(index_of("dump"))
    win._on_duplicate_selected_tree()
    assert [d.name for d in win.project.dumps] == ["Ht", "Ht (copy)"]
    win.project.dumps.pop()
    win._refresh_tree()

    win.tree.setCurrentRow(index_of("assign"))
    win._on_duplicate_selected_tree()
    assert len(win.project.assignments) == 2
    assert all(a.object_name == "patch1" for a in win.project.assignments)
    win.project.assignments.pop()
    win._refresh_tree()

    # geom / nf2ff are singletons: duplicate is disabled and a no-op
    n_geom = len(win.project.geometry_files)
    win.tree.setCurrentRow(index_of("geom"))
    win._on_duplicate_selected_tree()
    assert len(win.project.geometry_files) == n_geom
    n_items = win.tree.count()
    win.tree.setCurrentRow(index_of("nf2ff"))
    win._on_duplicate_selected_tree()
    assert win.tree.count() == n_items

    # --- delete UI: context menu action + Delete key ---

    win._refresh_tree()
    n_before = win.tree.count()
    win.tree.setCurrentRow(index_of("dump"))
    win._on_delete_selected_tree()
    assert not win.project.dumps
    assert win.tree.count() == n_before - 1
    assert all(win.tree.item(i).data(Qt.UserRole) != "dump" for i in range(win.tree.count()))

    # selection is restored after refresh (edit/delete keep working)
    win._refresh_tree()
    row = index_of("port")
    win.tree.setCurrentRow(row)
    win._refresh_tree()
    assert win._current_tree_entry() == ("port", 0), win._current_tree_entry()

    # Delete key on tree removes selected prim
    n_prims = len(win.primitives)
    win.tree.setCurrentRow(index_of("prim"))

    class _Ev:
        def __init__(self, key):
            self._key = key

        def key(self):
            return self._key

        def accept(self):
            self._accepted = True

    win._tree_key_press(_Ev(Qt.Key.Key_Delete))
    assert len(win.primitives) == n_prims - 1

    # geom can be removed from the project list
    assert win.project.geometry_files
    win._refresh_tree()
    win.tree.setCurrentRow(index_of("geom"))
    win._on_delete_selected_tree()
    assert not win.project.geometry_files

    # nf2ff: disable via delete path
    win.project.nf2ff.enabled = True
    win._refresh_tree()
    win.tree.setCurrentRow(index_of("nf2ff"))
    win._on_delete_selected_tree()
    assert not win.project.nf2ff.enabled

    win.close()
    print("TREE EDIT SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
