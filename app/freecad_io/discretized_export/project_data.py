"""Read VisualOpenEMS project JSON files without importing the main application.

The add-on is intentionally standalone: it only reads the saved project file and
the CSXCAD mesh XML, so the VisualOpenEMS source tree is never modified.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def project_stem(path: str | Path) -> str:
    """``my_antenna`` from ``my_antenna.fcsim.json`` (or ``my_antenna.json``)."""
    name = Path(path).name
    for suffix in (".fcsim.json", ".json"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name or "project"


@dataclass
class MaterialInfo:
    name: str
    kind: str = "dielectric"
    epsilon: float = 1.0
    kappa: float = 0.0
    color: tuple[float, float, float, float] = (0.8, 0.5, 0.2, 1.0)


@dataclass
class ObjectInfo:
    """One selectable object from the project or from the CSX file."""

    name: str
    material: str = ""
    priority: int = 10
    role: str = ""
    primitives: list[dict[str, Any]] = field(default_factory=list)
    source: str = "project"  # "project" | "csx"
    property_name: str = ""

    @property
    def label(self) -> str:
        material = self.material or "?"
        return f"{self.name}  [{material}]"

    @property
    def has_geometry(self) -> bool:
        return bool(self.primitives)


@dataclass
class ProjectData:
    path: Path
    raw: dict[str, Any]
    materials: dict[str, MaterialInfo]
    objects: list[ObjectInfo]
    mesh_xml: Path | None
    cell_constant_material: bool
    drawing_unit: str
    warnings: list[str]

    @classmethod
    def load(cls, path: str | Path, mesh_xml: str | Path | None = None) -> "ProjectData":
        project_path = Path(path).expanduser().resolve()
        if not project_path.is_file():
            raise FileNotFoundError(f"Project file not found: {project_path}")

        raw = json.loads(project_path.read_text(encoding="utf-8"))
        warnings: list[str] = []

        materials: dict[str, MaterialInfo] = {}
        for item in raw.get("materials") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            name = str(item["name"])
            raw_color = item.get("color") or (0.8, 0.5, 0.2, 1.0)
            try:
                color = tuple(float(v) for v in raw_color)
            except (TypeError, ValueError):
                color = (0.8, 0.5, 0.2, 1.0)
            if len(color) == 3:
                color = (*color, 1.0)
            materials[name] = MaterialInfo(
                name=name,
                kind=str(item.get("kind") or "dielectric"),
                epsilon=float(item.get("epsilon", 1.0) or 1.0),
                kappa=float(item.get("kappa", 0.0) or 0.0),
                color=color[:4],
            )

        assignments: dict[str, dict[str, Any]] = {}
        for item in raw.get("assignments") or []:
            if isinstance(item, dict) and item.get("object_name"):
                assignments[str(item["object_name"])] = item

        primitives = raw.get("primitives") or []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for index, prim in enumerate(primitives):
            if not isinstance(prim, dict):
                continue
            name = str(prim.get("name") or f"object_{index + 1}")
            grouped.setdefault(name, []).append(prim)

        objects: list[ObjectInfo] = []
        for name, group in grouped.items():
            assignment = assignments.get(name, {})
            role = str(assignment.get("role") or "")
            if role == "ignore":
                continue
            group_materials = [
                str(p.get("material")) for p in group if p.get("material")
            ]
            material = str(
                assignment.get("material")
                or (group_materials[0] if group_materials else "")
                or ""
            )
            if not material:
                warnings.append(f"{name}: no material assigned - skipped")
                continue
            if material not in materials:
                warnings.append(
                    f"{name}: material {material!r} is not in project.materials - "
                    "using a dielectric fallback"
                )
            priority = assignment.get("priority", group[0].get("priority", 10))
            try:
                priority_int = int(priority)
            except (TypeError, ValueError):
                priority_int = 10
            objects.append(
                ObjectInfo(
                    name=name,
                    material=material,
                    priority=priority_int,
                    role=role,
                    primitives=group,
                    source="project",
                    property_name=name,
                )
            )

        # Projects created entirely from FreeCAD assignments can have no
        # primitive list in the JSON. Keep them as objects so the CSX fallback
        # can still expose their material properties.
        if not objects:
            for name, assignment in assignments.items():
                role = str(assignment.get("role") or "")
                if role == "ignore":
                    continue
                material = str(assignment.get("material") or "")
                if not material:
                    continue
                objects.append(
                    ObjectInfo(
                        name=name,
                        material=material,
                        role=role,
                        source="project",
                        property_name=material,
                    )
                )

        project_mesh = _find_mesh_xml(project_path, raw, mesh_xml)
        if project_mesh is None:
            warnings.append(
                "No csx.xml found. Select the mesh XML manually or run Build Mesh "
                "in VisualOpenEMS first."
            )

        mesh_raw = raw.get("mesh") or {}
        drawing_unit = str(
            mesh_raw.get("drawing_unit") or mesh_raw.get("unit") or "mm"
        )
        sim_raw = raw.get("sim") or {}
        cell_constant = bool(sim_raw.get("cell_constant_material", False))

        return cls(
            path=project_path,
            raw=raw,
            materials=materials,
            objects=objects,
            mesh_xml=project_mesh,
            cell_constant_material=cell_constant,
            drawing_unit=drawing_unit,
            warnings=warnings,
        )


def _find_mesh_xml(
    project_path: Path, raw: dict[str, Any], explicit: str | Path | None
) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())

    results = raw.get("results") or {}
    for key in ("csx_xml", "mesh_xml"):
        value = results.get(key)
        if value:
            candidates.append(Path(str(value)))
    for key in ("project_dir", "sim_dir"):
        value = results.get(key)
        if value:
            candidates.append(Path(str(value)) / "csx.xml")

    candidates.extend(
        [
            project_path.parent / "csx.xml",
            project_path.parent / "simulation" / "csx.xml",
        ]
    )

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None
