# Running VisualopenEMS on Linux

VisualopenEMS is prepared for native Linux, but the current release asset is a
Windows one-file executable.  On Linux, run it from source or build a native
PyInstaller binary.

> **Native Linux is recommended.** WSL is possible for the Python part, but VTK
> and Qt need a working X11/Wayland display and OpenGL; a native desktop is the
> reliable path.

## 1. Prerequisites

- Python 3.10–3.11 (3.11 recommended; the generated openEMS scripts use it)
- Qt 6 runtime libraries and OpenGL drivers for the desktop
- `openEMS` and `CSXCAD` Python bindings, matching Python ABI
- A Linux build of `third_party/easyMesh4openEMS`
- Optional: FreeCAD (`FreeCADCmd` / `FreeCAD`) for FCStd and STEP features

Check the bindings:

```bash
python3.11 -c "import CSXCAD, openEMS; print(CSXCAD.__file__, openEMS.__file__)"
```

`openEMS` is not a normal pip-only package; follow the
[openEMS installation instructions](https://openems.de/) for your distribution
or build it from source.  `pyems` is not required.

Install the Python GUI dependencies:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Install easyMesh (requires a C/C++ compiler and Python development headers):

```bash
sudo apt install build-essential python3-dev
pip install ./third_party/easyMesh4openEMS
```

For a minimal Debian/Ubuntu desktop Qt setup, the distribution usually needs
something like `libgl1`, `libxkbcommon-x11-0`, `libxcb-cursor0` and the
platform OpenGL driver.  Package names differ between distributions.

## 2. Run from source

```bash
. .venv/bin/activate
python main.py
```

Open **Tools → Settings…** and check:

| Setting | Linux example |
|---|---|
| Simulation Python | `/path/to/.venv/bin/python` |
| FreeCADCmd | `/usr/bin/FreeCADCmd` |
| FreeCAD GUI | `/usr/bin/FreeCAD` |
| openEMS root | `/usr` or `/opt/openEMS` |

The executable discovery can also be overridden with environment variables:

```bash
export FREECAD_CMD=/usr/bin/FreeCADCmd
export FREECAD_GUI=/usr/bin/FreeCAD
export OPENEMS_ROOT=/usr
export SIMULATION_PYTHON=.venv/bin/python
export APPCSXCAD=/usr/bin/AppCSXCAD
```

`AppCSXCAD` is only needed for **Open in AppCSXCAD (3D)**.  The normal Mesh
Viewer and the generated openEMS scripts do not need it.

## 3. Headless checks

```bash
QT_QPA_PLATFORM=offscreen python tests/verify_imports.py
QT_QPA_PLATFORM=offscreen python tests/mesh_viewer_smoke.py
QT_QPA_PLATFORM=offscreen python tests/project_label_smoke.py
```

If a Qt/VTK window fails on Wayland, try the X11 backend:

```bash
QT_QPA_PLATFORM=xcb python main.py
```

## 4. Build a native Linux executable

```bash
python3.11 -m pip install pyinstaller
bash build_linux.sh
./dist/VisualopenEMS
```

`build_linux.sh` bundles the Python GUI and the `app/`, `assets/`, `examples/`
and `docs/` trees.  It deliberately does **not** bundle a system openEMS
installation; the generated scripts use the simulation Python configured in
Settings.  A self-contained AppImage/Flatpak is a possible next packaging step,
but is not included yet.

## 5. Linux feature matrix

| Feature | Linux readiness |
|---|---|
| GUI, project files, S-Parameters, Antenna, Field Viewer, 3D Design | yes, native Python/Qt/VTK |
| Meshing | yes, after building easyMesh for Linux |
| FDTD simulation | yes, with native openEMS/CSXCAD bindings |
| FreeCAD import/export | yes, with `FreeCADCmd` configured |
| CST/Gerber/KiCad/STL import | yes |
| AppCSXCAD | yes, if the binary is in `PATH` or configured |
| Windows `.exe` release asset | not usable on Linux |
