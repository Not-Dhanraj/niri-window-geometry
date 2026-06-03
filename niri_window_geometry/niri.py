from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any, Callable

from .utils import shell_join


class NiriClient:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.dry_run_commands: list[list[str]] = []

    def run(self, action_args: list[str]) -> None:
        command = ["niri", "msg", "action", *action_args]
        if self.dry_run:
            self.dry_run_commands.append(command)
            logging.info("Dry run: %s", shell_join(command))
            return

        try:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        except subprocess.CalledProcessError as exc:
            logging.warning("Command failed: %s: %s", shell_join(command), exc.stderr.strip())

    def query_json(self, query_args: list[str]) -> Any:
        command = ["niri", "msg", "--json", *query_args]
        try:
            completed = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except subprocess.CalledProcessError as exc:
            logging.warning("Query failed: %s: %s", shell_join(command), exc.stderr.strip())
            return None
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            logging.warning("Query returned invalid JSON: %s: %s", shell_join(command), exc)
            return None


def event_stream() -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["niri", "msg", "--json", "event-stream"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def run_forever(
    daemon: Any,
    stop_requested: Callable[[], bool],
    set_process: Callable[[subprocess.Popen[str] | None], None] | None = None,
) -> None:
    backoff_seconds = 1.0
    while not stop_requested():
        logging.info("Starting niri event stream")
        process = event_stream()
        if set_process is not None:
            set_process(process)
        try:
            assert process.stdout is not None
            for line in process.stdout:
                if stop_requested():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    logging.warning("Ignoring invalid JSON event: %s", exc)
                    continue
                if isinstance(event, dict):
                    backoff_seconds = 1.0
                    daemon.handle_event(event)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                _, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                _, stderr = process.communicate()
            if set_process is not None:
                set_process(None)

        if stop_requested():
            break

        if stderr:
            logging.warning("niri event stream ended: %s", stderr.strip())
        else:
            logging.warning("niri event stream ended")
        time.sleep(backoff_seconds)
        backoff_seconds = min(backoff_seconds * 2, 10.0)
