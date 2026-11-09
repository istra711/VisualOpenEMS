#!/usr/bin/env bash
# Build a standalone VisualopenEMS executable for Linux with PyInstaller.
#
#   bash build_linux.sh
#
# Output: dist/VisualopenEMS
#
# The GUI needs a native openEMS/CSXCAD installation and a Linux-built
# easyMesh4openEMS (see docs/LINUX.md).  This script bundles the application
# sources and docs; it does NOT bundle a system openEMS installation.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

"$PYTHON" - <<'PY'
import importlib.util
if importlib.util.find_spec("PyInstaller") is None:
    raise SystemExit("PyInstaller not installed — run: python3 -m pip install pyinstaller")
PY

"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --windowed \
  --name VisualopenEMS \
  --icon assets/app_icon.png \
  --add-data "app:app" \
  --add-data "assets:assets" \
  --add-data "third_party/easyMesh4openEMS:third_party/easyMesh4openEMS" \
  --add-data "examples:examples" \
  --add-data "docs:docs" \
  --collect-binaries CSXCAD \
  --collect-binaries openEMS \
  --collect-binaries vtk \
  --collect-submodules openEMS \
  --collect-submodules CSXCAD \
  --collect-data gerbonara \
  --collect-submodules gerbonara \
  --hidden-import imageio \
  --hidden-import imageio.plugins.pillow \
  --hidden-import imageio.plugins.pillowmulti \
  --hidden-import PIL \
  --hidden-import PIL.Image \
  --hidden-import PIL.GifImagePlugin \
  --exclude-module PyQt5 \
  --exclude-module PyQt6 \
  --exclude-module PySide2 \
  --exclude-module torch \
  --exclude-module torchvision \
  --exclude-module cv2 \
  --exclude-module osgeo \
  --exclude-module sklearn \
  --exclude-module skimage \
  --exclude-module numba \
  --exclude-module pyarrow \
  --exclude-module dask \
  --exclude-module xarray \
  --exclude-module pytest \
  --exclude-module IPython \
  --exclude-module tkinter \
  --distpath dist \
  --workpath build/pyi-linux \
  --specpath build/pyi-linux \
  main.py

echo "BUILT dist/VisualopenEMS"
