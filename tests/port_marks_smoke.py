"""Smoke: FreeCAD port marks → PortDef (no FreeCAD GUI required)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    from app.freecad_io.port_marks import (
        freecad_extract_ports_code,
        find_manifest_ports,
        parse_ports_payload,
        ports_to_port_defs,
    )

    # 1) named points merge
    named = {
        "in1": {"start": [0.0, 0.0, 0.0], "stop": [0.0, 0.0, 1.5]},
    }
    raw = [
        {"name": "msl", "type": "msl", "start": [1, 2, 0], "stop": [5, 2, 0], "resistance": 50},
        {"name": "in1", "start": [9, 9, 9], "stop": [9, 9, 9]},  # invalid stop==start
    ]
    ports = parse_ports_payload({"ports": raw, "named_ports": named})
    defs = ports_to_port_defs(ports)
    names = {d.name for d in defs}
    assert "in1" in names, names
    assert "msl" in names, names
    in1 = next(d for d in defs if d.name == "in1")
    assert in1.start == [0.0, 0.0, 0.0] and in1.stop == [0.0, 0.0, 1.5]
    assert in1.direction == "z"
    msl = next(d for d in defs if d.name == "msl")
    assert msl.type == "msl" and msl.direction == "x"
    print("merge ok", [(d.name, d.direction) for d in defs])

    # 2) FreeCAD helper code is self-contained and defines extract_openems_ports
    code = freecad_extract_ports_code()
    assert "def extract_openems_ports" in code
    assert "PORT_" in code
    ns: dict = {"json": json}
    exec(code, ns)
    assert callable(ns["extract_openems_ports"])

    class FakeDoc:
        pass

    class FakeObj:
        PropertiesList = [
            "openems_port_name",
            "openems_port_start",
            "openems_port_stop",
            "openems_port_r",
            "openems_port_type",
        ]

        def __init__(self, name, start, stop):
            self.Label = name
            self.Name = name
            self.openems_port_start = start
            self.openems_port_stop = stop
            self.openems_port_r = 50.0
            self.openems_port_type = "lumped"
            self.openems_port_name = "feed"
            self.Shape = None
            self.Placement = None

    class FakeVertex:
        def __init__(self, p):
            self.Point = p

    class FakeVec:
        def __init__(self, x, y, z):
            self.x, self.y, self.z = x, y, z

    class FakeShape:
        def __init__(self, pt):
            self.Vertexes = [FakeVertex(pt)]

    class FakePtObj:
        def __init__(self, label, pt):
            self.Label = label
            self.Name = label
            self.Shape = FakeShape(pt)
            self.Placement = None

    doc = FakeDoc()
    doc.Objects = [
        FakeObj("probe", [0, 0, 0], [0, 0, 2]),
        FakePtObj("PORT_out_start", FakeVec(1, 0, 0)),
        FakePtObj("PORT_out_stop", FakeVec(1, 0, 3)),
        FakePtObj("ignored_solid", FakeVec(0, 0, 0)),
    ]
    ports2 = ns["extract_openems_ports"](doc)
    pnames = {p["name"] for p in ports2}
    assert "feed" in pnames, pnames
    assert "out" in pnames, pnames
    feed = next(p for p in ports2 if p["name"] == "feed")
    assert feed["start"] == [0, 0, 0] and feed["stop"] == [0, 0, 2]
    out = next(p for p in ports2 if p["name"] == "out")
    assert out["start"] == [1, 0, 0] and out["stop"] == [1, 0, 3]
    print("freecad helper ok", pnames)

    # 3) manifest file helper
    man = ROOT / "work" / "port_marks_smoke" / "openems_export.json"
    man.parent.mkdir(parents=True, exist_ok=True)
    man.write_text(
        json.dumps(
            {
                "objects": [],
                "ports": [
                    {
                        "name": "in1",
                        "type": "lumped",
                        "start": [0, 0, 0],
                        "stop": [0, 0, 1.5],
                        "resistance": 50,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    mp = find_manifest_ports(str(man))
    assert len(mp) == 1 and mp[0]["name"] == "in1"
    print("manifest ok")
    print("PORT MARKS SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
