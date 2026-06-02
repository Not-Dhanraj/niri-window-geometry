from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from pathlib import Path

from .config import DaemonConfig
from .constants import DEFAULT_CONFIG, DEFAULT_CONFIG_FILE, DEFAULT_STATE_FILE
from .restore import WindowRestoreDaemon
from .ipc import output_geometries_from_json
from .niri import NiriClient, run_forever
from .shutdown import ShutdownController
from .store import StateStore


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persist and restore niri window geometry by app_id.")
    parser.add_argument("--config-file", type=Path, default=DEFAULT_CONFIG_FILE, help="Path to the JSON config file.")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE, help="Path to the JSON state file.")
    parser.add_argument("--dry-run", action="store_true", help="Log intended actions without changing windows/state.")
    parser.add_argument("--print-default-config", action="store_true", help="Print a default config.json and exit.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.print_default_config:
        json.dump(DEFAULT_CONFIG, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    stop_controller = ShutdownController()

    def request_stop(signum: int, _frame: object) -> None:
        stop_controller.stop(signum)

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    store = StateStore(args.state_file, dry_run=args.dry_run)
    store.load()
    config = DaemonConfig.load(args.config_file)
    logging.info(
        "Live updates are %s",
        "enabled" if config.live_updates else "disabled",
    )
    niri_client = NiriClient(dry_run=args.dry_run)
    outputs = output_geometries_from_json(niri_client.query_json(["outputs"]))
    daemon = WindowRestoreDaemon(store, niri_client, config=config, outputs=outputs)
    run_forever(daemon, stop_controller.requested, stop_controller.set_process)
    daemon.flush_live_state()
    return 0
