"""Windows helpers: start console child processes without a visible terminal."""

from __future__ import annotations

import subprocess
import sys


def hidden_console_kwargs() -> dict:
    """Kwargs for subprocess.Popen/run to suppress the console window on Windows.

    A GUI app (PyInstaller --windowed) has no console of its own; spawning a
    console child (FreeCADCmd, simulation python) would otherwise flash a
    black terminal window.  CREATE_NO_WINDOW + STARTF_USESHOWWINDOW/SW_HIDE
    together are the most robust combination across Windows versions.
    """
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
