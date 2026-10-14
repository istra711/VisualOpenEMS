"""Run generated simulation scripts in a subprocess."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from app.settings import AppSettings


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
        python = Path(self.settings.simulation_python)
        if not python.exists():
            raise FileNotFoundError(f"Simulation python not found: {python}")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.app_root) + os.pathsep + env.get("PYTHONPATH", "")
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
        self._proc = subprocess.Popen(
            [str(python), str(script_path)],
            cwd=str(self.app_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        t = threading.Thread(target=self._pump, daemon=True)
        t.start()

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
