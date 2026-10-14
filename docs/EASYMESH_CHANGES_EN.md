# easyMesh4openEMS — local changes (patch documentation)

**Status:** the modified sources live in `third_party/easyMesh4openEMS/easyMesh/`
and are mirrored to `site-packages/easyMesh/` via
`work/patch_site_easy_mesh.py`. `build/lib/easyMesh/` is the **unchanged**
upstream state (reference for diffing, not patched).

**Why:** easyMesh (upstream) could not handle certain CSXCAD primitives, or it
cut the mesh / FDTD domain at the wrong place. The edits are tightly scoped,
commented and idempotent.

---

## Sync obligation

After every change to `third_party/easyMesh4openEMS/easyMesh/*.py`:

```powershell
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe work\patch_site_easy_mesh.py
```

The script copies the 5 module files to
`<sys.prefix>/Lib/site-packages/easyMesh/` and asserts that the polyhedron
guards (`_is_polyhedron_type`, `hasattr(prim, 'GetCoords')`) are present in the
patched `geometryUtils.py`.

---

## Change 1 — cylinders: include both end discs

**File:** `easyMesh/geometryUtils.py` — `process_primitive()`, `CYLINDER` and
`CYLINDRICALSHELL` branches.

**Upstream behaviour:** only the **start** end disc of the cylinder was fed
into the x/y coordinate and edge collection.

**Problem:** a cylinder running along **X or Y** (typical for wire / dipole /
Yagi elements) was therefore only registered at one end. The resulting mesh —
and with it the FDTD domain — ended **inside** the wire instead of at the other
end (truncated element → wrong resonance / S11).

**Fix:** both ends (`for end in (start, stop)`) contribute their AABB
coordinates (`±radius` in x/y) and the four edges. For **Z-cylinders**
start/stop share x/y → the extra entries are duplicates and are de-duplicated
downstream (harmless).

Affected code (excerpt, `geometryUtils.py`):

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

Same treatment for `CYLINDRICALSHELL` (outer radius
`radius + thickness/2`, both ends, both radii).

**Symptom before:** Yagi / dipole elements (X-oriented wires) only half in the
mesh → truncated domain. **After:** full length represented.

---

## Change 2 — polyhedron type detection (`_is_polyhedron_type`)

**File:** `easyMesh/geometryUtils.py` — new helper `_is_polyhedron_type(prim)`.

**Problem:** `CSPrimPolyhedron` / `CSPrimPolyhedronReader` have **no**
`GetCoords()` API. Upstream code called `prim.GetCoords()` →
`AttributeError` / crash or a silent abort while building the mesh.

**Fix:** type detection via `GetTypeName()` (substring `'polyhedron'`) **and**
via `GetType()` against `CSPrimitives.PrimitiveType.POLYHEDRON` /
`POLYHEDRONREADER` (robust against enum / int variants). All callers check
`_is_polyhedron_type(prim)` or `hasattr(prim, 'GetCoords')` first.

Guarded call sites:
- `process_primitive()` — polyhedron branch before the `GetCoords` fallback
  (`geometryUtils.py:152`)
- `collect_z_coordinates()` — polyhedron via AABB instead of `GetElevation`
  (`geometryUtils.py:219`)
- fallback in the `else` branch (`geometryUtils.py:201`) → `_handle_polyhedron`
- `meshingUtils.get_mesh_map()` — single-primitive case: `hasattr(..., 'GetCoords')`,
  otherwise `process_primitive` + `collect_z_coordinates` instead of a crash
  (`meshingUtils.py:207`)

---

## Change 3 — polyhedron via AABB instead of `GetCoords` / `GetElevation`

**File:** `easyMesh/geometryUtils.py` — `_prim_aabb_xy_z()` + `_handle_polyhedron()`.

**Problem:** without `GetCoords` there was no way to capture the x/y footprint
coordinates and the z planes of a polyhedron → the primitive was missing from
the mesh entirely.

**Fix:** AABB via `GetBoundBox()` (finite check included) →
`x_coords` / `y_coords` as an XY rectangle, `z_min` / `z_max` as z planes; edges
via `collect_edges()`; z planes via `collect_z_coordinates()`.

---

## Not changed

- `decorateOriginalMethods.py` (`AddCylinder`, `AddWire`, …): unchanged
  (dispatch only to the original methods).
- `meshProcessing.py`: unchanged.
- `build/lib/easyMesh/*`: unchanged (upstream reference).

---

## Verification

```powershell
# Mirror the patch into site-packages
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe work\patch_site_easy_mesh.py

# Mesher smoke (tessellates CAD → mesh)
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\freecad_mesh_smoke.py
```

Expected: `SITE-PACKAGES EASYMESH PATCHED` and a mesh report without a
polyhedron / `GetCoords` crash; X-oriented cylinders (wire / Yagi) covering
their full length in the mesh.
