"""Run generated simulation scripts in a subprocess."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from app.settings import AppSettings
from app.winproc import hidden_console_kwargs


class SimulationRunner:
    def __init__(self, settings: AppSettings, app_root: Path | None = None) -> None:
        self.settings = settings
        if app_root is None:
            from app.paths import app_root as resolve_app_root

            app_root = resolve_app_root()
        self.app_root = app_root
        self._proc: subprocess.Popen | None = None
        self._log_cb: Callable[[str], None] | None = None
        self.last_exit: int | None = None
        self.log_path: Path | None = None
        self._log_file = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, script_path: Path | str, log_cb: Callable[[str], None] | None = None) -> None:
        if self.is_running():
            raise RuntimeError("Simulation already running")
        script_path = Path(script_path)
        # NOTE: Path("") == Path(".") which exists — an empty setting would then
        # spawn Popen([".", script]) and fail with WinError 87. Require a real file.
        py_str = str(getattr(self.settings, "simulation_python", "") or "").strip()
        python = Path(py_str) if py_str else None
        if python is None or not python.is_file():
            raise FileNotFoundError(
                "Simulation python not found: "
                f"{py_str or '(empty)'} — set it in Settings → Paths."
            )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.app_root) + os.pathsep + env.get("PYTHONPATH", "")
        # Unbuffered child stdout: otherwise the post-processing prints sit in an
        # 8 kB block buffer and the log looks frozen until the process exits.
        env["PYTHONUNBUFFERED"] = "1"
        # ensure openEMS can find DLLs
        openems = Path(self.settings.openems_root)
        if openems.exists():
            env["PATH"] = str(openems) + os.pathsep + env.get("PATH", "")

        self._log_cb = log_cb
        self.last_exit = None
        self.log_path = script_path.parent / "sim_log.txt"
        try:
            self._log_file = open(self.log_path, "w", encoding="utf-8")  # noqa: SIM115
        except Exception as e:  # noqa: BLE001
            print("sim log open failed:", e)
            self._log_file = None
        # -u is belt-and-braces with PYTHONUNBUFFERED (some embedded interpreters
        # ignore the env var); it keeps the log streaming during post-processing.
        self._proc = self._spawn([str(python), "-u", str(script_path)], env)
        t = threading.Thread(target=self._pump, daemon=True)
        t.start()

    def _spawn(self, cmd: list[str], env: dict) -> subprocess.Popen:
        """Popen that hides the console, with a plain fallback.

        The hidden-console flags (CREATE_NO_WINDOW) can make CreateProcess fail
        with WinError 87 on some setups; retrying without them still runs (a
        console may flash briefly).
        """
        kwargs = dict(
            cwd=str(self.app_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        extra = hidden_console_kwargs()
        try:
            return subprocess.Popen(cmd, **kwargs, **extra)
        except OSError as e:  # noqa: BLE001 — fall back to a plain spawn
            print(f"subprocess spawn with {extra} failed ({e}); retrying plainly")
            return subprocess.Popen(cmd, **kwargs)

    def _pump(self) -> None:
        """Drain subprocess stdout. log_cb may be invoked from this thread."""
        assert self._proc is not None and self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                line = line.rstrip("\n")
                if self._log_file:
                    try:
                        self._log_file.write(line + "\n")
                        self._log_file.flush()
                    except Exception:  # noqa: BLE001
                        pass
                if self._log_cb:
                    try:
                        self._log_cb(line)
                    except Exception as e:  # noqa: BLE001 — never kill pump
                        print("log_cb error:", e)
            code = self._proc.wait()
        except Exception as e:  # noqa: BLE001
            print("runner pump error:", e)
            code = self._proc.poll()
        self.last_exit = code
        if self._log_file:
            try:
                self._log_file.write(f"[exit code {code}]\n")
                self._log_file.flush()
                self._log_file.close()
            except Exception:  # noqa: BLE001
                pass
            self._log_file = None
        if self._log_cb:
            try:
                self._log_cb(f"[exit code {code}]")
                if self.log_path:
                    self._log_cb(f"Full simulation log: {self.log_path}")
            except Exception as e:  # noqa: BLE001
                print("log_cb exit error:", e)

    def abort(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def wait(self, timeout: float | None = None) -> int | None:
        if not self._proc:
            return None
        try:
            return self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
