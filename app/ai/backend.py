"""Backends for the AI assistant.

Phase 1 uses the OpenCode CLI in one-shot JSON mode.  The command consumes the
existing OpenCode login/subscription and does not require an API key in the
VisualopenEMS settings.  A small protocol keeps the UI independent of the
provider, so a Copilot SDK or OpenAI-compatible backend can be added later.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.winproc import hidden_console_kwargs


@dataclass
class AiReply:
    text: str = ""
    session_id: str = ""
    meta: dict = field(default_factory=dict)
    error: str = ""


class AiBackend:
    """Minimal interface used by the Qt worker."""

    name = "backend"

    def is_available(self) -> bool:
        return False

    def run(
        self,
        prompt: str,
        *,
        title: str = "VisualopenEMS AI",
        session_id: str = "",
        model: str = "",
        timeout_s: int = 180,
        cwd: Path | str | None = None,
        on_text: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> AiReply:
        raise NotImplementedError

    def cancel(self) -> None:
        pass


class OpenCodeBackend(AiBackend):
    """One-shot prompt through ``opencode run --format json``."""

    name = "OpenCode CLI"

    def __init__(self, command: str = "opencode") -> None:
        self.command = str(command or "opencode").strip() or "opencode"
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self._timed_out = False
        self._lock = threading.Lock()
        self._stderr = ""
        self._last_error = ""

    def resolved_command(self) -> str:
        command = self.command
        if os.path.sep in command or (os.path.altsep and os.path.altsep in command):
            candidate = command if Path(command).is_file() else ""
        else:
            candidate = shutil.which(command) or ""
        if not candidate:
            return ""
        # The npm launcher is a .cmd shim that passes arguments through cmd.exe,
        # which mangles multi-line prompts on Windows.  Resolve the real CLI
        # executable next to the shim so the prompt reaches OpenCode intact.
        path = Path(candidate)
        if path.suffix.lower() in (".cmd", ".bat"):
            sibling = (
                path.parent
                / "node_modules"
                / "@opencode"
                / "cli"
                / "bin"
                / "opencode.exe"
            )
            if sibling.is_file():
                return str(sibling)
        return candidate

    def is_available(self) -> bool:
        return bool(self.resolved_command())

    def cancel(self) -> None:
        self._cancelled = True
        self._terminate()

    def _terminate(self) -> None:
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        try:
            if proc.stderr is None:
                return
            for line in proc.stderr:
                self._stderr = (self._stderr + line)[-6000:]
        except Exception:  # noqa: BLE001 - never break the worker
            return

    @staticmethod
    def parse_event_line(line: str) -> dict:
        """Parse one JSON line of ``opencode run --format json``.

        Unknown/partial lines are returned as ``{"type": "raw", ...}`` so the
        caller can ignore log noise without failing the whole request.
        """
        try:
            payload = json.loads(str(line or "").strip())
            if isinstance(payload, dict):
                return payload
        except (TypeError, ValueError):
            pass
        return {"type": "raw", "text": str(line or "").strip()}

    def run(
        self,
        prompt: str,
        *,
        title: str = "VisualopenEMS AI",
        session_id: str = "",
        model: str = "",
        timeout_s: int = 180,
        cwd: Path | str | None = None,
        on_text: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> AiReply:
        self._cancelled = False
        self._timed_out = False
        self._stderr = ""
        self._last_error = ""
        command = self.resolved_command()
        if not command:
            return AiReply(
                error=(
                    f"OpenCode command '{self.command}' was not found. Install "
                    "OpenCode or set the command in AI settings."
                )
            )

        workdir = Path(cwd) if cwd else (
            Path(tempfile.gettempdir()) / "visualopenems-ai"
        )
        try:
            workdir.mkdir(parents=True, exist_ok=True)
        except OSError:
            workdir = Path(tempfile.gettempdir())

        args = [command, "run", "--format", "json", "--title", str(title or "VisualopenEMS AI")]
        if session_id:
            args.extend(["--session", str(session_id)])
        if model:
            args.extend(["--model", str(model)])
        # Keep the command line below the Windows CreateProcess limit.
        text = str(prompt or "")
        if len(text) > 28000:
            text = text[:27900] + "\n... [prompt truncated by VisualopenEMS]"
        # A .cmd/.bat shim goes through cmd.exe, which truncates multi-line
        # arguments.  The npm shim is normally resolved to opencode.exe above;
        # as a last resort flatten newlines instead of silently losing context.
        if Path(command).suffix.lower() in (".cmd", ".bat"):
            text = " ".join(text.splitlines())
        args.append(text)

        env = os.environ.copy()
        env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
        env["NO_COLOR"] = "1"
        kwargs = dict(
            cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        try:
            proc = subprocess.Popen(args, **kwargs, **hidden_console_kwargs())
        except OSError as exc:  # noqa: BLE001 - show a useful message
            return AiReply(error=f"Could not start OpenCode: {exc}")

        with self._lock:
            self._proc = proc
        stderr_thread = threading.Thread(
            target=self._drain_stderr, args=(proc,), daemon=True
        )
        stderr_thread.start()

        timeout = max(10, int(timeout_s or 180))
        timer = threading.Timer(timeout, self._on_timeout)
        timer.daemon = True
        timer.start()

        text_parts: list[str] = []
        session = str(session_id or "")
        meta: dict = {}
        error_text = ""
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if should_stop is not None and should_stop():
                    self._cancelled = True
                    self._terminate()
                    break
                event = self.parse_event_line(line.lstrip("\ufeff"))
                kind = str(event.get("type") or "")
                sid = str(event.get("sessionID") or "")
                if sid:
                    session = sid
                if kind == "text":
                    part = event.get("part") or {}
                    if str(part.get("type") or "") == "text":
                        chunk = str(part.get("text") or "")
                        if chunk:
                            text_parts.append(chunk)
                            if on_text is not None:
                                try:
                                    on_text(chunk)
                                except Exception:  # noqa: BLE001
                                    pass
                elif kind == "step_finish":
                    part = event.get("part") or {}
                    if isinstance(part.get("tokens"), dict):
                        meta["tokens"] = part.get("tokens")
                    if part.get("cost") is not None:
                        meta["cost"] = part.get("cost")
                    if part.get("reason"):
                        meta["reason"] = part.get("reason")
                elif kind == "error":
                    err = event.get("error") or {}
                    if isinstance(err, dict):
                        error_text = str(
                            err.get("message") or err.get("type") or err
                        )
                    else:
                        error_text = str(err)
            try:
                exit_code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._terminate()
                try:
                    exit_code = proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    exit_code = None
        finally:
            timer.cancel()
            if proc.poll() is None:
                self._terminate()
            try:
                stderr_thread.join(timeout=1.0)
            except Exception:  # noqa: BLE001
                pass
            with self._lock:
                self._proc = None

        answer = "".join(text_parts).strip()
        if self._cancelled:
            return AiReply(
                text=answer,
                session_id=session,
                meta=meta,
                error="Request cancelled.",
            )
        if self._timed_out:
            return AiReply(
                text=answer,
                session_id=session,
                meta=meta,
                error=f"OpenCode did not answer within {timeout} seconds.",
            )
        if not answer:
            detail = error_text or self._stderr.strip()
            if not detail:
                detail = f"OpenCode exited with code {exit_code} without text."
            return AiReply(session_id=session, meta=meta, error=detail[-3000:])
        return AiReply(text=answer, session_id=session, meta=meta)

    def _on_timeout(self) -> None:
        self._timed_out = True
        self._terminate()


class MockBackend(AiBackend):
    """Deterministic backend used by tests and UI smoke checks."""

    name = "Mock"

    def __init__(self, answer: str = "Mock AI answer.") -> None:
        self.answer = answer

    def is_available(self) -> bool:
        return True

    def run(self, prompt: str, **kwargs) -> AiReply:
        on_text = kwargs.get("on_text")
        if on_text is not None:
            on_text(self.answer)
        return AiReply(text=self.answer, session_id="mock-session", meta={"mock": True})
