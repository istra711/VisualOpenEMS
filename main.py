"""VisualOpenEMS — entry point for direct runs and the PyInstaller bundle.

Delegates to :func:`app.main.main` (same code path as ``python app\\main.py``);
adds a crash log for the windowed frozen EXE, which has no console.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


ROOT = _bundle_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import main  # noqa: E402


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        # windowed bundles swallow tracebacks — persist them for debugging
        try:
            base = (
                Path(sys.executable).resolve().parent
                if getattr(sys, "frozen", False)
                else ROOT
            )
            (base / "main_crash.log").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
        except OSError:
            pass
        raise
