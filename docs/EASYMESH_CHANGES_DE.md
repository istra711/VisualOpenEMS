# easyMesh4openEMS — lokale Änderungen (Patch-Doku)

> English version: **[EASYMESH_CHANGES_EN.md](EASYMESH_CHANGES_EN.md)**

**Status:** geänderte Quellen liegen in `third_party/easyMesh4openEMS/easyMesh/`
und werden via `work/patch_site_easy_mesh.py` nach `site-packages/easyMesh/`
gespiegelt. `build/lib/easyMesh/` ist der **unveränderte** Upstream-Stand
(Referenz zum Diffen, wird nicht gepatcht).

**Warum:** easyMesh (upstream) konnte bestimmte CSXCAD-Primitive nicht
verarbeiten bzw. hat das Mesh/FDTD-Domain an der falschen Stelle abgeschnitten.
Die Eingriffe sind eng begrenzt, kommentiert und idempotent.

---

## Sync-Pflicht

Nach jeder Änderung an `third_party/easyMesh4openEMS/easyMesh/*.py`:

```powershell
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe work\patch_site_easy_mesh.py
```

Das Skript kopiert die 5 Moduldateien nach
`<sys.prefix>/Lib/site-packages/easyMesh/` und verifiziert per Assert, dass die
Polyhedron-Guards (`_is_polyhedron_type`, `hasattr(prim, 'GetCoords')`) im
gepatchten `geometryUtils.py` vorhanden sind.

---

## Änderung 1 — Zylinder: beide Endscheiben einbeziehen

**Datei:** `easyMesh/geometryUtils.py` — `process_primitive()`, `CYLINDER`- und
`CYLINDRICALSHELL`-Zweig.

**Upstream-Verhalten:** nur die **Start**-Endscheibe des Zylinders wurde in die
x/y-Koordinaten- und Edge-Sammlung eingespeist.

**Problem:** ein Zylinder, der entlang **X oder Y** läuft (typisch bei
Draht/Dipol-/Yagi-Elementen), wurde dadurch nur an einem Ende registriert. Das
erzeugte Mesh — und damit die FDTD-Domain — endete **im** Draht statt am
anderen Ende (gekürztes Element → falsche Resonanz/S11).

**Fix:** beide Enden (`for end in (start, stop)`) tragen ihre
AABB-Koordinaten (`±radius` in x/y) und die vier Kanten bei. Für
**Z-Zylinder** liefern start/stop identische x/y → die zusätzlichen Einträge sind
Duplikate und werden downstream de-dupliziert (unschädlich).

Betroffene Stellen (Auszug, `geometryUtils.py`):

```python
# Contribute BOTH end discs: a cylinder along x/y would otherwise only
# register the start end and the mesh (and with it the FDTD domain)
# stops inside the wire. For z-cylinders start/stop share x/y, so the
# extra entries are duplicates and get de-duplicated downstream.
for end in (start, stop):
    x_vals = [end[0] - radius, end[0] + radius]
    y_vals = [end[1] - radius, end[1] + radius]
    x.extend(x_vals)
    y.extend(y_vals)
    x_edges.append([x_vals[0], y_vals[0], y_vals[1], prim, False])
    x_edges.append([x_vals[1], y_vals[0], y_vals[1], prim, False])
    y_edges.append([y_vals[0], x_vals[0], x_vals[1], prim, False])
    y_edges.append([y_vals[1], x_vals[0], x_vals[1], prim, False])
```

Gleiche Behandlung für `CYLINDRICALSHELL` (äußerer Radius
`radius + thickness/2`, beide Enden, beide Radien).

**Symptom vorher:** Yagi/Dipol-Elemente (X-orientierte Drähte) nur halb im
Mesh → verkürzte Domain. **Nachher:** volle Länge abgebildet.

---

## Änderung 2 — Polyhedron-Typ-Erkennung (`_is_polyhedron_type`)

**Datei:** `easyMesh/geometryUtils.py` — neue Hilfsfunktion `_is_polyhedron_type(prim)`.

**Problem:** `CSPrimPolyhedron` / `CSPrimPolyhedronReader` besitzen **keine**
`GetCoords()`-API. Upstream-Code rief `prim.GetCoords()` auf →
`AttributeError`/Crash oder stiller Abbruch beim Mesh-Aufbau.

**Fix:** Typ-Erkennung über `GetTypeName()` (Substring `'polyhedron'`) **und**
über `GetType()` gegen `CSPrimitives.PrimitiveType.POLYHEDRON` /
`POLYHEDRONREADER` (robust gegen Enum-/int-Varianten). Alle Aufrufer prüfen
zuvor `_is_polyhedron_type(prim)` bzw. `hasattr(prim, 'GetCoords')`.

Stellen mit Guard:
- `process_primitive()` — Polyhedron-Zweig vor dem `GetCoords`-Fallback
  (`geometryUtils.py:152`)
- `collect_z_coordinates()` — Polyhedron über AABB statt `GetElevation`
  (`geometryUtils.py:219`)
- Fallback im `else`-Zweig (`geometryUtils.py:201`) → `_handle_polyhedron`
- `meshingUtils.get_mesh_map()` — Single-Primitive-Fall: `hasattr(..., 'GetCoords')`,
  sonst `process_primitive` + `collect_z_coordinates` statt Crash
  (`meshingUtils.py:207`)

---

## Änderung 3 — Polyhedron über AABB statt `GetCoords`/`GetElevation`

**Datei:** `easyMesh/geometryUtils.py` — `_prim_aabb_xy_z()` + `_handle_polyhedron()`.

**Problem:** ohne `GetCoords` gab es keinen Weg, die x/y-Fußabdruck-Koordinaten
und die z-Ebenen eines Polyhedrons zu erfassen → Primitive fehlten komplett im
Mesh.

**Fix:** AABB über `GetBoundBox()` (Finite-Check inkl.) →
`x_coords`/`y_coords` als XY-Rechteck, `z_min`/`z_max` als z-Ebenen; Edges über
`collect_edges()`; Z-Ebenen über `collect_z_coordinates()`.

---

## Nicht geändert

- `decorateOriginalMethods.py` (`AddCylinder`, `AddWire`, …): unverändert
  (nur Dispatch auf Original-Methoden).
- `meshProcessing.py`: unverändert.
- `build/lib/easyMesh/*`: unverändert (Upstream-Referenz).

---

## Verifikation

```powershell
# Patch nach site-packages spiegeln
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe work\patch_site_easy_mesh.py

# Mesher-Smoke (tesselliert CAD → Mesh)
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\freecad_mesh_smoke.py
```

Erwartet: `SITE-PACKAGES EASYMESH PATCHED` bzw. Mesh-Report ohne
Polyhedron-/`GetCoords`-Crash; X-orientierte Zylinder (Draht/Yagi) über die
volle Länge im Mesh.
