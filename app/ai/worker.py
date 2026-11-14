"""Qt worker that runs an AI backend off the GUI thread."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.ai.backend import AiBackend, AiReply


class AiWorker(QThread):
    chunk = Signal(str)
    done = Signal(object)  # AiReply
    failed = Signal(str)

    def __init__(
        self,
        backend: AiBackend,
        prompt: str,
        *,
        title: str = "VisualopenEMS AI",
        session_id: str = "",
        model: str = "",
        timeout_s: int = 180,
        cwd: Path | str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.backend = backend
        self.prompt = prompt
        self.title = title
        self.session_id = session_id
        self.model = model
        self.timeout_s = timeout_s
        self.cwd = cwd
        self._stop = False

    def cancel(self) -> None:
        self._stop = True
        try:
            self.backend.cancel()
        except Exception:  # noqa: BLE001 - cancellation must never raise
            pass

    def _should_stop(self) -> bool:
        return bool(self._stop)

    def run(self) -> None:
        try:
            if self._stop:
                self.done.emit(AiReply(error="Request cancelled."))
                return
            reply: AiReply = self.backend.run(
                self.prompt,
                title=self.title,
                session_id=self.session_id,
                model=self.model,
                timeout_s=self.timeout_s,
                cwd=self.cwd,
                on_text=self.chunk.emit,
                should_stop=self._should_stop,
            )
            self.done.emit(reply)
        except Exception as exc:  # noqa: BLE001 - surface a readable error
            self.failed.emit(str(exc))
