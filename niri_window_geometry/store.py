from __future__ import annotations

import json
import logging
import tempfile
import threading
from pathlib import Path

from .constants import STATE_VERSION
from .models import WindowGeometry
from .utils import utc_now


class StateStore:
    def __init__(self, path: Path, dry_run: bool = False) -> None:
        self.path = path
        self.dry_run = dry_run
        self.apps: dict[str, WindowGeometry] = {}
        self._lock = threading.RLock()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            with self._lock:
                self.apps = {}
            return
        except (OSError, json.JSONDecodeError) as exc:
            logging.warning("Starting with empty state; could not read %s: %s", self.path, exc)
            with self._lock:
                self.apps = {}
            return

        apps = raw.get("apps") if isinstance(raw, dict) else None
        if not isinstance(apps, dict):
            with self._lock:
                self.apps = {}
            return

        loaded: dict[str, WindowGeometry] = {}
        for app_id, geometry_data in apps.items():
            if not isinstance(app_id, str) or not app_id:
                continue
            geometry = WindowGeometry.from_json(geometry_data)
            if geometry is not None:
                loaded[app_id] = geometry
        with self._lock:
            self.apps = loaded

    def save(self) -> None:
        if self.dry_run:
            logging.info("Dry run: not writing state to %s", self.path)
            return

        with self._lock:
            payload = {
                "version": STATE_VERSION,
                "apps": {app_id: geometry.to_json() for app_id, geometry in sorted(self.apps.items())},
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as tmp:
                json.dump(payload, tmp, indent=2, sort_keys=True)
                tmp.write("\n")
                tmp_path = Path(tmp.name)
            tmp_path.replace(self.path)
        except OSError:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            raise

    def get(self, app_id: str) -> WindowGeometry | None:
        with self._lock:
            return self.apps.get(app_id)

    def put(self, app_id: str, geometry: WindowGeometry, write: bool = True) -> None:
        with self._lock:
            self.apps[app_id] = WindowGeometry(
                width=geometry.width,
                height=geometry.height,
                is_floating=geometry.is_floating,
                mode=geometry.mode,
                floating_x=geometry.floating_x,
                floating_y=geometry.floating_y,
                output_width=geometry.output_width,
                output_height=geometry.output_height,
                updated_at=utc_now(),
            )
        if write:
            self.save()
