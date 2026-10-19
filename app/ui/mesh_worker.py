"""Worker thread running the heavy mesh build off the GUI thread.

``MeshBuildThread`` wraps :func:`app.meshing.build_task.build_mesh_and_validate`
in a :class:`QThread`. The core returns the live CSX/FDTD objects; the GUI
thread slots (see ``MainWindow._on_mesh_built`` / ``_finish_mesh_build``) do the
matplotlib preview, the Mesh Viewer CSX swap and the result recording — none of
that happens here.
"""

from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.meshing.build_task import build_mesh_and_validate


class MeshBuildThread(QThread):
    """Run one mesh build in the background.

    Signals:
        log(str): progress lines (connect to a thread-safe logger).
        done(object): the :class:`~app.meshing.build_task.MeshBuildResult`.
        failed(str): the exception message when the build raised.
    """

    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, project, primitives, work_dir, parent=None) -> None:
        super().__init__(parent)
        self._project = project
        self._primitives = primitives
        self._work_dir = Path(work_dir)

    def run(self) -> None:  # noqa: D102 - QThread entry point
        try:
            result = build_mesh_and_validate(
                self._project,
                self._primitives,
                self._work_dir,
                log=self.log.emit,
            )
        except Exception as e:  # noqa: BLE001 — report to the GUI thread
            # The message alone is often cryptic (e.g. a numpy reduction error
            # deep inside easyMesh), so log the full traceback as well.
            self.log.emit("Mesh build error:\n" + traceback.format_exc())
            self.failed.emit(str(e))
            return
        self.done.emit(result)
