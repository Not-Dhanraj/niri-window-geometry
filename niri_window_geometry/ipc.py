from __future__ import annotations

from typing import Any

from .models import WindowSnapshot, WindowGeometry, OutputSize
from .utils import clamp_int, parse_int


def cached_window_from_payload(payload: Any) -> WindowSnapshot | None:
    if not isinstance(payload, dict):
        return None

    window_id = parse_window_id(payload)
    if window_id is None:
        return None

    app_id = payload.get("app_id")
    if not isinstance(app_id, str) or not app_id:
        app_id = None

    is_floating = payload.get("is_floating")
    if not isinstance(is_floating, bool):
        is_floating = None

    layout = payload.get("layout")
    geometry = geometry_from_layout(layout, is_floating=is_floating)
    workspace_id = parse_int(payload.get("workspace_id"))
    is_focused = bool(payload.get("is_focused"))
    return WindowSnapshot(
        id=window_id,
        app_id=app_id,
        workspace_id=workspace_id,
        is_focused=is_focused,
        geometry=geometry,
    )


def geometry_from_layout(layout: Any, is_floating: bool | None = None) -> WindowGeometry | None:
    if not isinstance(layout, dict):
        return None

    visual_size = layout.get("tile_size") or layout.get("window_size") or layout.get("size")
    width, height = parse_size(visual_size)
    if width is None or height is None:
        return None

    if is_floating is None:
        is_floating = bool(
            layout.get("is_floating")
            or layout.get("floating")
            or layout.get("layout") == "Floating"
            or layout.get("window_type") == "Floating"
        )

    pos = layout.get("tile_pos_in_workspace_view") or layout.get("pos") or layout.get("position")
    floating_x, floating_y = parse_position(pos)
    if floating_x is None:
        floating_x = parse_int(layout.get("x"))
    if floating_y is None:
        floating_y = parse_int(layout.get("y"))

    return WindowGeometry(
        width=width,
        height=height,
        is_floating=is_floating,
        floating_x=floating_x if is_floating else None,
        floating_y=floating_y if is_floating else None,
    )


def iter_layout_changes(changes: dict[Any, Any] | list[Any]) -> list[tuple[Any, Any]]:
    if isinstance(changes, dict):
        return list(changes.items())

    parsed: list[tuple[Any, Any]] = []
    for item in changes:
        if isinstance(item, list | tuple) and len(item) >= 2:
            parsed.append((item[0], item[1]))
    return parsed


def output_geometries_from_json(raw: Any) -> dict[str, OutputSize]:
    if not isinstance(raw, dict):
        return {}

    outputs: dict[str, OutputSize] = {}
    for name, output in raw.items():
        if not isinstance(name, str) or not isinstance(output, dict):
            continue
        logical = output.get("logical")
        if not isinstance(logical, dict):
            continue
        width = clamp_int(logical.get("width"))
        height = clamp_int(logical.get("height"))
        if width is not None and height is not None:
            outputs[name] = OutputSize(width=width, height=height)
    return outputs


def parse_size(value: Any) -> tuple[int | None, int | None]:
    if isinstance(value, dict):
        return clamp_int(value.get("w") or value.get("width")), clamp_int(value.get("h") or value.get("height"))
    if isinstance(value, list | tuple) and len(value) >= 2:
        return clamp_int(value[0]), clamp_int(value[1])
    return None, None


def parse_position(value: Any) -> tuple[int | None, int | None]:
    if isinstance(value, dict):
        return parse_int(value.get("x")), parse_int(value.get("y"))
    if isinstance(value, list | tuple) and len(value) >= 2:
        return parse_int(value[0]), parse_int(value[1])
    return None, None


def parse_window_id(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key in ("id", "window_id"):
            window_id = parse_int(payload.get(key))
            if window_id is not None:
                return window_id
    return parse_int(payload)


def event_variant(event: dict[str, Any]) -> tuple[str | None, Any]:
    if "event" in event and isinstance(event["event"], dict):
        return event_variant(event["event"])
    if "type" in event and isinstance(event["type"], str):
        payload = event.get("payload", event.get("data", event))
        return event["type"], payload

    if len(event) == 1:
        key, value = next(iter(event.items()))
        if isinstance(key, str):
            return key, value

    for key in (
        "WindowsChanged",
        "WorkspacesChanged",
        "WindowOpenedOrChanged",
        "WindowLayoutsChanged",
        "WindowClosed",
    ):
        if key in event:
            return key, event[key]

    return None, None
