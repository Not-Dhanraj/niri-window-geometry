from __future__ import annotations

import logging
import subprocess
import threading


class ShutdownController:
    def __init__(self) -> None:
        self._requested = False
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()

    def requested(self) -> bool:
        with self._lock:
            return self._requested

    def set_process(self, process: subprocess.Popen[str] | None) -> None:
        with self._lock:
            self._process = process
            should_terminate = self._requested

        if should_terminate and process is not None and process.poll() is None:
            process.terminate()

    def stop(self, signum: int) -> None:
        with self._lock:
            self._requested = True
            process = self._process

        logging.info("Received signal %s, stopping", signum)
        if process is not None and process.poll() is None:
            process.terminate()
