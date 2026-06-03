from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable

from .config import DaemonConfig
from .constants import MODE_FULLSCREEN, MODE_MAXIMIZED, MODE_NORMAL, VALID_MODES
from .ipc import cached_window_from_payload, event_variant, geometry_from_layout, iter_layout_changes, parse_window_id
from .models import WindowSnapshot, WindowGeometry, OutputSize
from .niri import NiriClient
from .store import StateStore
from .utils import parse_int


class WindowRestoreDaemon:
    sync_restores = False

    def __init__(
        self,
        store: StateStore,
        niri_client: NiriClient,
        config: DaemonConfig | None = None,
        outputs: dict[str, OutputSize] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        sync_restores: bool | None = None,
    ) -> None:
        self.store = store
        self.niri_client = niri_client
        self.config = config or DaemonConfig()
        self.outputs = outputs or {}
        self._single_output = next(iter(self.outputs.values())) if len(self.outputs) == 1 else None
        self.sleeper = sleeper
        if sync_restores is not None:
            self.sync_restores = sync_restores
        else:
            self.sync_restores = getattr(self.__class__, "sync_restores", False)
        self._lock = threading.RLock()
        self._restore_command_lock = threading.RLock()
        self.windows: dict[int, WindowSnapshot] = {}
        self.window_generations: dict[int, int] = {}
        self._next_generation = 0
        self.workspace_outputs: dict[int, str] = {}
        self.ignored_window_ids: set[int] = set()
        self.restored_window_ids: set[int] = set()
        self._live_save_timer: threading.Timer | None = None

    def handle_event(self, event: dict[str, Any]) -> None:
        event_type, payload = event_variant(event)
        if event_type is None:
            logging.debug("Ignoring unknown event: %r", event)
            return

        if event_type == "WindowsChanged":
            self._handle_windows_changed(payload)
        elif event_type == "WorkspacesChanged":
            self._handle_workspaces_changed(payload)
        elif event_type == "WindowOpenedOrChanged":
            self._handle_window_opened_or_changed(payload)
        elif event_type == "WindowLayoutsChanged":
            self._handle_window_layouts_changed(payload)
        elif event_type == "WindowClosed":
            self._handle_window_closed(payload)

    def _handle_windows_changed(self, payload: Any) -> None:
        windows = payload.get("windows") if isinstance(payload, dict) else payload
        if not isinstance(windows, list):
            return

        windows_by_id: dict[int, WindowSnapshot] = {}
        for window in windows:
            snapshot = cached_window_from_payload(window)
            if snapshot is not None:
                windows_by_id[snapshot.id] = snapshot
        live_saves: list[tuple[str, WindowGeometry]] = []
        with self._lock:
            previous_generations = self.window_generations
            self.windows = windows_by_id
            next_generations: dict[int, int] = {}
            for window_id in windows_by_id:
                if window_id in previous_generations:
                    next_generations[window_id] = previous_generations[window_id]
                else:
                    next_generations[window_id] = self._next_window_generation()
            self.window_generations = next_generations
            self.ignored_window_ids = {
                window.id for window in windows_by_id.values() if self.should_ignore_window(window)
            }
            self.restored_window_ids.intersection_update(windows_by_id)
            for window in windows_by_id.values():
                live_geometry = self.live_geometry_for(window)
                app_id = window.app_id
                if app_id is not None and live_geometry is not None:
                    live_saves.append((app_id, live_geometry))
        logging.debug("Loaded %d initial windows", len(windows_by_id))
        for app_id, geometry in live_saves:
            self.save_live_geometry_for_app(app_id, geometry)

    def _handle_workspaces_changed(self, payload: Any) -> None:
        workspaces = payload.get("workspaces") if isinstance(payload, dict) else payload
        if not isinstance(workspaces, list):
            return

        mapping: dict[int, str] = {}
        for workspace in workspaces:
            if not isinstance(workspace, dict):
                continue
            workspace_id = parse_int(workspace.get("id"))
            output = workspace.get("output")
            if workspace_id is not None and isinstance(output, str) and output:
                mapping[workspace_id] = output
        with self._lock:
            self.workspace_outputs = mapping
        logging.debug("Loaded %d workspace output mappings", len(mapping))

    def _handle_window_opened_or_changed(self, payload: Any) -> None:
        window = payload.get("window") if isinstance(payload, dict) else payload
        snapshot = cached_window_from_payload(window)
        if snapshot is None:
            return

        generation: int | None
        with self._lock:
            previous = self.windows.get(snapshot.id)
            self.windows[snapshot.id] = snapshot
            if previous is None:
                generation = self._next_window_generation()
                self.window_generations[snapshot.id] = generation
            else:
                generation = self.window_generations.get(snapshot.id)

            should_ignore = snapshot.id in self.ignored_window_ids or self.should_ignore_window(snapshot)
            if should_ignore:
                self.ignored_window_ids.add(snapshot.id)
            elif previous is None:
                self.ignored_window_ids.discard(snapshot.id)

        if previous is None:
            logging.info("Window opened: %s ignored=%s", self.window_summary(snapshot), should_ignore)

        if should_ignore:
            return

        if previous is None:
            self.dispatch_restore(snapshot, generation)
        else:
            self.save_live_geometry(snapshot)

    def _handle_window_layouts_changed(self, payload: Any) -> None:
        changes = payload.get("changes") if isinstance(payload, dict) else payload
        if not isinstance(changes, dict | list):
            return

        live_saves: list[tuple[str, WindowGeometry]] = []
        with self._lock:
            for raw_id, layout in iter_layout_changes(changes):
                window_id = parse_int(raw_id)
                if window_id is None or window_id not in self.windows:
                    continue

                snapshot = self.windows[window_id]
                is_floating = snapshot.geometry.is_floating if snapshot.geometry is not None else None
                geometry = geometry_from_layout(layout, is_floating=is_floating)
                if geometry is None:
                    continue
                updated = WindowSnapshot(
                    snapshot.id,
                    snapshot.app_id,
                    snapshot.workspace_id,
                    snapshot.is_focused,
                    geometry,
                    snapshot.title,
                )
                self.windows[window_id] = updated
                if window_id in self.ignored_window_ids:
                    continue
                live_geometry = self.live_geometry_for(updated)
                app_id = updated.app_id
                if app_id is not None and live_geometry is not None:
                    live_saves.append((app_id, live_geometry))

        for app_id, geometry in live_saves:
            self.save_live_geometry_for_app(app_id, geometry)

    def _handle_window_closed(self, payload: Any) -> None:
        window_id = parse_window_id(payload)
        if window_id is None:
            return

        with self._lock:
            snapshot = self.windows.pop(window_id, None)
            self.window_generations.pop(window_id, None)
            self.restored_window_ids.discard(window_id)
            ignored = window_id in self.ignored_window_ids
            self.ignored_window_ids.discard(window_id)
            if ignored:
                return
            app_id = snapshot.app_id if snapshot is not None else None
            if snapshot is None or snapshot.geometry is None or app_id is None or not self.config.allows_app(app_id):
                return
            geometry = self.geometry_with_mode(snapshot)

        logging.info(
            "Saving %s geometry: %dx%d floating=%s pos=%s,%s",
            app_id,
            geometry.width,
            geometry.height,
            geometry.is_floating,
            geometry.floating_x,
            geometry.floating_y,
        )
        self.store.put(app_id, geometry)

    def live_geometry_for(self, window: WindowSnapshot) -> WindowGeometry | None:
        if not self.config.live_updates:
            return None
        if window.geometry is None or not self.config.allows_app(window.app_id):
            return None
        if window.id in self.ignored_window_ids:
            return None
        return self.geometry_with_mode(window)

    def save_live_geometry(self, window: WindowSnapshot) -> None:
        app_id = window.app_id
        if app_id is None:
            return
        geometry = self.live_geometry_for(window)
        if geometry is None:
            return
        self.save_live_geometry_for_app(app_id, geometry)

    def save_live_geometry_for_app(self, app_id: str, geometry: WindowGeometry) -> None:
        logging.debug(
            "Live-updating %s geometry: %dx%d floating=%s",
            app_id,
            geometry.width,
            geometry.height,
            geometry.is_floating,
        )
        self.store.put(app_id, geometry, write=False)
        self.schedule_live_state_save()

    def schedule_live_state_save(self) -> None:
        if self.config.live_save_delay_ms <= 0:
            self.store.save()
            return

        with self._lock:
            if self._live_save_timer is not None:
                self._live_save_timer.cancel()

            timer: threading.Timer

            def flush_timer() -> None:
                self.flush_live_state(timer)

            timer = threading.Timer(self.config.live_save_delay_ms / 1000, flush_timer)
            timer.daemon = True
            self._live_save_timer = timer
            timer.start()

    def flush_live_state(self, timer: threading.Timer | None = None) -> None:
        with self._lock:
            if self._live_save_timer is None:
                return
            if timer is not None and timer is not self._live_save_timer:
                return
            if timer is None:
                self._live_save_timer.cancel()
            self._live_save_timer = None
        self.store.save()

    def _next_window_generation(self) -> int:
        self._next_generation += 1
        return self._next_generation

    def should_ignore_window(self, window: WindowSnapshot) -> bool:
        if self.title_is_ignored(window.title):
            return True
        if not self.config.ignore_dialog_like_windows:
            return False
        if window.app_id is None or window.geometry is None or not window.geometry.is_floating:
            return False
        if window.geometry.width > self.config.dialog_max_width_px:
            return False
        if window.geometry.height > self.config.dialog_max_height_px:
            return False

        if self.has_other_window_for_app(window):
            return True

        if self.title_looks_like_dialog(window.title):
            return True

        saved = self.store.get(window.app_id)
        if saved is not None:
            if not saved.is_floating:
                return True
            if saved.width > window.geometry.width * 1.5:
                return True
            if saved.height > window.geometry.height * 1.5:
                return True

        return False

    def title_is_ignored(self, title: str | None) -> bool:
        if title is None:
            return False
        for pattern in self.config.ignore_title_patterns:
            try:
                if re.search(pattern, title):
                    return True
            except re.error as exc:
                logging.warning("Ignoring invalid title pattern %r: %s", pattern, exc)
        return False

    def title_looks_like_dialog(self, title: str | None) -> bool:
        if title is None:
            return False
        for pattern in self.config.dialog_title_patterns:
            try:
                if re.search(pattern, title):
                    return True
            except re.error as exc:
                logging.warning("Ignoring invalid dialog title pattern %r: %s", pattern, exc)
        return False

    def has_other_window_for_app(self, window: WindowSnapshot) -> bool:
        if window.app_id is None:
            return False
        return any(
            other.id != window.id and other.app_id == window.app_id
            for other in self.windows.values()
        )

    def window_summary(self, window: WindowSnapshot) -> str:
        parts = [
            f"id={window.id}",
            f"app_id={self.log_value(window.app_id)}",
            f"title={self.log_value(window.title)}",
            f"workspace={window.workspace_id if window.workspace_id is not None else 'unknown'}",
            f"focused={window.is_focused}",
        ]
        if window.geometry is None:
            parts.append("geometry=unknown")
        else:
            parts.append(self.geometry_summary(window.geometry))
            parts.append(f"detected_mode={self.current_mode(window)}")
        return " ".join(parts)

    def geometry_summary(self, geometry: WindowGeometry) -> str:
        parts = [
            f"size={geometry.width}x{geometry.height}",
            f"floating={geometry.is_floating}",
            f"mode={geometry.mode}",
        ]
        if geometry.floating_x is not None and geometry.floating_y is not None:
            parts.append(f"pos={geometry.floating_x},{geometry.floating_y}")
        if geometry.output_width is not None and geometry.output_height is not None:
            parts.append(f"output={geometry.output_width}x{geometry.output_height}")
        return " ".join(parts)

    def log_value(self, value: str | None) -> str:
        if value is None:
            return "unknown"
        return repr(value)

    def dispatch_restore(self, window: WindowSnapshot, generation: int | None) -> None:
        if self.sync_restores:
            self.restore_window(window, generation)
            return

        thread = threading.Thread(target=self.restore_window, args=(window, generation), daemon=True)
        thread.start()

    def restore_window(self, window: WindowSnapshot, generation: int | None = None) -> None:
        app_id = window.app_id
        with self._lock:
            if (
                app_id is None
                or window.id in self.restored_window_ids
                or window.id in self.ignored_window_ids
                or not self.config.allows_app(app_id)
            ):
                return
            if generation is None:
                generation = self.window_generations.get(window.id)

            geometry = self.store.get(app_id)
            if geometry is None:
                return

            self.restored_window_ids.add(window.id)

        if self.config.restore_delay_ms > 0:
            self.sleeper(self.config.restore_delay_seconds)

        with self._lock:
            latest_window = self.windows.get(window.id)
            latest_generation = self.window_generations.get(window.id)
            if latest_window is None:
                self.restored_window_ids.discard(window.id)
                logging.info("Window %s closed before restore could apply", window.id)
                return
            if latest_window.id in self.ignored_window_ids:
                self.restored_window_ids.discard(window.id)
                logging.debug("Skipping restore for ignored window %s", window.id)
                return
            if not self._restore_is_current(window, generation, latest_window, latest_generation):
                logging.info("Skipping stale restore for window %s", window.id)
                return

        target_id = latest_window.id
        with self._restore_command_lock:
            with self._lock:
                latest_window = self.windows.get(target_id)
                latest_generation = self.window_generations.get(target_id)
                if latest_window is None:
                    self.restored_window_ids.discard(target_id)
                    logging.info("Window %s closed before restore commands could apply", target_id)
                    return
                if latest_window.id in self.ignored_window_ids:
                    self.restored_window_ids.discard(target_id)
                    logging.debug("Skipping restore commands for ignored window %s", target_id)
                    return
                if not self._restore_is_current(window, generation, latest_window, latest_generation):
                    logging.info("Skipping stale restore commands for window %s", target_id)
                    return

                saved_mode = self.effective_saved_mode(geometry, latest_window)
                current_mode = self.current_mode(latest_window)

            restore_geometry = self.geometry_for_restore(geometry, latest_window, saved_mode)
            logging.info(
                "Applying restore: window=(%s) current_mode=%s saved_mode=%s saved=(%s) target=(%s)",
                self.window_summary(latest_window),
                current_mode,
                saved_mode,
                self.geometry_summary(geometry),
                self.geometry_summary(restore_geometry),
            )
            self._run_restore_commands(latest_window, restore_geometry, saved_mode, current_mode)
            # Do NOT cache the restore target position here.
            # restore_geometry uses working-area coordinates, but geometry_with_mode
            # expects workspace-view coordinates (what niri events report) and applies
            # the working_area_offset. Caching working-area coords here would cause
            # double-application of the offset if the window is saved before the next
            # niri event updates the position. Let niri events naturally update
            # self.windows with the correct workspace-view coordinates.

    def _restore_is_current(
        self,
        original: WindowSnapshot,
        original_generation: int | None,
        latest: WindowSnapshot,
        latest_generation: int | None,
    ) -> bool:
        return (
            original.app_id == latest.app_id
            and (original_generation is None or original_generation == latest_generation)
        )

    def _run_restore_commands(
        self,
        latest_window: WindowSnapshot,
        geometry: WindowGeometry,
        saved_mode: str,
        current_mode: str,
    ) -> None:
        if saved_mode == MODE_FULLSCREEN and self.config.restore_fullscreen:
            if self.config.restore_floating_state:
                self.niri_client.run(["move-window-to-tiling", "--id", str(latest_window.id)])
            if current_mode != MODE_FULLSCREEN:
                self.niri_client.run(["fullscreen-window", "--id", str(latest_window.id)])
            return

        if geometry.is_floating:
            if self.config.restore_size:
                self.niri_client.run(["set-window-width", "--id", str(latest_window.id), str(geometry.width)])
                self.niri_client.run(["set-window-height", "--id", str(latest_window.id), str(geometry.height)])
            if self.config.restore_floating_state:
                self.niri_client.run(["move-window-to-floating", "--id", str(latest_window.id)])
            if (
                self.config.restore_floating_position
                and geometry.floating_x is not None
                and geometry.floating_y is not None
            ):
                self.niri_client.run(
                    [
                        "move-floating-window",
                        "--id",
                        str(latest_window.id),
                        "--x",
                        str(geometry.floating_x),
                        "--y",
                        str(geometry.floating_y),
                    ]
                )
        else:
            if self.config.restore_floating_state:
                self.niri_client.run(["move-window-to-tiling", "--id", str(latest_window.id)])
            if saved_mode == MODE_MAXIMIZED and self.config.restore_maximized:
                # niri does not expose --id variants for column-width actions.
                # These focus-dependent restores are best effort: focus can move
                # after we check the latest event snapshot but before niri runs the command.
                if current_mode != MODE_MAXIMIZED and latest_window.is_focused:
                    self.niri_client.run(["maximize-column"])
                elif not latest_window.is_focused:
                    logging.info(
                        "Skipping maximize-column restore for unfocused window %s; maximize-column has no --id",
                        latest_window.id,
                    )
                if self.config.restore_size and self.config.restore_tiled_height:
                    self.niri_client.run(["set-window-height", "--id", str(latest_window.id), str(geometry.height)])
                return
            if self.config.restore_size and self.config.restore_tiled_width:
                # set-column-width has the same focus limitation as maximize-column.
                if latest_window.is_focused:
                    self.niri_client.run(["set-column-width", str(geometry.width)])
                else:
                    logging.info(
                        "Skipping tiled width restore for unfocused window %s; set-column-width has no --id",
                        latest_window.id,
                    )
            if self.config.restore_size and self.config.restore_tiled_height:
                self.niri_client.run(["set-window-height", "--id", str(latest_window.id), str(geometry.height)])

    def geometry_with_mode(self, window: WindowSnapshot) -> WindowGeometry:
        assert window.geometry is not None
        mode = self.classify_mode(window.geometry, window.workspace_id)
        output = self.output_for_workspace(window.workspace_id)
        floating_x = window.geometry.floating_x
        floating_y = window.geometry.floating_y
        if (
            window.geometry.is_floating
            and floating_x is not None
            and floating_y is not None
            and output is not None
        ):
            output_name = self._output_name(output)
            if output_name is not None:
                offset = self.config.working_area_offsets.get(output_name)
                if offset is not None:
                    floating_x = floating_x + offset[0]
                    floating_y = floating_y + offset[1]
        return WindowGeometry(
            width=window.geometry.width,
            height=window.geometry.height,
            is_floating=window.geometry.is_floating,
            mode=mode,
            floating_x=floating_x,
            floating_y=floating_y,
            output_width=output.width if output is not None else None,
            output_height=output.height if output is not None else None,
            updated_at=window.geometry.updated_at,
        )

    def geometry_for_restore(
        self,
        geometry: WindowGeometry,
        window: WindowSnapshot,
        saved_mode: str,
    ) -> WindowGeometry:
        if saved_mode == MODE_FULLSCREEN or not self.config.adapt_to_output:
            return geometry

        source = self.output_from_geometry(geometry)
        target = self.output_for_workspace(window.workspace_id)
        if source is None or target is None or source == target:
            return geometry

        width = self.scale_dimension(geometry.width, source.width, target.width)
        height = self.scale_dimension(geometry.height, source.height, target.height)
        width = min(width, target.width)
        height = min(height, target.height)

        floating_x = geometry.floating_x
        floating_y = geometry.floating_y
        if (
            geometry.is_floating
            and self.config.adapt_floating_position_to_output
            and floating_x is not None
            and floating_y is not None
        ):
            floating_x = self.scale_dimension(floating_x, source.width, target.width, minimum=0)
            floating_y = self.scale_dimension(floating_y, source.height, target.height, minimum=0)
            floating_x = min(floating_x, max(0, target.width - width))
            floating_y = min(floating_y, max(0, target.height - height))

        return WindowGeometry(
            width=width,
            height=height,
            is_floating=geometry.is_floating,
            mode=geometry.mode,
            floating_x=floating_x,
            floating_y=floating_y,
            output_width=target.width,
            output_height=target.height,
            updated_at=geometry.updated_at,
        )

    def output_from_geometry(self, geometry: WindowGeometry) -> OutputSize | None:
        if geometry.output_width is None or geometry.output_height is None:
            return None
        return OutputSize(width=geometry.output_width, height=geometry.output_height)

    def scale_dimension(self, value: int, source: int, target: int, minimum: int = 1) -> int:
        if source <= 0:
            return max(minimum, value)
        return max(minimum, int(round(value * target / source)))

    def effective_saved_mode(self, geometry: WindowGeometry, window: WindowSnapshot) -> str:
        if geometry.mode != MODE_NORMAL:
            return geometry.mode
        output = self.output_from_geometry(geometry)
        if output is not None:
            return self.classify_geometry_for_output(geometry, output)
        return self.classify_mode(geometry, window.workspace_id)

    def current_mode(self, window: WindowSnapshot) -> str:
        if window.geometry is None:
            return MODE_NORMAL
        return self.classify_mode(window.geometry, window.workspace_id)

    def classify_mode(self, geometry: WindowGeometry, workspace_id: int | None) -> str:
        if geometry.is_floating:
            return MODE_NORMAL

        output = self.output_for_workspace(workspace_id)
        if output is None:
            return geometry.mode if geometry.mode in VALID_MODES else MODE_NORMAL
        return self.classify_geometry_for_output(geometry, output)

    def classify_geometry_for_output(self, geometry: WindowGeometry, output: OutputSize) -> str:
        if geometry.is_floating:
            return MODE_NORMAL

        if (
            abs(geometry.width - output.width) <= self.config.fullscreen_tolerance_px
            and abs(geometry.height - output.height) <= self.config.fullscreen_tolerance_px
        ):
            return MODE_FULLSCREEN

        if geometry.width >= output.width - self.config.maximized_width_tolerance_px:
            return MODE_MAXIMIZED

        return MODE_NORMAL

    def output_for_workspace(self, workspace_id: int | None) -> OutputSize | None:
        if workspace_id is not None:
            output_name = self.workspace_outputs.get(workspace_id)
            if output_name is not None and output_name in self.outputs:
                return self.outputs[output_name]
        return self._single_output

    def _output_name(self, output: OutputSize) -> str | None:
        for name, candidate in self.outputs.items():
            if candidate is output:
                return name
        return None
