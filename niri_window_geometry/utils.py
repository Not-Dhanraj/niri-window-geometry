from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .constants import MAX_SIZE, MIN_SIZE, MODE_NORMAL, VALID_MODES


def parse_mode(value: Any) -> str:
    if isinstance(value, str) and value in VALID_MODES:
        return value
    return MODE_NORMAL


def clamp_int(value: Any) -> int | None:
    number = parse_int(value)
    if number is None:
        return None
    return max(MIN_SIZE, min(MAX_SIZE, number))


def parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            try:
                return int(round(float(value)))
            except ValueError:
                return None
    return None


def parse_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def shell_join(command: list[str]) -> str:
    return " ".join(command)
