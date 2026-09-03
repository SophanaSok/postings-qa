"""Start, watch and stop a `jobbot run` subprocess from the UI.

The scrape runs out of process on purpose: Playwright's sync API does not cooperate with Streamlit's
script thread, a crash in an adapter must not take the UI down, and `--headed` needs to open a browser
window on the user's desktop session (the child inherits WAYLAND_DISPLAY / DISPLAY).
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st


class RunManager:
    MODULE = "jobbot.cli"  # overridden in tests with a stub

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.log_dir = self.project_dir / "data" / "runs"
        self.proc: subprocess.Popen | None = None
        self.log_path: Path | None = None
        self.started_at: datetime | None = None
        self.command: list[str] = []
        self._fh = None

    # -- state ------------------------------------------------------------

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def returncode(self) -> int | None:
        if self.proc is None:
            return None
        rc = self.proc.poll()
        if rc is not None and self._fh:
            self._fh.close()
            self._fh = None
        return rc

    # -- control ----------------------------------------------------------

    def start(self, cli_args: list[str], config: Path | None = None) -> Path:
        if self.running():
            raise RuntimeError("a run is already in progress")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = datetime.now(timezone.utc)
        self.log_path = self.log_dir / f"{self.started_at:%Y%m%dT%H%M%SZ}.log"
        cmd = [sys.executable, "-u", "-m", self.MODULE]
        if config:
            cmd += ["-c", str(config)]
        cmd += list(cli_args)
        self.command = cmd
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        self._fh = self.log_path.open("w")
        self._fh.write(f"$ {shlex.join(cmd)}\n")
        self._fh.flush()
        self.proc = subprocess.Popen(
            cmd,
            cwd=self.project_dir,
            env=env,
            stdout=self._fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # own process group so stop() reaches Playwright's children
        )
        return self.log_path

    def stop(self, grace_seconds: float = 10) -> None:
        if not self.running():
            return
        pgid = os.getpgid(self.proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        try:
            self.proc.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            self.proc.wait()
        self.returncode  # closes the log handle

    # -- log --------------------------------------------------------------

    def tail(self, lines: int = 200, path: Path | None = None) -> str:
        path = path or self.log_path
        if not path or not path.exists():
            return ""
        with path.open("r", errors="replace") as fh:
            return "".join(deque(fh, maxlen=lines))

    def past_logs(self) -> list[Path]:
        if not self.log_dir.exists():
            return []
        return sorted(self.log_dir.glob("*.log"), reverse=True)


@st.cache_resource(show_spinner=False)
def get_runner(project_dir: str) -> RunManager:
    """One manager per server process: survives reruns, page changes and browser refreshes."""
    return RunManager(Path(project_dir))
