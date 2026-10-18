"""Filesystem locations: repository root in dev, bundle dir when frozen.

When frozen with PyInstaller, ``sys._MEIPASS`` is the extraction directory of
the onefile bundle (or the ``_internal`` dir of an onedir build). It contains
the bundled *source* copies of ``app/``, ``third_party/easyMesh4openEMS/``,
``examples/`` and ``docs/`` — real files on disk, so:

  * the external simulation Python can import ``app.*`` via PYTHONPATH /
    the APP_ROOT baked into generated ``project_run.py`` scripts, and
  * FreeCADCmd can run ``examples/freecad_export_for_openems.py``.

The extraction dir only exists while the frozen process runs; the GUI always
regenerates ``project_run.py`` (fresh APP_ROOT) before each Run, so this is
fine for the normal workflow.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Root that contains app/, third_party/, examples/ and docs/ on disk."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def settings_path() -> Path:
    """Location of app_settings.json (next to the EXE when frozen).

    When the running script is a test/smoke under ``<root>/tests`` a throwaway
    file in the temp dir is used instead, so tests never overwrite the user's
    real settings (loading + MainWindow.save() round-trips used to clobber it).
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent / "app_settings.json"
    try:
        argv0 = Path(sys.argv[0]).resolve()
        is_test = argv0.parent.name == "tests" or "pytest" in str(argv0).lower()
        if is_test:
            return Path(tempfile.gettempdir()) / "openems_gui_test_settings.json"
    except Exception:  # noqa: BLE001
        pass
    return app_root() / "app_settings.json"
