from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .constants import MODE_NORMAL
from .utils import clamp_int, parse_int, parse_mode, utc_now


@dataclass(frozen=True)
class WindowGeometry:
    width: int
    height: int
    is_floating: bool
    mode: str = MODE_NORMAL
    floating_x: int | None = None
    floating_y: int | None = None
    output_width: int | None = None
    output_height: int | None = None
    updated_at: str | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "width": self.width,
            "height": self.height,
            "is_floating": self.is_floating,
            "mode": self.mode,
            "updated_at": self.updated_at or utc_now(),
        }
        if self.is_floating and self.floating_x is not None and self.floating_y is not None:
            data["floating_x"] = self.floating_x
            data["floating_y"] = self.floating_y
        if self.output_width is not None and self.output_height is not None:
            data["output_width"] = self.output_width
            data["output_height"] = self.output_height
        return data

    @classmethod
    def from_json(cls, data: Any) -> "WindowGeometry | None":
        if not isinstance(data, dict):
            return None

        width = clamp_int(data.get("width"))
        height = clamp_int(data.get("height"))
        if width is None or height is None:
            return None

        is_floating = bool(data.get("is_floating"))
        mode = parse_mode(data.get("mode"))
        floating_x = parse_int(data.get("floating_x"))
        floating_y = parse_int(data.get("floating_y"))
        output_width = clamp_int(data.get("output_width"))
        output_height = clamp_int(data.get("output_height"))
        updated_at = data.get("updated_at")
        if not isinstance(updated_at, str):
            updated_at = None

        return cls(
            width=width,
            height=height,
            is_floating=is_floating,
            mode=mode,
            floating_x=floating_x,
            floating_y=floating_y,
            output_width=output_width,
            output_height=output_height,
            updated_at=updated_at,
        )


@dataclass
class WindowSnapshot:
    id: int
    app_id: str | None
    workspace_id: int | None
    is_focused: bool
    geometry: WindowGeometry | None


@dataclass(frozen=True)
class OutputSize:
    width: int
    height: int
