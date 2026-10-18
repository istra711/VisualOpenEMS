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
    # CREATE_NO_WINDOW alone suppresses the console window reliably. Combining
    # it with a STARTUPINFO (STARTF_USESHOWWINDOW) is redundant and has been
    # observed to make CreateProcess fail with WinError 87 ("The parameter is
    # incorrect") on some setups, so keep it minimal.
    return {"creationflags": subprocess.CREATE_NO_WINDOW}
