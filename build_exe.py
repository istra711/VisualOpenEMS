"""Build a standalone Windows EXE of the GUI with PyInstaller (onefile).

Usage (with the simulation Python that has all dependencies installed):

    python build_exe.py

Output: dist/FreeCAD_OpenEMS_GUI.exe  (windowed, single file)

The bundle contains the GUI (PySide6, matplotlib, numpy, scipy, h5py,
CSXCAD/openEMS bindings, easyMesh) plus *source* copies of app/, examples/,
docs/ and third_party/easyMesh4openEMS/ — the generated project_run.py and
the external simulation Python import app.* from the bundled source dir
while the GUI runs (see app/paths.py).

Settings (app_settings.json) are stored next to the EXE; the work directory
defaults to the repository work/ folder (changeable in Settings).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        print("PyInstaller not installed — run: pip install pyinstaller")
        return 1

    dist = ROOT / "dist"
    build = ROOT / "build" / "pyi"
    # stale onefile output locks the target on Windows — drop it first
    exe = dist / "FreeCAD_OpenEMS_GUI.exe"
    if exe.exists():
        try:
            exe.unlink()
        except OSError as e:
            print(f"Cannot overwrite {exe} (still running?): {e}")
            return 1

    args = [
        str(ROOT / "main.py"),
        "--name=FreeCAD_OpenEMS_GUI",
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--noupx",
        # taskbar / explorer icon (generated from assets/logo.svg by
        # tools_svg_to_ico.py; the file itself is bundled for the window icon)
        f"--icon={ROOT / 'assets' / 'app_icon.ico'}",
        f"--path={ROOT}",
        f"--distpath={dist}",
        f"--workpath={build}",
        f"--specpath={build}",
        # real source trees for the external simulation python / FreeCADCmd:
        f"--add-data={ROOT / 'app'};app",
        f"--add-data={ROOT / 'assets'};assets",
        f"--add-data={ROOT / 'third_party' / 'easyMesh4openEMS'};third_party/easyMesh4openEMS",
        f"--add-data={ROOT / 'examples'};examples",
        f"--add-data={ROOT / 'docs'};docs",
        # native bindings used at runtime (openEMS / CSXCAD wheels):
        "--collect-binaries=CSXCAD",
        "--collect-binaries=openEMS",
        "--collect-submodules=openEMS",
        "--collect-submodules=CSXCAD",
        # the WinPython env ships PyQt5 next to PySide6 — PyInstaller refuses
        # to bundle two Qt bindings; the GUI uses PySide6 only
        "--exclude=PyQt5",
        "--exclude=PyQt6",
        "--exclude=PySide2",
    ]
    # The WinPython environment contains the full scientific/AI stack; the GUI
    # only needs PySide6 + numpy/scipy/pandas/matplotlib/h5py/skrf/CSXCAD.
    # Without these excludes PyInstaller pulls ~2 GB of junk (torch, OpenCV,
    # GDAL, VTK, bokeh, ...) into the bundle via transitive imports.
    excludes = [
        # heavy unused scientific / AI stacks
        "torch", "torchvision", "torchaudio",
        "cv2", "vtk", "osgeo", "sklearn", "skimage", "statsmodels",
        "numba", "llvmlite", "blis", "cvxopt",
        "pyarrow", "dask", "xarray", "zarr", "numcodecs", "fsspec",
        "netCDF4", "pyproj", "h5netcdf", "tables",
        # plotting / media extras not used by the GUI
        "bokeh", "panel", "pydeck", "plotly", "dash",
        "imageio", "imageio_ffmpeg", "moviepy", "av",
        "tokenizers", "transformers",
        # dev / doc / notebook toolchain
        "IPython", "jedi", "black", "yapf", "astroid", "pylint",
        "sphinx", "docutils", "babel", "pygments",
        "zmq",  # pyzmq (only used by IPython/jupyter)
        # crypto / windows COM / excel IO not used by the GUI
        "nacl", "bcrypt", "cryptography",
        "win32com", "pythonwin",
        "openpyxl", "odfpy", "xlsxwriter", "xlrd",
        # tkinter / stdlib dev modules
        "tkinter", "turtle", "idlelib", "pydoc_data",
        "setuptools", "pkg_resources",
        # unused Qt modules (PySide6 ships the whole Qt gallery)
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQmlModels",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.QtMultimedia", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtPdf",
    ]
    for mod in excludes:
        args.append(f"--exclude={mod}")
    print("PyInstaller:", " ".join(args))
    pyi.run(args)

    out = dist / "FreeCAD_OpenEMS_GUI.exe"
    if out.exists():
        mb = out.stat().st_size / (1024 * 1024)
        print(f"BUILT {out} ({mb:.1f} MB)")
        return 0
    print("BUILD FAILED — no exe produced")
    return 1


if __name__ == "__main__":
    sys.exit(main())
