"""Offscreen GUI smoke for variable fields, table, dialogs and MainWindow."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _assert(condition, message="") -> None:
    if not condition:
        raise AssertionError(message or "assertion failed")


def _test_variable_field() -> None:
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication(sys.argv)
    from app.models.variables import VariableError
    from app.ui.variable_field import (
        VariableField,
        set_variable_autocomplete_enabled,
    )

    set_variable_autocomplete_enabled(True)
    field = VariableField(10.0, variables={"L": 20.0, "Lx": 30.0, "W": 4.0})
    _assert(field.value() == 10.0)
    _assert(field.completion_names() == ["L", "Lx", "W"], field.completion_names())

    field.setExpression("L/2")
    _assert(field.value() == 10.0)
    _assert(field.expression() == "L/2")
    _assert(field.expression_entry() == "L/2")
    field.setValue(7.5)
    _assert(field.value() == 7.5)
    _assert(field.expression() == "")

    # completion replaces only the identifier before the cursor
    field.setText("W + L")
    field.setCursorPosition(1)
    _assert(field._token_before_cursor() == "W")
    field._insert_completion("Lx")
    _assert(field.text() == "Lx + L", field.text())
    _assert(field.cursorPosition() == 2)

    # enabled autocomplete opens the popup with the matching names
    field.show()
    field.setText("L")
    field.setCursorPosition(1)
    field._on_text_edited("L")
    app.processEvents()
    _assert(field._completer.popup().isVisible())
    _assert(field._completer.completionCount() == 2, field._completer.completionCount())
    field.setFocus()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.keyClick(field, Qt.Key.Key_Down)
    QTest.keyClick(field, Qt.Key.Key_Return)
    app.processEvents()
    _assert(field.text() == "Lx", field.text())
    _assert(field.value() == 30.0, field.value())
    _assert(not field._completer.popup().isVisible())
    field.hide()

    # disabled autocomplete must never try to open a popup
    set_variable_autocomplete_enabled(False)
    field.setText("L")
    field._on_text_edited("L")
    _assert(not field._completer.popup().isVisible())
    set_variable_autocomplete_enabled(True)

    # scaled phi field: 90 degrees is pi/2 in project units
    import math

    phi = VariableField(0.0, scale=math.pi / 180.0, variables={"A": 45.0})
    phi.setExpression("A")
    _assert(abs(phi.value() - math.pi / 4) < 1e-12, phi.value())
    phi.setValue(math.pi / 2)
    _assert(abs(phi.value() - math.pi / 2) < 1e-12)

    bad = VariableField(0.0, variables={})
    bad.setText("Missing/2")
    try:
        bad.value()
    except VariableError as e:
        _assert("Missing" in str(e))
    else:  # pragma: no cover
        raise AssertionError("unknown variable must raise")
    _assert("c0392b" in bad.styleSheet())

    parent = QWidget()
    parent.deleteLater()
    app.processEvents()
    print("[field] value/expression/completion/scale OK")


def _test_variable_table() -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication(sys.argv)
    from app.models.project import VariableDef
    from app.ui.variable_table import VariableTableDialog

    table = VariableTableDialog(
        variables=[VariableDef("L", 20.0, "length"), VariableDef("W", 10.0)],
        usage={"l": 3, "w": 0},
    )
    _assert(table.table.rowCount() == 2)
    table.table.selectRow(0)
    _assert(table.ed_name.text() == "L")
    _assert("3 geometry fields" in table.lbl_usage.text(), table.lbl_usage.text())
    table.ed_name.setText("length")
    table.ed_value.setText("25")
    table.ed_comment.setText("board length")
    table._accept()
    vars_ = table.variables()
    _assert([v.name for v in vars_] == ["length", "W"], [v.name for v in vars_])
    _assert(vars_[0].value == 25.0)
    _assert(table.renames == {"L": "length"}, table.renames)

    # MainWindow-style callback rejects unresolved expressions and keeps editing
    warned: list[str] = []

    class _FakeMB:
        @staticmethod
        def warning(*args, **kwargs):  # noqa: ANN002, ANN003
            warned.append(str(args[2]) if len(args) > 2 else "warning")

    real_mb = QMessageBox
    import app.ui.variable_table as variable_table_module

    variable_table_module.QMessageBox = _FakeMB
    try:
        rejected = VariableTableDialog(
            variables=[VariableDef("L", 20.0)],
            validate=lambda definitions, renames: ["field still uses 'Q'"],
        )
        rejected._accept()
        _assert(bool(warned), "validation warning must be shown")
        _assert(rejected.variables() == [], "invalid table must not be accepted")
        _assert(rejected.result() != VariableTableDialog.DialogCode.Accepted)

        warned.clear()
        blank_value = VariableTableDialog(variables=[VariableDef("L", 20.0)])
        blank_value.ed_value.setText("")
        blank_value._accept()
        _assert(bool(warned), "blank variable value must be rejected")
        _assert(blank_value.variables() == [])
    finally:
        variable_table_module.QMessageBox = real_mb
    rejected.deleteLater()
    blank_value.deleteLater()
    table.deleteLater()
    app.processEvents()
    print("[table] edit/rename/validation OK")


class _FakeParent:
    """QWidget-like parent that only provides a project with variables."""

    def __init__(self) -> None:
        from PySide6.QtWidgets import QWidget

        class _Parent(QWidget):
            pass

        self.widget = _Parent()
        from app.models.project import Project, VariableDef

        self.project = Project.new_default("fake")
        self.project.variables = [
            VariableDef("L", 20.0),
            VariableDef("W", 10.0),
            VariableDef("A", 45.0),
        ]
        self.widget.project = self.project


def _test_geometry_dialogs() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.geometry_editors import (
        DumpDialog,
        PortDialog,
        PrimitiveBoxDialog,
        PrimitiveSphereDialog,
    )

    fake = _FakeParent()
    box = PrimitiveBoxDialog(fake.widget)
    box.spn_start[0].setExpression("0")
    box.spn_stop[0].setExpression("L")
    box.spn_stop[1].setExpression("W/2")
    box._accept()
    _assert(box.result is not None, "box dialog did not accept")
    _assert(box.result["stop"][0] == 20.0, box.result)
    _assert(box.result["stop"][1] == 5.0, box.result)
    _assert(box.result["expressions"]["stop.0"] == "L")

    edited = PrimitiveBoxDialog(fake.widget, prim=box.result)
    _assert(edited.spn_stop[0].text() == "L", edited.spn_stop[0].text())
    _assert(edited.spn_stop[1].text() == "W/2", edited.spn_stop[1].text())
    edited._accept()
    _assert(edited.result["expressions"]["stop.0"] == "L")
    _assert(edited.result["stop"][1] == 5.0)

    sphere = PrimitiveSphereDialog(fake.widget)
    sphere.spn_center[0].setExpression("L/2")
    sphere.spn_radius.setExpression("W/4")
    sphere._accept()
    _assert(sphere.result is not None)
    _assert(sphere.result["center"][0] == 10.0, sphere.result)
    _assert(sphere.result["radius"] == 2.5, sphere.result)
    _assert(sphere.result["start"][0] == 7.5, sphere.result)

    port = PortDialog(fake.widget)
    port.spn_stop[0].setExpression("L")
    port._accept()
    _assert(port.result_port is not None, "port dialog did not accept")
    _assert(port.result_port.stop[0] == 20.0, port.result_port)
    _assert(port.result_port.expressions["stop.0"] == "L")

    import math

    polar = PortDialog(fake.widget, coord_system="cylindrical")
    polar.spn_start[1].setExpression("A")
    polar._accept()
    _assert(polar.result_port is not None, "polar port dialog did not accept")
    _assert(abs(polar.result_port.start[1] - math.pi / 4) < 1e-12)
    entry = polar.result_port.expressions["start.1"]
    _assert(isinstance(entry, dict), entry)
    _assert(entry["expr"] == "A", entry)
    _assert(abs(entry["scale"] - math.pi / 180.0) < 1e-15, entry)

    dump = DumpDialog(fake.widget)
    dump.spn_stop[2].setExpression("W")
    dump._accept()
    _assert(dump.result is not None, "dump dialog did not accept")
    _assert(dump.result.stop[2] == 10.0, dump.result)
    _assert(dump.result.expressions["stop.2"] == "W")

    for dlg in (box, edited, sphere, port, polar, dump):
        dlg.deleteLater()
    fake.widget.deleteLater()
    app.processEvents()
    print("[dialogs] expressions and saved maps OK")


def _test_main_window_apply_reload() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from app.models.project import PortDef, Project, VariableDef
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    win = MainWindow(AppSettings.load())
    win.project.variables = [VariableDef("L", 20.0), VariableDef("W", 10.0)]
    prim = {
        "kind": "box",
        "name": "b",
        "material": "copper",
        "start": [0.0, 0.0, 0.0],
        "stop": [1.0, 1.0, 1.0],
        "expressions": {"stop.0": "L", "stop.1": "W/2"},
    }
    win.primitives = [dict(prim)]
    win.project.primitives = [dict(prim)]
    win.project.ports = [
        PortDef(
            name="p",
            start=[0.0, 0.0, 0.0],
            stop=[1.0, 1.0, 1.0],
            expressions={"stop.2": "L/2"},
        )
    ]

    ok = win._apply_variable_update(
        [VariableDef("L", 30.0), VariableDef("W", 10.0)], {}
    )
    _assert(ok, "variable apply failed")
    _assert(win.primitives[0]["stop"][0] == 30.0, win.primitives[0])
    _assert(win.primitives[0]["stop"][1] == 5.0, win.primitives[0])
    _assert(win.project.primitives[0]["stop"][0] == 30.0)
    _assert(win.project.ports[0].stop[2] == 15.0, win.project.ports[0])

    # rename follows the expression source text as well as numeric fields
    ok = win._apply_variable_update(
        [VariableDef("length", 30.0), VariableDef("W", 10.0)],
        {"L": "length"},
    )
    _assert(ok, "rename apply failed")
    _assert(
        win.primitives[0]["expressions"]["stop.0"] == "length",
        win.primitives[0]["expressions"],
    )
    _assert(
        win.project.ports[0].expressions["stop.2"] == "length/2",
        win.project.ports[0].expressions,
    )

    # unresolved expression is refused and leaves the previous geometry alone
    warned: list[str] = []

    class _FakeMB:
        @staticmethod
        def warning(*args, **kwargs):  # noqa: ANN002, ANN003
            warned.append(str(args[2]) if len(args) > 2 else "warning")

    import app.ui.main_window as main_window_module

    real_mb = main_window_module.QMessageBox
    main_window_module.QMessageBox = _FakeMB
    try:
        bad = win._apply_variable_update(
            [VariableDef("length", 30.0), VariableDef("W", 10.0)],
            {"W": "L"},
        )
    finally:
        main_window_module.QMessageBox = real_mb
    _assert(not bad, "renaming to a missing variable must fail")
    _assert(bool(warned), "a rejected variable update must warn")
    _assert(win.primitives[0]["stop"][0] == 30.0)

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "vars.fcsim.json"
        win.project.save(path)
        loaded = Project.load(path)
        _assert([v.name for v in loaded.variables] == ["length", "W"])
        # simulate stale numeric values and verify load-time resolution
        loaded.primitives[0]["stop"][0] = 999.0
        loaded.ports[0].stop[2] = 999.0
        win.project = loaded
        win.primitives = [dict(p) for p in (loaded.primitives or [])]
        win._resolve_loaded_variables()
        _assert(win.primitives[0]["stop"][0] == 30.0, win.primitives[0])
        _assert(win.project.ports[0].stop[2] == 15.0, win.project.ports[0])

    win.close()
    win.deleteLater()
    app.processEvents()
    print("[main] apply/rename/load resolution OK")


def main() -> int:
    _test_variable_field()
    _test_variable_table()
    _test_geometry_dialogs()
    _test_main_window_apply_reload()
    print("VARIABLES SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
