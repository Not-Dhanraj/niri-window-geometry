#!/usr/bin/env python3
"""Calibrate working area offsets for niri-window-geometry.

Detects the panel/bar offset on each output by moving a floating window
to (0, 0) and measuring the difference between what `move-floating-window`
sets (working-area coordinates) and what `tile_pos_in_workspace_view`
reports (output coordinates).

Run this once after installing niri-window-geometry, and re-run whenever
your monitor or panel/bar configuration changes.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "niri-window-geometry"
CONFIG_FILE = CONFIG_DIR / "config.json"


def niri_query(*args: str) -> object:
    result = subprocess.run(
        ["niri", "msg", "--json", *args],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def niri_action(*args: str) -> None:
    subprocess.run(["niri", "msg", "action", *args], check=True)


def niri_query_outputs() -> dict[str, dict]:
    raw = niri_query("outputs")
    if not isinstance(raw, dict):
        return {}
    return {
        name: output
        for name, output in raw.items()
        if isinstance(name, str) and isinstance(output, dict) and "logical" in output
    }


def niri_query_workspaces() -> list[dict]:
    raw = niri_query("workspaces")
    return raw if isinstance(raw, list) else []


def niri_query_floating_window() -> dict | None:
    raw = niri_query("windows")
    if not isinstance(raw, list):
        return None
    for window in raw:
        if isinstance(window, dict) and window.get("is_floating"):
            return window
    return None


def find_window_on_output(output_name: str, workspaces: list[dict]) -> dict | None:
    """Get a floating window on the given output, or prompt user to create one."""
    workspace_ids = {
        ws["id"]
        for ws in workspaces
        if isinstance(ws, dict) and ws.get("output") == output_name
    }

    raw = niri_query("windows")
    if not isinstance(raw, list):
        return None

    for window in raw:
        if not isinstance(window, dict):
            continue
        ws_id = window.get("workspace_id")
        if ws_id in workspace_ids and window.get("is_floating"):
            return window

    print(f"\n  No floating window found on output '{output_name}'.")
    print(f"  Please open any window and put it to floating mode in this monitor.")
    input("  Press Enter when ready... ")

    raw = niri_query("windows")
    if not isinstance(raw, list):
        return None
    for window in raw:
        if not isinstance(window, dict):
            continue
        ws_id = window.get("workspace_id")
        if ws_id in workspace_ids and window.get("is_floating"):
            return window

    return None


def calibrate_output(output_name: str, output_info: dict, workspaces: list[dict]) -> tuple[int, int] | None:
    """Calibrate a single output's working area offset."""
    print(f"\n{'='*60}")
    print(f"Calibrating output: {output_name}")
    logical = output_info.get("logical", {})
    if isinstance(logical, dict):
        print(f"  Resolution: {logical.get('width')}x{logical.get('height')}")
        scale = logical.get("scale", 1.0)
        if scale != 1.0:
            print(f"  Scale: {scale}")

    window = find_window_on_output(output_name, workspaces)
    if window is None:
        print(f"  Could not find a floating window on {output_name}. Skipping.")
        return None

    window_id = window["id"]
    window_title = window.get("title") or window.get("app_id", "unknown")
    print(f"  Using window: id={window_id} ({window_title})")

    layout = window.get("layout", {})
    original_pos = None
    if isinstance(layout, dict):
        pos = layout.get("tile_pos_in_workspace_view")
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            original_pos = (int(pos[0]), int(pos[1]))

    # Move to a known position far from edges to avoid clamping.
    # On a 1920x1080 output, (300, 300) is safely within the working area.
    cal_x, cal_y = 300, 300
    print(f"  Moving to ({cal_x}, {cal_y})...")
    niri_action("move-floating-window", "--id", str(window_id),
                "--x", str(cal_x), "--y", str(cal_y))
    time.sleep(0.3)

    raw = niri_query("windows")
    measured_pos = None
    if isinstance(raw, list):
        for w in raw:
            if isinstance(w, dict) and w.get("id") == window_id:
                w_layout = w.get("layout", {})
                pos = w_layout.get("tile_pos_in_workspace_view") if isinstance(w_layout, dict) else None
                if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                    measured_pos = (round(pos[0]), round(pos[1]))
                break

    if measured_pos is None:
        print(f"  Could not read back position. Skipping.")
        return None

    # niri's move-floating-window sets output coordinates.
    # tile_pos_in_workspace_view reports working-area coordinates
    # (relative to working area, excluding panels/bars).
    #   offset = set_value - reported_value
    # On a monitor with a 40px top bar: set_y=300 → reported=260 → offset_y=40
    offset_x = cal_x - measured_pos[0]
    offset_y = cal_y - measured_pos[1]
    print(f"  Set position:       ({cal_x}, {cal_y})")
    print(f"  Reported (working area): ({measured_pos[0]}, {measured_pos[1]})")
    print(f"  Working area offset: x={offset_x}, y={offset_y}")

    if original_pos is not None:
        niri_action("move-floating-window", "--id", str(window_id),
                     "--x", str(original_pos[0]), "--y", str(original_pos[1]))

    return (offset_x, offset_y)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"\nConfig written to {CONFIG_FILE}")


def main() -> int:
    print("niri-window-geometry: Working Area Calibration")
    print("==============================================")
    print()
    print("This tool detects the panel/bar offset on each monitor so that")
    print("floating window positions are saved and restored correctly.")
    print()
    print("It works by temporarily moving a floating window to (0, 0) on")
    print("each output and measuring the actual position niri reports.")
    print()

    try:
        outputs = niri_query_outputs()
        workspaces = niri_query_workspaces()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"Error: Could not query niri: {exc}", file=sys.stderr)
        print("Make sure niri is running and 'niri msg' is available.", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Error: Invalid JSON from niri: {exc}", file=sys.stderr)
        return 1

    if not outputs:
        print("No outputs detected from niri. Is niri running?", file=sys.stderr)
        return 1

    print(f"Detected {len(outputs)} output(s):")
    for name, info in outputs.items():
        logical = info.get("logical", {})
        if isinstance(logical, dict):
            print(f"  - {name}: {logical.get('width')}x{logical.get('height')}")

    config = load_config()
    existing_offsets = config.get("working_area_offsets", {})

    all_offsets: dict[str, dict[str, int]] = {}
    for output_name, output_info in outputs.items():
        offset = calibrate_output(output_name, output_info, workspaces)
        if offset is not None:
            all_offsets[output_name] = {"x": offset[0], "y": offset[1]}
        elif output_name in existing_offsets:
            # Keep existing offset if calibration is skipped
            existing = existing_offsets[output_name]
            if isinstance(existing, dict):
                all_offsets[output_name] = existing

    if not all_offsets:
        print("\nNo offsets were calibrated. Config unchanged.", file=sys.stderr)
        return 1

    config["working_area_offsets"] = all_offsets
    save_config(config)

    print("\nSummary of working area offsets:")
    for output_name, offset in all_offsets.items():
        print(f"  {output_name}: x={offset['x']}, y={offset['y']}")
    print("\nCalibration complete. Restart the niri-window-geometry daemon to apply.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
