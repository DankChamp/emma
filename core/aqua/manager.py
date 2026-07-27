"""
AquaManager — launches, monitors, and stops the Aqua server as a subprocess.

Emma uses this to manage Aqua's lifecycle: start it when a research task
comes in, keep it alive with periodic health checks, and shut it down when
not needed.
"""
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger("emma.aqua")


class AquaManager:
    def __init__(self, project_dir: str, api_url: str, api_key: str = ""):
        self.project_dir = Path(project_dir).resolve()
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self._process: Optional[subprocess.Popen] = None
        self._started_at: Optional[float] = None

    @property
    def is_running(self) -> bool:
        if self._process is None:
            return False
        if self._process.poll() is not None:
            self._process = None
            self._started_at = None
            return False
        return True

    @property
    def uptime(self) -> Optional[float]:
        if self._started_at is None:
            return None
        return time.time() - self._started_at

    @property
    def pid(self) -> Optional[int]:
        if self._process is not None and self._process.pid is not None:
            return self._process.pid
        return None

    async def launch(self) -> bool:
        if self.is_running:
            logger.info("Aqua is already running (pid %s)", self.pid)
            return True

        log_path = str(self.project_dir / "aqua.log")
        venv_python = self.project_dir / ".venv" / "bin" / "python"
        python = venv_python if venv_python.exists() else "python3"
        cmd = [
            str(python), "-m", "uvicorn", "main:app",
            "--host", "127.0.0.1",
            "--port", str(self._port_from_url()),
        ]

        logger.info("Launching Aqua: %s", " ".join(cmd))
        try:
            log_file = open(log_path, "a")
        except OSError as exc:
            logger.error("Cannot open log file %s: %s", log_path, exc)
            return False

        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=str(self.project_dir),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
            self._started_at = time.time()
            logger.info("Aqua started (pid %s, log: %s)", self.pid, log_path)
            return True
        except FileNotFoundError as exc:
            logger.error("Failed to launch Aqua: %s", exc)
            log_file.close()
            self._process = None
            return False

    def stop(self) -> bool:
        if not self.is_running:
            logger.info("Aqua is not running")
            return True

        try:
            pgid = os.getpgid(self._process.pid)
            os.killpg(pgid, signal.SIGTERM)
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(pgid, signal.SIGKILL)
                self._process.wait(timeout=5)
        except (ProcessLookupError, PermissionError) as exc:
            logger.warning("Could not stop Aqua: %s", exc)

        self._process = None
        self._started_at = None
        logger.info("Aqua stopped")
        return True

    async def health(self) -> dict:
        from core.aqua.client import AquaClient

        client = AquaClient(self.api_url, self.api_key)
        alive = await client.is_connected()
        return {
            "running": self.is_running,
            "alive": alive,
            "pid": self.pid,
            "uptime": round(self.uptime, 1) if self.uptime else None,
            "url": self.api_url,
        }

    def _port_from_url(self) -> int:
        try:
            parsed = urlparse(self.api_url)
            if parsed.port:
                return parsed.port
        except Exception:
            pass
        return 8765
