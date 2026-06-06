import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from niri_window_geometry import (
    DaemonConfig,
    WindowGeometry,
    WindowRestoreDaemon,
    StateStore,
    NiriClient,
    OutputSize,
    ShutdownController,
    cached_window_from_payload,
    event_variant,
)

WindowRestoreDaemon.sync_restores = True


def wait_for(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def window_payload(
    window_id=1,
    app_id="org.gnome.Nautilus",
    width=1200,
    height=800,
    is_floating=False,
    is_focused=True,
    workspace_id=4,
    x=None,
    y=None,
    title=None,
):
    layout = {
        "tile_size": {"w": width, "h": height},
        "window_size": {"w": width, "h": height},
        "is_floating": is_floating,
    }
    if x is not None and y is not None:
        layout["tile_pos_in_workspace_view"] = {"x": x, "y": y}
    return {
        "id": window_id,
        "app_id": app_id,
        "title": title,
        "workspace_id": workspace_id,
        "is_focused": is_focused,
        "is_floating": is_floating,
        "layout": layout,
    }


class StateStoreTests(unittest.TestCase):
    def test_missing_state_starts_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(Path(temp_dir) / "missing.json")
            store.load()
            self.assertEqual(store.apps, {})

    def test_corrupt_state_starts_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            state_file.write_text("{nope", encoding="utf-8")

            store = StateStore(state_file)
            store.load()

            self.assertEqual(store.apps, {})

    def test_workspace_is_never_written(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            store = StateStore(state_file)
            store.put("org.gnome.Nautilus", WindowGeometry(width=1200, height=800, is_floating=False))

            raw = json.loads(state_file.read_text(encoding="utf-8"))
            serialized = json.dumps(raw)
            self.assertNotIn("workspace", serialized)

    def test_failed_save_cleans_up_temp_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            store = StateStore(state_file)
            store.apps["org.gnome.Nautilus"] = WindowGeometry(width=1200, height=800, is_floating=False)

            with patch("pathlib.Path.replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    store.save()

            self.assertEqual(list(Path(temp_dir).glob(f".{state_file.name}.*")), [])


class NiriClientTests(unittest.TestCase):
    def test_dry_run_commands_are_not_kept_for_real_runs(self):
        niri_client = NiriClient(dry_run=False)
        with patch("niri_window_geometry.niri.subprocess.run") as run:
            niri_client.run(["set-window-height", "--id", "1", "800"])

        self.assertTrue(run.called)
        self.assertEqual(niri_client.dry_run_commands, [])

    def test_dry_run_keeps_command_history_for_tests(self):
        niri_client = NiriClient(dry_run=True)

        niri_client.run(["set-window-height", "--id", "1", "800"])

        self.assertEqual(
            niri_client.dry_run_commands,
            [["niri", "msg", "action", "set-window-height", "--id", "1", "800"]],
        )


class EventParsingTests(unittest.TestCase):
    def test_event_variant_accepts_single_key_shape(self):
        event_type, payload = event_variant({"WindowClosed": {"id": 4}})
        self.assertEqual(event_type, "WindowClosed")
        self.assertEqual(payload, {"id": 4})

    def test_event_variant_accepts_type_payload_shape(self):
        event_type, payload = event_variant({"type": "WindowClosed", "payload": {"id": 4}})
        self.assertEqual(event_type, "WindowClosed")
        self.assertEqual(payload, {"id": 4})

    def test_cached_window_ignores_missing_app_id_but_keeps_geometry(self):
        snapshot = cached_window_from_payload(window_payload(app_id=None))
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertIsNone(snapshot.app_id)
        self.assertEqual(snapshot.geometry.width, 1200)


class DaemonConfigTests(unittest.TestCase):
    def test_include_apps_allows_only_listed_apps(self):
        config = DaemonConfig.from_json({"apps": {"include": ["org.gnome.Nautilus"]}})
        self.assertTrue(config.allows_app("org.gnome.Nautilus"))
        self.assertFalse(config.allows_app("kitty"))

    def test_exclude_apps_blocks_listed_apps(self):
        config = DaemonConfig.from_json({"apps": {"exclude": ["kitty"]}})
        self.assertTrue(config.allows_app("org.gnome.Nautilus"))
        self.assertFalse(config.allows_app("kitty"))

    def test_disabled_config_blocks_all_apps(self):
        config = DaemonConfig.from_json({"enabled": False})
        self.assertFalse(config.allows_app("org.gnome.Nautilus"))

    def test_restore_delay_can_be_zero(self):
        config = DaemonConfig.from_json({"restore_delay_ms": 0})
        self.assertEqual(config.restore_delay_ms, 0)

    def test_live_update_tracking_can_be_enabled(self):
        config = DaemonConfig.from_json(
            {"tracking": {"live_updates": True, "live_save_delay_ms": 0}}
        )
        self.assertTrue(config.live_updates)
        self.assertEqual(config.live_save_delay_ms, 0)

    def test_output_adaptation_can_be_disabled(self):
        config = DaemonConfig.from_json({"restore": {"adapt_to_output": False}})
        self.assertFalse(config.adapt_to_output)

    def test_missing_config_uses_default_config_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = DaemonConfig.load(Path(temp_dir) / "missing.json")

        self.assertTrue(config.live_updates)
        self.assertTrue(config.adapt_to_output)

    def test_detection_tolerances_can_be_zero(self):
        config = DaemonConfig.from_json(
            {"detection": {"fullscreen_tolerance_px": 0, "maximized_width_tolerance_px": 0}}
        )
        self.assertEqual(config.fullscreen_tolerance_px, 0)
        self.assertEqual(config.maximized_width_tolerance_px, 0)


class WindowRestoreDaemonTests(unittest.TestCase):
    def test_save_tiled_window_size_on_close(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(Path(temp_dir) / "state.json")
            niri_client = NiriClient(dry_run=True)
            daemon = WindowRestoreDaemon(store, niri_client, config=DaemonConfig(restore_delay_ms=0))
            daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(is_floating=False)}})

            daemon.handle_event({"WindowClosed": {"id": 1}})

            saved = store.get("org.gnome.Nautilus")
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual((saved.width, saved.height), (1200, 800))
            self.assertFalse(saved.is_floating)

    def test_save_floating_window_size_and_position_on_close(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(Path(temp_dir) / "state.json")
            niri_client = NiriClient(dry_run=True)
            daemon = WindowRestoreDaemon(store, niri_client, config=DaemonConfig(restore_delay_ms=0))
            daemon.handle_event(
                {
                    "WindowOpenedOrChanged": {
                        "window": window_payload(is_floating=True, x=160, y=90)
                    }
                }
            )

            daemon.handle_event({"WindowClosed": {"id": 1}})

            saved = store.get("org.gnome.Nautilus")
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertTrue(saved.is_floating)
            self.assertEqual((saved.floating_x, saved.floating_y), (160, 90))

    def test_restore_tiled_window_forces_tiling(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(width=1200, height=800, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(store, niri_client, config=DaemonConfig(restore_delay_ms=0))

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(is_floating=True)}})

        self.assertEqual(
            niri_client.dry_run_commands,
            [
                ["niri", "msg", "action", "move-window-to-tiling", "--id", "1"],
                ["niri", "msg", "action", "set-column-width", "1200"],
                ["niri", "msg", "action", "set-window-height", "--id", "1", "800"],
            ],
        )

    def test_restore_tiled_window_adapts_size_to_current_output(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(
            width=1200,
            height=800,
            is_floating=False,
            output_width=1920,
            output_height=1080,
        )
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0),
            outputs={
                "landscape": OutputSize(width=1920, height=1080),
                "portrait": OutputSize(width=1080, height=1920),
            },
        )
        daemon.handle_event({"WorkspacesChanged": {"workspaces": [{"id": 4, "output": "portrait"}]}})

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(is_floating=False)}})

        self.assertEqual(
            niri_client.dry_run_commands,
            [
                ["niri", "msg", "action", "move-window-to-tiling", "--id", "1"],
                ["niri", "msg", "action", "set-column-width", "675"],
                ["niri", "msg", "action", "set-window-height", "--id", "1", "1422"],
            ],
        )

    def test_restore_tiled_window_keeps_exact_size_when_output_adaptation_is_disabled(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(
            width=1200,
            height=800,
            is_floating=False,
            output_width=1920,
            output_height=1080,
        )
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0, adapt_to_output=False),
            outputs={"portrait": OutputSize(width=1080, height=1920)},
        )

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(is_floating=False)}})

        self.assertEqual(
            niri_client.dry_run_commands,
            [
                ["niri", "msg", "action", "move-window-to-tiling", "--id", "1"],
                ["niri", "msg", "action", "set-column-width", "1200"],
                ["niri", "msg", "action", "set-window-height", "--id", "1", "800"],
            ],
        )

    def test_restore_floating_window_adapts_size_and_position_to_current_output(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(
            width=960,
            height=540,
            is_floating=True,
            floating_x=480,
            floating_y=270,
            output_width=1920,
            output_height=1080,
        )
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0),
            outputs={"portrait": OutputSize(width=1080, height=1920)},
        )

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(is_floating=False)}})

        self.assertEqual(
            niri_client.dry_run_commands,
            [
                ["niri", "msg", "action", "set-window-width", "--id", "1", "540"],
                ["niri", "msg", "action", "set-window-height", "--id", "1", "960"],
                ["niri", "msg", "action", "move-window-to-floating", "--id", "1"],
                [
                    "niri",
                    "msg",
                    "action",
                    "move-floating-window",
                    "--id",
                    "1",
                    "--x",
                    "270",
                    "--y",
                    "480",
                ],
            ],
        )

    def test_save_fullscreen_mode_from_output_sized_tiled_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(Path(temp_dir) / "state.json")
            niri_client = NiriClient(dry_run=True)
            daemon = WindowRestoreDaemon(
                store,
                niri_client,
                config=DaemonConfig(restore_delay_ms=0),
                outputs={"eDP-2": OutputSize(width=1920, height=1080)},
            )
            daemon.handle_event(
                {
                    "WindowOpenedOrChanged": {
                        "window": window_payload(width=1920, height=1080, is_floating=False)
                    }
                }
            )

            daemon.handle_event({"WindowClosed": {"id": 1}})

            saved = store.get("org.gnome.Nautilus")
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.mode, "fullscreen")

    def test_save_maximized_mode_from_near_output_width(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(Path(temp_dir) / "state.json")
            niri_client = NiriClient(dry_run=True)
            daemon = WindowRestoreDaemon(
                store,
                niri_client,
                config=DaemonConfig(restore_delay_ms=0),
                outputs={"eDP-2": OutputSize(width=1920, height=1080)},
            )
            daemon.handle_event(
                {
                    "WindowOpenedOrChanged": {
                        "window": window_payload(width=1904, height=1024, is_floating=False)
                    }
                }
            )

            daemon.handle_event({"WindowClosed": {"id": 1}})

            saved = store.get("org.gnome.Nautilus")
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.mode, "maximized")

    def test_restore_fullscreen_uses_fullscreen_action(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(
            width=1920,
            height=1080,
            is_floating=False,
            mode="fullscreen",
        )
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0),
            outputs={"eDP-2": OutputSize(width=1920, height=1080)},
        )

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload()}})

        self.assertEqual(
            niri_client.dry_run_commands,
            [
                ["niri", "msg", "action", "move-window-to-tiling", "--id", "1"],
                ["niri", "msg", "action", "fullscreen-window", "--id", "1"],
            ],
        )

    def test_restore_legacy_fullscreen_dimensions_uses_fullscreen_action(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(
            width=1920,
            height=1080,
            is_floating=False,
        )
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0),
            outputs={"eDP-2": OutputSize(width=1920, height=1080)},
        )

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload()}})

        self.assertEqual(
            niri_client.dry_run_commands,
            [
                ["niri", "msg", "action", "move-window-to-tiling", "--id", "1"],
                ["niri", "msg", "action", "fullscreen-window", "--id", "1"],
            ],
        )

    def test_restore_maximized_uses_maximize_column_not_raw_width(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(
            width=1904,
            height=1024,
            is_floating=False,
            mode="maximized",
        )
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0),
            outputs={"eDP-2": OutputSize(width=1920, height=1080)},
        )

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload()}})

        self.assertEqual(
            niri_client.dry_run_commands,
            [
                ["niri", "msg", "action", "move-window-to-tiling", "--id", "1"],
                ["niri", "msg", "action", "maximize-column"],
                ["niri", "msg", "action", "set-window-height", "--id", "1", "1024"],
            ],
        )

    def test_restore_legacy_maximized_dimensions_uses_maximize_column(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(
            width=1904,
            height=1024,
            is_floating=False,
        )
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0),
            outputs={"eDP-2": OutputSize(width=1920, height=1080)},
        )

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload()}})

        self.assertEqual(
            niri_client.dry_run_commands,
            [
                ["niri", "msg", "action", "move-window-to-tiling", "--id", "1"],
                ["niri", "msg", "action", "maximize-column"],
                ["niri", "msg", "action", "set-window-height", "--id", "1", "1024"],
            ],
        )

    def test_restore_floating_window_size_and_position(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(
            width=1200,
            height=800,
            is_floating=True,
            floating_x=160,
            floating_y=90,
        )
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(store, niri_client, config=DaemonConfig(restore_delay_ms=0))

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(is_floating=False)}})

        self.assertEqual(
            niri_client.dry_run_commands,
            [
                ["niri", "msg", "action", "set-window-width", "--id", "1", "1200"],
                ["niri", "msg", "action", "set-window-height", "--id", "1", "800"],
                ["niri", "msg", "action", "move-window-to-floating", "--id", "1"],
                [
                    "niri",
                    "msg",
                    "action",
                    "move-floating-window",
                    "--id",
                    "1",
                    "--x",
                    "160",
                    "--y",
                    "90",
                ],
            ],
        )

    def test_restore_only_once_per_window_id(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(width=1200, height=800, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(store, niri_client, config=DaemonConfig(restore_delay_ms=0))

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(width=900, height=600)}})
        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(width=1300)}})

        self.assertEqual(len(niri_client.dry_run_commands), 3)

    def test_restore_skipped_when_tiled_window_already_matches(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(width=1200, height=800, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(store, niri_client, config=DaemonConfig(restore_delay_ms=0))

        daemon.handle_event(
            {"WindowOpenedOrChanged": {"window": window_payload(width=1200, height=800, is_floating=False)}}
        )

        self.assertEqual(niri_client.dry_run_commands, [])

    def test_restore_skipped_when_maximized_window_already_matches(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(
            width=1904, height=1024, is_floating=False, mode="maximized",
        )
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store, niri_client,
            config=DaemonConfig(restore_delay_ms=0),
            outputs={"eDP-2": OutputSize(width=1920, height=1080)},
        )

        daemon.handle_event(
            {"WindowOpenedOrChanged": {"window": window_payload(width=1904, height=1024, is_floating=False)}}
        )

        self.assertEqual(niri_client.dry_run_commands, [])

    def test_restore_not_skipped_when_size_differs(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(width=900, height=700, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(store, niri_client, config=DaemonConfig(restore_delay_ms=0))

        daemon.handle_event(
            {"WindowOpenedOrChanged": {"window": window_payload(width=1200, height=800, is_floating=False)}}
        )

        self.assertGreater(len(niri_client.dry_run_commands), 0)

    def test_restore_delay_is_applied_before_commands(self):
        sleeps = []
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(width=1200, height=800, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=250),
            sleeper=sleeps.append,
        )

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload()}})

        self.assertEqual(sleeps, [0.25])

    def test_async_restore_delay_does_not_block_close_event(self):
        sleep_started = threading.Event()
        release_sleep = threading.Event()

        def sleeper(_seconds):
            sleep_started.set()
            release_sleep.wait(1)

        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(width=1200, height=800, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=250),
            sleeper=sleeper,
            sync_restores=False,
        )

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload()}})
        self.assertTrue(sleep_started.wait(1))
        daemon.handle_event({"WindowClosed": {"id": 1}})
        release_sleep.set()

        self.assertTrue(wait_for(lambda: 1 not in daemon.restored_window_ids))
        self.assertEqual(niri_client.dry_run_commands, [])

    def test_async_restore_does_not_target_reused_window_id(self):
        sleep_started = threading.Event()
        release_sleep = threading.Event()
        sleep_returned = threading.Event()

        def sleeper(_seconds):
            sleep_started.set()
            release_sleep.wait(1)
            sleep_returned.set()

        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(width=1200, height=800, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=250),
            sleeper=sleeper,
            sync_restores=False,
        )

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(app_id="org.gnome.Nautilus")}})
        self.assertTrue(sleep_started.wait(1))
        daemon.handle_event({"WindowClosed": {"id": 1}})
        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(app_id="kitty")}})
        release_sleep.set()

        self.assertTrue(sleep_returned.wait(1))
        self.assertFalse(wait_for(lambda: len(niri_client.dry_run_commands) > 0, timeout=0.1))

    def test_async_maximize_uses_latest_focus_state_after_delay(self):
        sleep_started = threading.Event()
        release_sleep = threading.Event()

        def sleeper(_seconds):
            sleep_started.set()
            release_sleep.wait(1)

        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(
            width=1904,
            height=1024,
            is_floating=False,
            mode="maximized",
        )
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=250),
            outputs={"eDP-2": OutputSize(width=1920, height=1080)},
            sleeper=sleeper,
            sync_restores=False,
        )

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(is_focused=True)}})
        self.assertTrue(sleep_started.wait(1))
        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(is_focused=False)}})
        release_sleep.set()

        self.assertTrue(wait_for(lambda: len(niri_client.dry_run_commands) >= 2))
        self.assertEqual(
            niri_client.dry_run_commands,
            [
                ["niri", "msg", "action", "move-window-to-tiling", "--id", "1"],
                ["niri", "msg", "action", "set-window-height", "--id", "1", "1024"],
            ],
        )

    def test_excluded_app_is_not_saved_or_restored(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["kitty"] = WindowGeometry(width=1200, height=800, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0, exclude_apps=("kitty",)),
        )

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(app_id="kitty")}})
        daemon.handle_event({"WindowClosed": {"id": 1}})

        self.assertEqual(niri_client.dry_run_commands, [])

    def test_window_layouts_changed_updates_cached_size(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(Path(temp_dir) / "state.json")
            niri_client = NiriClient(dry_run=True)
            daemon = WindowRestoreDaemon(store, niri_client, config=DaemonConfig(restore_delay_ms=0))
            daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(is_floating=True)}})
            daemon.handle_event(
                {
                    "WindowLayoutsChanged": {
                        "changes": {
                            "1": {
                                "window_size": {"w": 900, "h": 700},
                                "is_floating": True,
                                "tile_pos_in_workspace_view": {"x": 10, "y": 20},
                            }
                        }
                    }
                }
            )

            daemon.handle_event({"WindowClosed": {"id": 1}})

            saved = store.get("org.gnome.Nautilus")
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual((saved.width, saved.height), (900, 700))
            self.assertTrue(saved.is_floating)
            self.assertEqual((saved.floating_x, saved.floating_y), (10, 20))

    def test_live_updates_save_layout_change_before_close(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0, live_updates=True, live_save_delay_ms=0),
        )
        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(window_id=1)}})
        daemon.handle_event(
            {
                "WindowLayoutsChanged": {
                    "changes": {
                        "1": {
                            "window_size": {"w": 900, "h": 700},
                            "tile_pos_in_workspace_view": {"x": 30, "y": 40},
                        }
                    }
                }
            }
        )

        saved = store.get("org.gnome.Nautilus")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual((saved.width, saved.height), (900, 700))

    def test_live_updates_learn_initial_open_windows(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0, live_updates=True, live_save_delay_ms=0),
        )

        daemon.handle_event(
            {
                "WindowsChanged": {
                    "windows": [
                        window_payload(window_id=1, width=880, height=660),
                    ]
                }
            }
        )

        saved = store.get("org.gnome.Nautilus")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual((saved.width, saved.height), (880, 660))

    def test_live_updates_restore_second_window_from_open_window_resize(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0, live_updates=True, live_save_delay_ms=0),
        )
        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(window_id=1)}})
        daemon.handle_event(
            {
                "WindowLayoutsChanged": {
                    "changes": {
                        "1": {
                            "window_size": {"w": 900, "h": 700},
                        }
                    }
                }
            }
        )
        niri_client.dry_run_commands.clear()

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(window_id=2)}})

        self.assertEqual(
            niri_client.dry_run_commands,
            [
                ["niri", "msg", "action", "move-window-to-tiling", "--id", "2"],
                ["niri", "msg", "action", "set-column-width", "900"],
                ["niri", "msg", "action", "set-window-height", "--id", "2", "700"],
            ],
        )

    def test_live_updates_can_be_disabled(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0, live_updates=False),
        )
        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(window_id=1)}})
        daemon.handle_event(
            {
                "WindowLayoutsChanged": {
                    "changes": {
                        "1": {
                            "window_size": {"w": 900, "h": 700},
                        }
                    }
                }
            }
        )

        self.assertIsNone(store.get("org.gnome.Nautilus"))

    def test_window_layouts_changed_accepts_niri_tuple_list_shape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(Path(temp_dir) / "state.json")
            niri_client = NiriClient(dry_run=True)
            daemon = WindowRestoreDaemon(store, niri_client, config=DaemonConfig(restore_delay_ms=0))
            daemon.handle_event(
                {
                    "WindowOpenedOrChanged": {
                        "window": window_payload(is_floating=True, x=1, y=2)
                    }
                }
            )
            daemon.handle_event(
                {
                    "WindowLayoutsChanged": {
                        "changes": [
                            [
                                1,
                                {
                                    "window_size": {"w": 640, "h": 480},
                                    "tile_pos_in_workspace_view": {"x": 30, "y": 40},
                                },
                            ]
                        ]
                    }
                }
            )

            daemon.handle_event({"WindowClosed": {"id": 1}})

            saved = store.get("org.gnome.Nautilus")
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertTrue(saved.is_floating)
            self.assertEqual((saved.width, saved.height), (640, 480))
            self.assertEqual((saved.floating_x, saved.floating_y), (30, 40))

    def test_window_tracking_is_removed_on_close(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(width=1200, height=800, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(store, niri_client, config=DaemonConfig(restore_delay_ms=0))

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(window_id=12)}})
        self.assertIn(12, daemon.windows)
        self.assertIn(12, daemon.window_generations)
        self.assertIn(12, daemon.restored_window_ids)

        daemon.handle_event({"WindowClosed": {"id": 12}})

        self.assertNotIn(12, daemon.windows)
        self.assertNotIn(12, daemon.window_generations)
        self.assertNotIn(12, daemon.restored_window_ids)
        self.assertNotIn(12, daemon.ignored_window_ids)

    def test_dialog_like_secondary_window_is_not_restored_or_saved(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["code"] = WindowGeometry(width=1904, height=1024, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0, live_updates=True, live_save_delay_ms=0),
        )

        daemon.handle_event(
            {
                "WindowOpenedOrChanged": {
                    "window": window_payload(
                        window_id=10,
                        app_id="code",
                        width=1904,
                        height=1024,
                        is_floating=False,
                    )
                }
            }
        )
        niri_client.dry_run_commands.clear()

        daemon.handle_event(
            {
                "WindowOpenedOrChanged": {
                    "window": window_payload(
                        window_id=11,
                        app_id="code",
                        title="Undo all edits?",
                        width=534,
                        height=167,
                        is_floating=True,
                    )
                }
            }
        )
        daemon.handle_event(
            {
                "WindowLayoutsChanged": {
                    "changes": {
                        "11": {
                            "window_size": {"w": 534, "h": 167},
                            "tile_size": {"w": 534, "h": 167},
                            "tile_pos_in_workspace_view": {"x": 693, "y": 477},
                        }
                    }
                }
            }
        )
        daemon.handle_event({"WindowClosed": {"id": 11}})

        self.assertEqual(niri_client.dry_run_commands, [])
        saved = store.get("code")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual((saved.width, saved.height), (1904, 1024))

    def test_dialog_from_closed_app_is_ignored(self):
        """A dialog that opens after the main window closed (tray scenario)."""
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["Electron20"] = WindowGeometry(width=1904, height=1024, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0, live_updates=True, live_save_delay_ms=0),
        )

        daemon.handle_event(
            {
                "WindowOpenedOrChanged": {
                    "window": window_payload(
                        window_id=10,
                        app_id="Electron20",
                        width=1904,
                        height=1024,
                        is_floating=False,
                    )
                }
            }
        )
        daemon.handle_event({"WindowClosed": {"id": 10}})
        niri_client.dry_run_commands.clear()

        # Dialog opens from tray — no other Electron20 window in self.windows,
        # but the store has the tiled main window geometry.
        daemon.handle_event(
            {
                "WindowOpenedOrChanged": {
                    "window": window_payload(
                        window_id=83,
                        app_id="Electron20",
                        title="Exit?",
                        width=328,
                        height=83,
                        is_floating=True,
                    )
                }
            }
        )
        daemon.handle_event({"WindowClosed": {"id": 83}})

        self.assertEqual(niri_client.dry_run_commands, [])
        saved = store.get("Electron20")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual((saved.width, saved.height), (1904, 1024))

    def test_cross_app_id_dialog_caught_by_built_in_title_pattern(self):
        """WALC: main window app_id='walc', dialog app_id='Electron20' with title 'Exit?'."""
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["walc"] = WindowGeometry(width=948, height=1024, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0, live_updates=True, live_save_delay_ms=0),
        )

        daemon.handle_event(
            {
                "WindowOpenedOrChanged": {
                    "window": window_payload(
                        window_id=104,
                        app_id="walc",
                        title="WALC",
                        width=948,
                        height=1024,
                        is_floating=False,
                    )
                }
            }
        )
        niri_client.dry_run_commands.clear()

        # Dialog opens with a different app_id — no way to link via app_id,
        # but the title "Exit?" matches the built-in `\?$` pattern.
        daemon.handle_event(
            {
                "WindowOpenedOrChanged": {
                    "window": window_payload(
                        window_id=105,
                        app_id="Electron20",
                        title="Exit?",
                        width=328,
                        height=83,
                        is_floating=True,
                    )
                }
            }
        )
        daemon.handle_event({"WindowClosed": {"id": 105}})

        self.assertEqual(niri_client.dry_run_commands, [])
        saved = store.get("walc")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual((saved.width, saved.height), (948, 1024))
        self.assertIsNone(store.get("Electron20"))

    def test_single_word_action_title_is_treated_as_dialog(self):
        """A small floating window titled 'Discard' from a tray is a dialog."""
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["myapp"] = WindowGeometry(width=1904, height=1024, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0, live_updates=True, live_save_delay_ms=0),
        )

        daemon.handle_event(
            {
                "WindowOpenedOrChanged": {
                    "window": window_payload(
                        window_id=1,
                        app_id="myapp",
                        title="Discard",
                        width=300,
                        height=100,
                        is_floating=True,
                    )
                }
            }
        )

        self.assertEqual(niri_client.dry_run_commands, [])

    def test_large_tiled_window_with_question_mark_title_is_not_ignored(self):
        """A large tiled window titled 'Ready?' should NOT be treated as a dialog.
        The built-in title patterns only fire for small floating windows."""
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["SetupTool"] = WindowGeometry(width=1200, height=800, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(restore_delay_ms=0, live_updates=True, live_save_delay_ms=0),
        )

        daemon.handle_event(
            {
                "WindowOpenedOrChanged": {
                    "window": window_payload(
                        window_id=1,
                        app_id="SetupTool",
                        title="Ready?",
                        width=900,
                        height=600,
                        is_floating=False,
                    )
                }
            }
        )

        # Should still restore — it's a large tiled window, not a dialog.
        self.assertNotEqual(niri_client.dry_run_commands, [])

    def test_ignored_title_pattern_is_not_restored_or_saved(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["code"] = WindowGeometry(width=1904, height=1024, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(
                restore_delay_ms=0,
                live_updates=True,
                live_save_delay_ms=0,
                ignore_title_patterns=(r"^Undo all edits\?",),
            ),
        )

        daemon.handle_event(
            {
                "WindowOpenedOrChanged": {
                    "window": window_payload(
                        window_id=1,
                        app_id="code",
                        title="Undo all edits?",
                        width=900,
                        height=600,
                    )
                }
            }
        )
        daemon.handle_event({"WindowClosed": {"id": 1}})

        self.assertEqual(niri_client.dry_run_commands, [])
        saved = store.get("code")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual((saved.width, saved.height), (1904, 1024))

    def test_ignored_app_title_pattern_is_not_restored_or_saved(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["code"] = WindowGeometry(width=1904, height=1024, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(
                restore_delay_ms=0,
                live_updates=True,
                live_save_delay_ms=0,
                ignore_app_title_patterns=(("code", r"^Undo all edits\?"),),
            ),
        )

        daemon.handle_event(
            {
                "WindowOpenedOrChanged": {
                    "window": window_payload(
                        window_id=1,
                        app_id="code",
                        title="Undo all edits?",
                        width=900,
                        height=600,
                    )
                }
            }
        )
        daemon.handle_event({"WindowClosed": {"id": 1}})

        self.assertEqual(niri_client.dry_run_commands, [])
        saved = store.get("code")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual((saved.width, saved.height), (1904, 1024))

    def test_app_title_pattern_does_not_ignore_different_app(self):
        """Same title pattern but different app_id should NOT be ignored."""
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["other-app"] = WindowGeometry(width=1200, height=800, is_floating=False)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(
            store,
            niri_client,
            config=DaemonConfig(
                restore_delay_ms=0,
                ignore_app_title_patterns=(("code", r"^Settings$"),),
            ),
        )

        daemon.handle_event(
            {
                "WindowOpenedOrChanged": {
                    "window": window_payload(
                        window_id=1,
                        app_id="other-app",
                        title="Settings",
                        width=800,
                        height=600,
                    )
                }
            }
        )

        # Should still restore — the pattern is scoped to "code", not "other-app"
        self.assertNotEqual(niri_client.dry_run_commands, [])

    def test_ignore_app_title_patterns_parsed_from_json(self):
        config = DaemonConfig.from_json(
            {
                "tracking": {
                    "ignore_app_title_patterns": [
                        {"app_id": "code", "title": "^Undo"},
                        {"app_id": "Electron20", "title": "Exit\\?"},
                    ]
                }
            }
        )
        self.assertEqual(
            config.ignore_app_title_patterns,
            (("code", "^Undo"), ("Electron20", "Exit\\?")),
        )

    def test_ignore_app_title_patterns_rejects_invalid_entries(self):
        config = DaemonConfig.from_json(
            {
                "tracking": {
                    "ignore_app_title_patterns": [
                        {"app_id": "code"},
                        {"title": "^Undo"},
                        "not-a-dict",
                        {"app_id": "", "title": "^Undo"},
                        {"app_id": "code", "title": ""},
                    ]
                }
            }
        )
        self.assertEqual(config.ignore_app_title_patterns, ())

    def test_window_without_app_id_is_ignored(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(store, niri_client, config=DaemonConfig(restore_delay_ms=0))

        daemon.handle_event({"WindowOpenedOrChanged": {"window": window_payload(app_id=None)}})
        daemon.handle_event({"WindowClosed": {"id": 1}})

        self.assertEqual(store.apps, {})

    def test_working_area_offset_applied_in_geometry_with_mode_on_close(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(Path(temp_dir) / "state.json")
            niri_client = NiriClient(dry_run=True)
            daemon = WindowRestoreDaemon(
                store,
                niri_client,
                config=DaemonConfig(
                    restore_delay_ms=0,
                    working_area_offsets={"eDP-2": (0, 40)},
                ),
                outputs={"eDP-2": OutputSize(width=1920, height=1080)},
            )
            daemon.handle_event({"WorkspacesChanged": {"workspaces": [{"id": 4, "output": "eDP-2"}]}})
            daemon.handle_event(
                {
                    "WindowOpenedOrChanged": {
                        "window": window_payload(
                            is_floating=True, x=996, y=272, workspace_id=4,
                        )
                    }
                }
            )
            daemon.handle_event({"WindowClosed": {"id": 1}})

            saved = store.get("org.gnome.Nautilus")
            self.assertIsNotNone(saved)
            assert saved is not None
            # Working-area to output: y=272 + 40 bar = 312 (output coords for move-floating-window)
            self.assertEqual(saved.floating_y, 312)
            self.assertEqual(saved.floating_x, 996)

    def test_working_area_offset_not_applied_for_unknown_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(Path(temp_dir) / "state.json")
            niri_client = NiriClient(dry_run=True)
            daemon = WindowRestoreDaemon(
                store,
                niri_client,
                config=DaemonConfig(
                    restore_delay_ms=0,
                    working_area_offsets={"DP-5": (0, 40)},
                ),
                outputs={"eDP-2": OutputSize(width=1920, height=1080)},
            )
            daemon.handle_event({"WorkspacesChanged": {"workspaces": [{"id": 4, "output": "eDP-2"}]}})
            daemon.handle_event(
                {
                    "WindowOpenedOrChanged": {
                        "window": window_payload(
                            is_floating=True, x=100, y=200, workspace_id=4,
                        )
                    }
                }
            )
            daemon.handle_event({"WindowClosed": {"id": 1}})

            saved = store.get("org.gnome.Nautilus")
            self.assertIsNotNone(saved)
            assert saved is not None
            # Offset is for DP-5, not eDP-2 → no correction
            self.assertEqual(saved.floating_y, 200)
            self.assertEqual(saved.floating_x, 100)

    def test_working_area_offset_with_x_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(Path(temp_dir) / "state.json")
            niri_client = NiriClient(dry_run=True)
            daemon = WindowRestoreDaemon(
                store,
                niri_client,
                config=DaemonConfig(
                    restore_delay_ms=0,
                    working_area_offsets={"eDP-2": (10, 40)},
                ),
                outputs={"eDP-2": OutputSize(width=1920, height=1080)},
            )
            daemon.handle_event({"WorkspacesChanged": {"workspaces": [{"id": 4, "output": "eDP-2"}]}})
            daemon.handle_event(
                {
                    "WindowOpenedOrChanged": {
                        "window": window_payload(
                            is_floating=True, x=106, y=272, workspace_id=4,
                        )
                    }
                }
            )
            daemon.handle_event({"WindowClosed": {"id": 1}})

            saved = store.get("org.gnome.Nautilus")
            self.assertIsNotNone(saved)
            assert saved is not None
            # Working-area to output: x=106+10=116, y=272+40=312
            self.assertEqual(saved.floating_x, 116)
            self.assertEqual(saved.floating_y, 312)

    def test_cached_geometry_updated_after_restore(self):
        store = StateStore(Path("/tmp/not-written.json"), dry_run=True)
        store.apps["org.gnome.Nautilus"] = WindowGeometry(
            width=1200, height=800, is_floating=True,
            floating_x=160, floating_y=90,
        )
        niri_client = NiriClient(dry_run=True)
        daemon = WindowRestoreDaemon(store, niri_client, config=DaemonConfig(restore_delay_ms=0))

        daemon.handle_event(
            {"WindowOpenedOrChanged": {"window": window_payload(is_floating=False)}}
        )

        # The cached geometry should NOT be overwritten with restore target coords.
        # It retains the original event geometry; niri events will naturally update
        # self.windows with the correct workspace-view coordinates, preventing
        # double-application of working_area_offsets.
        cached = daemon.windows.get(1)
        self.assertIsNotNone(cached)
        assert cached is not None
        assert cached.geometry is not None
        self.assertFalse(cached.geometry.is_floating)
        self.assertIsNone(cached.geometry.floating_x)
        self.assertIsNone(cached.geometry.floating_y)

    def test_tiled_window_geometry_with_mode_skips_offset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = StateStore(Path(temp_dir) / "state.json")
            niri_client = NiriClient(dry_run=True)
            daemon = WindowRestoreDaemon(
                store,
                niri_client,
                config=DaemonConfig(
                    restore_delay_ms=0,
                    working_area_offsets={"eDP-2": (0, 40)},
                ),
                outputs={"eDP-2": OutputSize(width=1920, height=1080)},
            )
            daemon.handle_event({"WorkspacesChanged": {"workspaces": [{"id": 4, "output": "eDP-2"}]}})
            daemon.handle_event(
                {
                    "WindowOpenedOrChanged": {
                        "window": window_payload(is_floating=False, workspace_id=4)
                    }
                }
            )
            daemon.handle_event({"WindowClosed": {"id": 1}})

            saved = store.get("org.gnome.Nautilus")
            self.assertIsNotNone(saved)
            assert saved is not None
            # Tiled window: no offset applied since is_floating is False
            self.assertFalse(saved.is_floating)
            self.assertIsNone(saved.floating_x)
            self.assertIsNone(saved.floating_y)

    def test_working_area_offsets_parsed_from_json(self):
        config = DaemonConfig.from_json(
            {"working_area_offsets": {"eDP-2": {"x": 0, "y": 40}, "DP-1": {"x": 5, "y": 0}}}
        )
        self.assertEqual(config.working_area_offsets, {"eDP-2": (0, 40), "DP-1": (5, 0)})

    def test_working_area_offsets_defaults_to_empty(self):
        config = DaemonConfig.from_json({})
        self.assertEqual(config.working_area_offsets, {})

    def test_working_area_offsets_rejects_invalid_format(self):
        config = DaemonConfig.from_json(
            {"working_area_offsets": {"bad": "not-a-dict", "also_bad": {"x": "nope", "y": 40}}}
        )
        self.assertEqual(config.working_area_offsets, {})


class FakeProcess:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True


class ShutdownControllerTests(unittest.TestCase):
    def test_stop_terminates_current_process(self):
        controller = ShutdownController()
        process = FakeProcess()
        controller.set_process(process)

        controller.stop(15)

        self.assertTrue(controller.requested())
        self.assertTrue(process.terminated)

    def test_set_process_after_stop_terminates_immediately(self):
        controller = ShutdownController()
        controller.stop(15)
        process = FakeProcess()

        controller.set_process(process)

        self.assertTrue(process.terminated)


if __name__ == "__main__":
    unittest.main()
