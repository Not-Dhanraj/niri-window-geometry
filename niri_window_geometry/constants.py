from __future__ import annotations

import os
from pathlib import Path
from typing import Any


STATE_VERSION = 1
DEFAULT_STATE_FILE = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    / "niri-window-geometry"
    / "state.json"
)

DEFAULT_CONFIG_FILE = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "niri-window-geometry"
    / "config.json"
)
MIN_SIZE = 1
MAX_SIZE = 100_000
DEFAULT_RESTORE_DELAY_MS = 150
DEFAULT_LIVE_UPDATES = True
DEFAULT_LIVE_SAVE_DELAY_MS = 500
DEFAULT_ADAPT_TO_OUTPUT = True
DEFAULT_ADAPT_FLOATING_POSITION_TO_OUTPUT = True
DEFAULT_FULLSCREEN_TOLERANCE_PX = 2
DEFAULT_MAXIMIZED_WIDTH_TOLERANCE_PX = 32
MODE_NORMAL = "normal"
MODE_MAXIMIZED = "maximized"
MODE_FULLSCREEN = "fullscreen"
VALID_MODES = {MODE_NORMAL, MODE_MAXIMIZED, MODE_FULLSCREEN}


DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "restore_delay_ms": DEFAULT_RESTORE_DELAY_MS,
    "apps": {
        "include": [],
        "exclude": [],
    },
    "restore": {
        "size": True,
        "floating_state": True,
        "floating_position": True,
        "tiled_width": True,
        "tiled_height": True,
        "maximized": True,
        "fullscreen": True,
        "adapt_to_output": DEFAULT_ADAPT_TO_OUTPUT,
        "adapt_floating_position_to_output": DEFAULT_ADAPT_FLOATING_POSITION_TO_OUTPUT,
    },
    "detection": {
        "fullscreen_tolerance_px": DEFAULT_FULLSCREEN_TOLERANCE_PX,
        "maximized_width_tolerance_px": DEFAULT_MAXIMIZED_WIDTH_TOLERANCE_PX,
    },
    "tracking": {
        "live_updates": DEFAULT_LIVE_UPDATES,
        "live_save_delay_ms": DEFAULT_LIVE_SAVE_DELAY_MS,
    },
}
