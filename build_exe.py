"""Build a standalone Windows EXE of the GUI with PyInstaller (onefile, pruned).

Usage (with the simulation Python that has all dependencies installed):

    python build_exe.py

Output: dist/VisualOpenEMS.exe  (windowed, single file)

The bundle contains the GUI (PySide6, matplotlib, numpy, scipy, h5py,
CSXCAD/openEMS bindings, easyMesh) plus *source* copies of app/, examples/,
docs/ and third_party/easyMesh4openEMS/ — the generated project_run.py and
the external simulation Python import app.* from the bundled source dir
while the GUI runs (see app/paths.py).

Size pruning (0.6.x): the spec written below drops what the GUI never uses —
unused pure-Python stacks that only enter through optional imports (django,
astropy, sympy, sqlalchemy, networkx, pytest, hypothesis), the Qt Quick/Qml/
Pdf/Network DLLs and their plugins, the software-OpenGL fallback
(opengl32sw.dll) and the bundled ffmpeg (MP4 export then asks for ffmpeg).
Set OPENEMS_KEEP_OPENGL_SW=1 to keep the software-OpenGL fallback for machines
without a GPU driver.

Settings (app_settings.json) are stored next to the EXE; the work directory
defaults to the repository work/ folder (changeable in Settings).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build" / "pyi"
SPEC = BUILD / "VisualOpenEMS.spec"

# The WinPython environment contains the full scientific/AI stack; the GUI only
# needs PySide6 + numpy/scipy/pandas/matplotlib/h5py/skrf/CSXCAD/VTK. Without
# these excludes PyInstaller pulls ~2 GB of junk (torch, OpenCV, GDAL, ...).
EXCLUDES = [
    # the WinPython env ships PyQt5 next to PySide6 — only PySide6 is used
    "PyQt5", "PyQt6", "PySide2",
    # heavy unused scientific / AI stacks
    "torch", "torchvision", "torchaudio",
    # NOTE: vtk is intentionally NOT excluded — the VTK viewer needs it.
    "cv2", "osgeo", "sklearn", "skimage", "statsmodels",
    "numba", "llvmlite", "blis", "cvxopt",
    "pyarrow", "dask", "xarray", "zarr", "numcodecs", "fsspec",
    "netCDF4", "pyproj", "h5netcdf", "tables",
    # plotting / media extras not used by the GUI
    "bokeh", "panel", "pydeck", "plotly", "dash",
    "moviepy", "av",  # imageio: needed for image export, not for video
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
    # Pure-Python packages that only enter the graph through optional imports
    # (pandas.io.sql -> sqlalchemy, networkx -> sympy, astropy -> networkx,
    #  astropy tests -> hypothesis/pytest, django via a PyInstaller hook).
    # None of them is imported by the app or by its runtime dependencies.
    "django", "astropy", "sympy", "sqlalchemy", "networkx",
    "pytest", "_pytest", "hypothesis",
    # MP4 export: imageio asks for imageio-ffmpeg at runtime (see vtk_viewer)
    "imageio_ffmpeg",
]

# Destination prefixes (forward slashes) removed from the bundle.
DROP = [
    "PySide6/Qt6Quick.dll",
    "PySide6/Qt6Qml.dll",
    "PySide6/Qt6QmlModels.dll",
    "PySide6/Qt6QmlMeta.dll",
    "PySide6/Qt6QmlWorkerScript.dll",
    "PySide6/Qt6Pdf.dll",
    "PySide6/Qt6Network.dll",
    "PySide6/QtNetwork.pyd",
    "PySide6/plugins/tls/",
    "PySide6/plugins/networkinformation/",
    "PySide6/plugins/platforms/qdirect2d.dll",
    "PySide6/Qt6VirtualKeyboard.dll",
    "PySide6/plugins/platforminputcontexts/",
    # MP4 export asks for ffmpeg instead of bundling 62 MB
    "imageio_ffmpeg/",
]


def _write_spec() -> Path:
    drop = list(DROP)
    if os.environ.get("OPENEMS_KEEP_OPENGL_SW", "") not in ("1", "true", "yes"):
        drop.append("PySide6/opengl32sw.dll")
    spec = f'''# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

datas = [
    (r"{ROOT / 'app'}", "app"),
    (r"{ROOT / 'assets'}", "assets"),
    (r"{ROOT / 'third_party' / 'easyMesh4openEMS'}", "third_party/easyMesh4openEMS"),
    (r"{ROOT / 'examples'}", "examples"),
    (r"{ROOT / 'docs'}", "docs"),
]
binaries = []
hiddenimports = ["imageio"]
binaries += collect_dynamic_libs("CSXCAD")
binaries += collect_dynamic_libs("openEMS")
binaries += collect_dynamic_libs("vtk")
hiddenimports += collect_submodules("openEMS")
hiddenimports += collect_submodules("CSXCAD")

a = Analysis(
    [r"{ROOT / 'main.py'}"],
    pathex=[r"{ROOT}"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes={EXCLUDES!r},
    noarchive=False,
    optimize=0,
)

# --- size pruning: drop unused Qt DLLs/plugins and the bundled ffmpeg ------
_DROP = {drop!r}


def _keep(entry) -> bool:
    name = entry[0].replace("\\\\", "/")
    return not any(part in name for part in _DROP)


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VisualOpenEMS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[r"{ROOT / 'assets' / 'app_icon.ico'}"],
)
'''
    BUILD.mkdir(parents=True, exist_ok=True)
    SPEC.write_text(spec, encoding="utf-8")
    return SPEC


def main() -> int:
    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        print("PyInstaller not installed — run: pip install pyinstaller")
        return 1

    DIST.mkdir(parents=True, exist_ok=True)
    # stale onefile output locks the target on Windows — drop it first
    exe = DIST / "VisualOpenEMS.exe"
    if exe.exists():
        try:
            exe.unlink()
        except OSError as e:
            print(f"Cannot overwrite {exe} (still running?): {e}")
            return 1

    spec = _write_spec()
    print(f"PyInstaller spec: {spec}")
    pyi.run(
        [
            str(spec),
            "--noconfirm",
            "--clean",
            f"--distpath={DIST}",
            f"--workpath={BUILD}",
        ]
    )

    if exe.exists():
        mb = exe.stat().st_size / (1024 * 1024)
        print(f"BUILT {exe} ({mb:.1f} MB)")
        return 0
    print("BUILD FAILED — no exe produced")
    return 1


if __name__ == "__main__":
    sys.exit(main())
