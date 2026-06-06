from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_ADAPT_FLOATING_POSITION_TO_OUTPUT,
    DEFAULT_ADAPT_TO_OUTPUT,
    DEFAULT_CONFIG,
    DEFAULT_DIALOG_MAX_HEIGHT_PX,
    DEFAULT_DIALOG_MAX_WIDTH_PX,
    DEFAULT_DIALOG_TITLE_PATTERNS,
    DEFAULT_FULLSCREEN_TOLERANCE_PX,
    DEFAULT_IGNORE_DIALOG_LIKE_WINDOWS,
    DEFAULT_LIVE_SAVE_DELAY_MS,
    DEFAULT_LIVE_UPDATES,
    DEFAULT_MAXIMIZED_WIDTH_TOLERANCE_PX,
    DEFAULT_RESTORE_DELAY_MS,
)
from .utils import parse_int, parse_string_list


@dataclass(frozen=True)
class DaemonConfig:
    enabled: bool = True
    restore_delay_ms: int = DEFAULT_RESTORE_DELAY_MS
    include_apps: tuple[str, ...] = ()
    exclude_apps: tuple[str, ...] = ()
    restore_size: bool = True
    restore_floating_state: bool = True
    restore_floating_position: bool = True
    restore_tiled_width: bool = True
    restore_tiled_height: bool = True
    restore_maximized: bool = True
    restore_fullscreen: bool = True
    adapt_to_output: bool = DEFAULT_ADAPT_TO_OUTPUT
    adapt_floating_position_to_output: bool = DEFAULT_ADAPT_FLOATING_POSITION_TO_OUTPUT
    fullscreen_tolerance_px: int = DEFAULT_FULLSCREEN_TOLERANCE_PX
    maximized_width_tolerance_px: int = DEFAULT_MAXIMIZED_WIDTH_TOLERANCE_PX
    live_updates: bool = DEFAULT_LIVE_UPDATES
    live_save_delay_ms: int = DEFAULT_LIVE_SAVE_DELAY_MS
    ignore_dialog_like_windows: bool = DEFAULT_IGNORE_DIALOG_LIKE_WINDOWS
    dialog_max_width_px: int = DEFAULT_DIALOG_MAX_WIDTH_PX
    dialog_max_height_px: int = DEFAULT_DIALOG_MAX_HEIGHT_PX
    dialog_title_patterns: tuple[str, ...] = DEFAULT_DIALOG_TITLE_PATTERNS
    ignore_title_patterns: tuple[str, ...] = ()
    ignore_app_title_patterns: tuple[tuple[str, str], ...] = ()
    working_area_offsets: dict[str, tuple[int, int]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None) -> "DaemonConfig":
        if path is None:
            return cls.default()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logging.info("Config file %s not found; using defaults", path)
            return cls.default()
        except (OSError, json.JSONDecodeError) as exc:
            logging.warning("Could not read config %s; using defaults: %s", path, exc)
            return cls.default()
        return cls.from_json(raw)

    @classmethod
    def default(cls) -> "DaemonConfig":
        return cls.from_json(DEFAULT_CONFIG)

    @classmethod
    def from_json(cls, raw: Any) -> "DaemonConfig":
        if not isinstance(raw, dict):
            return cls.default()

        apps = raw.get("apps")
        if not isinstance(apps, dict):
            apps = {}
        restore = raw.get("restore")
        if not isinstance(restore, dict):
            restore = {}
        detection = raw.get("detection")
        if not isinstance(detection, dict):
            detection = {}
        tracking = raw.get("tracking")
        if not isinstance(tracking, dict):
            tracking = {}

        restore_defaults = config_section("restore")
        detection_defaults = config_section("detection")
        tracking_defaults = config_section("tracking")

        delay_ms = parse_int(raw.get("restore_delay_ms", DEFAULT_CONFIG["restore_delay_ms"]))
        if delay_ms is None:
            delay_ms = DEFAULT_RESTORE_DELAY_MS
        live_save_delay_ms = parse_int(
            tracking.get("live_save_delay_ms", tracking_defaults.get("live_save_delay_ms"))
        )
        if live_save_delay_ms is None:
            live_save_delay_ms = DEFAULT_LIVE_SAVE_DELAY_MS
        fullscreen_tolerance_px = parse_int(
            detection.get("fullscreen_tolerance_px", detection_defaults.get("fullscreen_tolerance_px"))
        )
        if fullscreen_tolerance_px is None:
            fullscreen_tolerance_px = DEFAULT_FULLSCREEN_TOLERANCE_PX
        maximized_width_tolerance_px = parse_int(
            detection.get("maximized_width_tolerance_px", detection_defaults.get("maximized_width_tolerance_px"))
        )
        if maximized_width_tolerance_px is None:
            maximized_width_tolerance_px = DEFAULT_MAXIMIZED_WIDTH_TOLERANCE_PX
        dialog_max_width_px = parse_int(
            tracking.get("dialog_max_width_px", tracking_defaults.get("dialog_max_width_px"))
        )
        if dialog_max_width_px is None:
            dialog_max_width_px = DEFAULT_DIALOG_MAX_WIDTH_PX
        dialog_max_height_px = parse_int(
            tracking.get("dialog_max_height_px", tracking_defaults.get("dialog_max_height_px"))
        )
        if dialog_max_height_px is None:
            dialog_max_height_px = DEFAULT_DIALOG_MAX_HEIGHT_PX

        return cls(
            enabled=bool(raw.get("enabled", DEFAULT_CONFIG["enabled"])),
            restore_delay_ms=max(0, delay_ms),
            include_apps=tuple(parse_string_list(apps.get("include"))),
            exclude_apps=tuple(parse_string_list(apps.get("exclude"))),
            restore_size=bool(restore.get("size", restore_defaults.get("size"))),
            restore_floating_state=bool(restore.get("floating_state", restore_defaults.get("floating_state"))),
            restore_floating_position=bool(
                restore.get("floating_position", restore_defaults.get("floating_position"))
            ),
            restore_tiled_width=bool(restore.get("tiled_width", restore_defaults.get("tiled_width"))),
            restore_tiled_height=bool(restore.get("tiled_height", restore_defaults.get("tiled_height"))),
            restore_maximized=bool(restore.get("maximized", restore_defaults.get("maximized"))),
            restore_fullscreen=bool(restore.get("fullscreen", restore_defaults.get("fullscreen"))),
            adapt_to_output=bool(restore.get("adapt_to_output", restore_defaults.get("adapt_to_output"))),
            adapt_floating_position_to_output=bool(
                restore.get(
                    "adapt_floating_position_to_output",
                    restore_defaults.get("adapt_floating_position_to_output"),
                )
            ),
            fullscreen_tolerance_px=max(0, fullscreen_tolerance_px),
            maximized_width_tolerance_px=max(0, maximized_width_tolerance_px),
            live_updates=bool(tracking.get("live_updates", tracking_defaults.get("live_updates"))),
            live_save_delay_ms=max(0, live_save_delay_ms),
            ignore_dialog_like_windows=bool(
                tracking.get("ignore_dialog_like_windows", tracking_defaults.get("ignore_dialog_like_windows"))
            ),
            dialog_max_width_px=max(1, dialog_max_width_px),
            dialog_max_height_px=max(1, dialog_max_height_px),
            ignore_title_patterns=tuple(parse_string_list(tracking.get("ignore_title_patterns"))),
            ignore_app_title_patterns=tuple(
                parse_app_title_patterns(tracking.get("ignore_app_title_patterns"))
            ),
            dialog_title_patterns=tuple(
                parse_string_list(
                    tracking.get("dialog_title_patterns", tracking_defaults.get("dialog_title_patterns"))
                )
            ) or DEFAULT_DIALOG_TITLE_PATTERNS,
            working_area_offsets=parse_working_area_offsets(raw.get("working_area_offsets")),
        )

    def allows_app(self, app_id: str | None) -> bool:
        if not self.enabled or not app_id:
            return False
        if self.include_apps and app_id not in self.include_apps:
            return False
        return app_id not in self.exclude_apps

    @property
    def restore_delay_seconds(self) -> float:
        return self.restore_delay_ms / 1000


def config_section(name: str) -> dict[str, Any]:
    section = DEFAULT_CONFIG.get(name)
    if not isinstance(section, dict):
        raise KeyError(f"DEFAULT_CONFIG section {name!r} is missing or invalid")
    return section


def parse_app_title_patterns(raw: Any) -> list[tuple[str, str]]:
    if not isinstance(raw, list):
        return []
    patterns: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        app_id = item.get("app_id")
        title = item.get("title")
        if isinstance(app_id, str) and app_id and isinstance(title, str) and title:
            patterns.append((app_id, title))
    return patterns


def parse_working_area_offsets(raw: Any) -> dict[str, tuple[int, int]]:
    if not isinstance(raw, dict):
        return {}
    offsets: dict[str, tuple[int, int]] = {}
    for output_name, offset_data in raw.items():
        if not isinstance(output_name, str) or not output_name:
            continue
        if not isinstance(offset_data, dict):
            continue
        x = parse_int(offset_data.get("x"))
        y = parse_int(offset_data.get("y"))
        if x is not None and y is not None:
            offsets[output_name] = (x, y)
    return offsets
