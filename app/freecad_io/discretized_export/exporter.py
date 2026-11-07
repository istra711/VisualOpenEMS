"""High-level orchestration for the standalone discretised export."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from .csx_model import (
    build_identity_csx,
    grid_arrays,
    load_csx,
    objects_from_csx,
)
from .freecad_bridge import build_fcstd, write_manifest, write_macro
from .project_data import ObjectInfo, ProjectData, project_stem
from .step_export import export_step_from_stls
from .stl_io import write_binary_stl
from .voxelize import (
    Cancelled,
    cell_edges,
    classify_cells,
    compute_volume,
    extract_boundary,
    sheet_triangles,
)

Progress = Callable[[int, str], None]
Cancel = Callable[[], bool]


@dataclass
class ExportOptions:
    project_path: str | Path
    output_dir: str | Path
    mesh_xml: str | Path | None = None
    object_names: tuple[str, ...] = ()
    include_sheets: bool = True
    export_step: bool = False
    export_fcstd: bool = False
    fcstd_object_type: str = "part"  # "part" | "mesh"
    export_voxel_grid: bool = False
    voxel_grid_mode: str = "boundary"  # "boundary" | "all"
    freecad_cmd: str | Path | None = None
    step_name: str = ""
    progress: Progress | None = None
    cancel: Cancel | None = None


@dataclass
class ObjectResult:
    name: str
    material: str
    cells: int
    triangles: int
    volume_mm3: float
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ExportResult:
    files: list[Path]
    objects: list[ObjectResult]
    warnings: list[str]
    exactness: str
    log: str
    step_file: Path | None = None
    fcstd_file: Path | None = None
    grid_file: Path | None = None
    manifest_file: Path | None = None
    macro_file: Path | None = None


@dataclass
class Context:
    project: ProjectData
    mesh_xml: Path
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    unit_to_m: float
    objects: list[ObjectInfo]
    classify_csx: object
    warnings: list[str]
    mode: str  # "project" | "csx"


def list_objects(
    project_path: str | Path, mesh_xml: str | Path | None = None
) -> tuple[list[ObjectInfo], list[str], Path | None, bool, tuple[int, int, int]]:
    """Load project + mesh and return selectable objects and mesh metadata."""
    project = ProjectData.load(project_path, mesh_xml=mesh_xml)
    warnings = list(project.warnings)
    mesh_path = project.mesh_xml
    if mesh_path is None:
        return project.objects, warnings, None, project.cell_constant_material, (0, 0, 0)

    csx = load_csx(mesh_path)
    x, y, z, _unit = grid_arrays(csx)
    shape = (len(x) - 1, len(y) - 1, len(z) - 1)

    if any(obj.primitives for obj in project.objects):
        objects = project.objects
    else:
        objects = objects_from_csx(csx)
        warnings.append(
            "project JSON contains no primitive geometry; exporting CSX "
            "properties instead (objects sharing a material cannot be separated)"
        )

    return objects, warnings, mesh_path, project.cell_constant_material, shape


def export(options: ExportOptions) -> ExportResult:
    """Classify cells and write one STL per selected object."""
    progress = options.progress or (lambda _p, _m: None)
    cancel = options.cancel or (lambda: False)
    warnings: list[str] = []
    log_lines: list[str] = []

    def log(message: str) -> None:
        log_lines.append(message)
        progress(-1, message)

    log("Loading project and mesh...")
    context = _prepare_context(options.project_path, options.mesh_xml)
    warnings.extend(context.warnings)
    log(
        f"Mesh: {context.mesh_xml}  "
        f"({len(context.x) - 1} x {len(context.y) - 1} x {len(context.z) - 1} cells)"
    )
    log(f"Object source: {context.mode}")

    selected = _select_objects(context.objects, options.object_names)
    if not selected:
        raise ValueError("No objects selected for export")

    if context.project.cell_constant_material:
        exactness = (
            "CellConstantMaterial=1: cell-centre assignment matches the solver's "
            "cell material assignment."
        )
    else:
        exactness = (
            "CellConstantMaterial=0: mesh-voxel approximation. The solver's "
            "quarter-cell material averaging is not represented."
        )
    log(exactness)

    names = [obj.property_name or obj.name for obj in selected]
    log(f"Classifying {len(names)} object(s) at cell centres...")

    def classification_progress(done: int, total: int) -> None:
        if cancel():
            raise Cancelled()
        pct = int(70 * done / max(1, total))
        progress(pct, f"Classifying cells... {done:,}/{total:,}")

    masks = classify_cells(
        context.classify_csx,
        context.x,
        context.y,
        context.z,
        names,
        progress=classification_progress,
        cancel=cancel,
    )

    out_dir = Path(options.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = project_stem(context.project.path)
    files: list[Path] = []
    object_results: list[ObjectResult] = []
    used_names: set[str] = set()
    stl_items: list[tuple[str, Path]] = []
    step_file: Path | None = None
    fcstd_file: Path | None = None
    grid_file: Path | None = None
    voxel_grid_info: dict | None = None
    manifest_objects: list[dict] = []

    total = len(selected)
    for index, obj in enumerate(selected, start=1):
        if cancel():
            raise Cancelled()
        key = obj.property_name or obj.name
        mask = masks.get(key)
        if mask is None:
            mask = np.zeros(
                (len(context.x) - 1, len(context.y) - 1, len(context.z) - 1),
                dtype=bool,
            )

        obj_warnings: list[str] = []
        triangles = extract_boundary(mask, context.x, context.y, context.z)
        if options.include_sheets and obj.primitives:
            sheet = sheet_triangles([obj], obj_warnings)
            if len(sheet):
                triangles = np.concatenate([triangles, sheet], axis=0)

        cells = int(np.count_nonzero(mask))
        volume_mm3 = (
            compute_volume(mask, context.x, context.y, context.z)
            * (context.unit_to_m * 1000.0) ** 3
        )
        warnings.extend(f"{obj.name}: {w}" for w in obj_warnings)

        obj_files: list[Path] = []
        if len(triangles) == 0:
            obj_warnings.append("no cells/faces found - no STL written")
            warnings.append(f"{obj.name}: no cells/faces found - no STL written")
        else:
            file_stem = _unique_stem(obj.name, used_names)
            path = out_dir / f"{file_stem}_discretized.stl"
            write_binary_stl(path, triangles)
            obj_files.append(path)
            files.append(path)
            stl_items.append((obj.name, path))
            log(
                f"{obj.name}: {cells:,} cells, {len(triangles):,} triangles, "
                f"{volume_mm3:.3f} mm^3 -> {path.name}"
            )

        object_results.append(
            ObjectResult(
                name=obj.name,
                material=obj.material,
                cells=cells,
                triangles=int(len(triangles)),
                volume_mm3=float(volume_mm3),
                files=obj_files,
                warnings=obj_warnings,
            )
        )

        material = context.project.materials.get(obj.material)
        color = list(material.color) if material else [0.8, 0.5, 0.2, 1.0]
        if len(color) == 3:
            color.append(1.0)
        manifest_objects.append(
            {
                "name": obj.name,
                "material": obj.material,
                "kind": material.kind if material else "",
                "cells": cells,
                "triangles": int(len(triangles)),
                "volume_mm3": round(float(volume_mm3), 6),
                "stl": obj_files[0].name if obj_files else "",
                "color": [round(float(c), 6) for c in color[:4]],
                "transparency": int(round((1.0 - float(color[3])) * 100.0)),
            }
        )
        progress(70 + int(20 * index / max(1, total)), f"Exported {index}/{total}")

    if options.export_step:
        progress(90, "Writing STEP with FreeCADCmd...")
        if not stl_items:
            warnings.append("STEP not written: no non-empty STL objects")
        else:
            step_name = options.step_name or (
                f"{project_stem(context.project.path)}_discretized.step"
            )
            step_path = out_dir / step_name
            ok, step_log, step_warnings = export_step_from_stls(
                stl_items,
                step_path,
                freecad_cmd=options.freecad_cmd,
            )
            warnings.extend(step_warnings)
            if step_log.strip():
                log(step_log.strip())
            if ok:
                step_file = step_path
                files.append(step_path)
                log(f"STEP: {step_path}")
            else:
                log("STEP export failed; STL files are still available.")

    if options.export_voxel_grid:
        progress(92, "Writing OpenEMS voxel grid...")
        mode = str(options.voxel_grid_mode or "boundary").lower()
        if mode not in {"boundary", "all"}:
            mode = "boundary"
        combined = np.zeros(
            (len(context.x) - 1, len(context.y) - 1, len(context.z) - 1),
            dtype=bool,
        )
        for mask in masks.values():
            combined |= mask
        edges = cell_edges(
            combined,
            context.x,
            context.y,
            context.z,
            boundary_only=(mode == "boundary"),
        )
        if not edges:
            warnings.append("Voxel grid: no occupied cells - no grid written")
        else:
            grid_path = out_dir / f"{stem}_voxel_grid.json"
            grid_path.write_text(
                json.dumps(
                    {
                        "mode": mode,
                        "edges": [
                            [
                                round(float(a[0]), 9),
                                round(float(a[1]), 9),
                                round(float(a[2]), 9),
                                round(float(b[0]), 9),
                                round(float(b[1]), 9),
                                round(float(b[2]), 9),
                            ]
                            for a, b in edges
                        ],
                    }
                ),
                encoding="utf-8",
            )
            grid_file = grid_path
            files.append(grid_file)
            voxel_grid_info = {
                "file": grid_path.name,
                "mode": mode,
                "edges": len(edges),
            }
            log(f"Voxel grid: {len(edges):,} edges -> {grid_path.name}")

    manifest_path = out_dir / f"{stem}_discretized_manifest.json"
    macro_path = out_dir / f"{stem}_ImportVisualOpenEMS.FCMacro"
    fcstd_type = str(options.fcstd_object_type or "part").lower()
    if fcstd_type not in {"part", "mesh"}:
        fcstd_type = "part"
    manifest_file = write_manifest(
        manifest_path,
        project=stem,
        unit="mm",
        cell_constant_material=context.project.cell_constant_material,
        exactness=exactness,
        step=step_file.name if step_file is not None else None,
        objects=manifest_objects,
        fcstd_object_type=fcstd_type,
        voxel_grid=voxel_grid_info,
    )
    macro_file = write_macro(macro_path, manifest_file)
    log(f"Manifest: {manifest_file}")
    log(f"FreeCAD macro: {macro_file}")

    if options.export_fcstd:
        progress(96, "Writing FCStd with FreeCADCmd...")
        expected_fcstd = out_dir / f"{stem}_discretized.FCStd"
        ok, fc_log, fc_warnings = build_fcstd(
            macro_file,
            expected_fcstd,
            freecad_cmd=options.freecad_cmd,
        )
        warnings.extend(fc_warnings)
        if fc_log.strip():
            log(fc_log.strip())
        if ok:
            fcstd_file = expected_fcstd
            files.append(fcstd_file)
            log(f"FCStd: {fcstd_file}")
        else:
            log("FCStd export failed; STL/STEP/macro are still available.")

    progress(100, "Done")
    return ExportResult(
        files=files,
        objects=object_results,
        warnings=warnings,
        exactness=exactness,
        log="\n".join(log_lines),
        step_file=step_file,
        fcstd_file=fcstd_file,
        grid_file=grid_file,
        manifest_file=manifest_file,
        macro_file=macro_file,
    )


def _prepare_context(
    project_path: str | Path, mesh_xml: str | Path | None
) -> Context:
    project = ProjectData.load(project_path, mesh_xml=mesh_xml)
    if project.mesh_xml is None:
        raise FileNotFoundError(
            "No mesh XML found. Build the mesh in VisualOpenEMS or pass --mesh."
        )
    csx = load_csx(project.mesh_xml)
    x, y, z, unit_to_m = grid_arrays(csx)
    warnings = list(project.warnings)

    if any(obj.primitives for obj in project.objects):
        objects = project.objects
        identity, identity_warnings = build_identity_csx(
            objects, project.materials, unit_to_m
        )
        warnings.extend(identity_warnings)
        return Context(
            project=project,
            mesh_xml=project.mesh_xml,
            x=x,
            y=y,
            z=z,
            unit_to_m=unit_to_m,
            objects=objects,
            classify_csx=identity,
            warnings=warnings,
            mode="project identity CSX",
        )

    objects = objects_from_csx(csx)
    warnings.append(
        "project JSON contains no primitive geometry; using CSX properties. "
        "Objects that share one material are merged."
    )
    return Context(
        project=project,
        mesh_xml=project.mesh_xml,
        x=x,
        y=y,
        z=z,
        unit_to_m=unit_to_m,
        objects=objects,
        classify_csx=csx,
        warnings=warnings,
        mode="simulation CSX properties",
    )


def _select_objects(
    objects: Iterable[ObjectInfo], names: tuple[str, ...]
) -> list[ObjectInfo]:
    objects = list(objects)
    if not names:
        return objects
    wanted = set(names)
    return [obj for obj in objects if obj.name in wanted]


def _unique_stem(name: str, used: set[str]) -> str:
    stem = re.sub(r'[<>:"/\\|?*]+', "_", str(name)).strip(" .") or "object"
    candidate = stem
    counter = 2
    while candidate.lower() in used:
        candidate = f"{stem}_{counter}"
        counter += 1
    used.add(candidate.lower())
    return candidate




