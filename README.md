# niri-window-geometry

A small Python daemon for [niri](https://github.com/YaLTeR/niri) that
remembers window geometry by `app_id` and restores it when the same app opens
again.

niri places new windows at default sizes. If you always resize an app a certain
way, or want an app to reopen as a floating window, this daemon remembers those
choices and restores them automatically.

**What it remembers:**

- Tiled width and height
- Floating state and position
- Maximized column state
- Fullscreen state
- The output size where the geometry was learned

It does **not** remember workspaces. Reopened apps still appear on the current
workspace according to normal niri behavior.

The code uses only the Python standard library - no external dependencies.

## Showcase

https://github.com/user-attachments/assets/f52835c2-5315-4ea8-aed3-ec825e8df05c

## Requirements

- [niri](https://github.com/YaLTeR/niri) with `niri msg` IPC available
- Python 3.10 or newer
- A running niri session

This project was developed against niri `26.04`.

## Quick Start

The install script handles everything: cloning the repo, creating a config,
running calibration, and setting up autostart.

```bash
git clone https://github.com/Not-Dhanraj/niri-window-geometry.git
cd niri-window-geometry
bash install.sh install
```

The script will walk you through:

1. **Copying** the repo to `~/.local/share/niri-window-geometry`
2. **Creating** a default config at `~/.config/niri-window-geometry/config.json`
3. **Calibrating** working-area offsets for each monitor (for accurate floating
   window positioning)
4. **Setting up autostart** - choose between niri config, systemd, or manual
   start
5. **Verbose logging** - optionally enable debug output

Other install script modes:

| Command | Description |
| --- | --- |
| `bash install.sh update` | Fetch latest from GitHub and restart the daemon. Preserves config and calibration. |
| `bash install.sh update --local` | Update from local code instead of GitHub. Useful for development. |
| `bash install.sh reinstall` | Remove and re-install. Keeps config, re-runs calibration. |
| `bash install.sh calibrate` | Only run the working-area calibration tool. |
| `bash install.sh remove` | Remove everything installed by this script. |
| `bash install.sh status` | Show what is currently installed and where. |

Pass `--dry-run` to preview what any mode would do without making changes.
Pass `--force` to skip all confirmation prompts.
Pass `--local` with `update` to use local code instead of fetching from GitHub.

<details>
<summary><strong>Manual Install</strong></summary>

If you prefer to set things up yourself, clone the repository somewhere stable:

```bash
mkdir -p ~/.local/share
git clone https://github.com/Not-Dhanraj/niri-window-geometry.git ~/.local/share/niri-window-geometry
cd ~/.local/share/niri-window-geometry
```

Run it once from a terminal inside niri:

```bash
python3 daemon.py --verbose
```

The daemon writes learned state to `~/.local/state/niri-window-geometry/state.json`.

**Autostart with niri config** - add to `~/.config/niri/config.kdl`:

```kdl
spawn-sh-at-startup "python3 ~/.local/share/niri-window-geometry/daemon.py"
```

Then reload: `niri msg action load-config-file`

**Autostart with systemd** - create `~/.config/systemd/user/niri-window-geometry.service`:

```ini
[Unit]
Description=Restore niri window geometry
After=graphical-session.target

[Service]
ExecStart=/usr/bin/python3 %h/.local/share/niri-window-geometry/daemon.py
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
```

Then enable: `systemctl --user daemon-reload && systemctl --user enable --now niri-window-geometry.service`

</details>

## Configuration

Configuration is optional. If the config file is missing, the daemon uses the
same defaults printed by `--print-default-config`.

Default config path:

```text
~/.config/niri-window-geometry/config.json
```

Print the default config:

```bash
python3 daemon.py --print-default-config
```

Create a config file from the example:

```bash
mkdir -p ~/.config/niri-window-geometry
cp config.example.json ~/.config/niri-window-geometry/config.json
```

Use a custom config path:

```bash
python3 daemon.py --config-file /path/to/config.json
```

<details>
<summary><strong>View Default Config</strong></summary>

```json
{
  "apps": {
    "exclude": [],
    "include": []
  },
  "detection": {
    "fullscreen_tolerance_px": 2,
    "maximized_width_tolerance_px": 32
  },
  "enabled": true,
  "restore": {
    "adapt_floating_position_to_output": true,
    "adapt_to_output": true,
    "floating_position": true,
    "floating_state": true,
    "fullscreen": true,
    "maximized": true,
    "size": true,
    "tiled_height": true,
    "tiled_width": true
  },
  "restore_delay_ms": 150,
  "tracking": {
    "dialog_max_height_px": 360,
    "dialog_max_width_px": 900,
    "dialog_title_patterns": [
      "\\?$",
      "^(?:Exit|Quit|Abort|Retry|Ignore|Discard)$",
      "^(?:Open|Save|Export|Import)(?:\\s|$)",
      "^Save As$",
      "^(?:Preferences|Settings|Options|Properties|Configuration)$",
      "^(?:About|Log [Ii]n|Sign [Ii]n|Authenticate|Authentication)$",
      "^(?:Confirm|Warning|Error|Info(?:rmation)?)$",
      "^(?:Find|Replace|Search|Go to|Print|Color)(?:\\s|$)",
      "^(?:Choose|Select|Pick)\\s"
    ],
    "ignore_dialog_like_windows": true,
    "ignore_title_patterns": [],
    "live_save_delay_ms": 500,
    "live_updates": true
  }
}
```

</details>

<details>
<summary><strong>Config Keys</strong></summary>

| Key | Default | Description |
| --- | --- | --- |
| `enabled` | `true` | Turns the daemon on or off without changing autostart setup. |
| `restore_delay_ms` | `150` | Delay before applying restore commands after a window opens. |
| `apps.include` | `[]` | App IDs to manage. Empty means all apps unless excluded. |
| `apps.exclude` | `[]` | App IDs to ignore. Useful for apps where restore feels wrong. |
| `tracking.live_updates` | `true` | Learns geometry from open windows as their layouts change. |
| `tracking.live_save_delay_ms` | `500` | Debounces state-file writes after live layout changes. Use `0` to write immediately. |
| `tracking.ignore_dialog_like_windows` | `true` | Skips small floating windows when the same app has another window open or was previously seen as a tiled/larger window. Catches dialogs even when the main window closed to tray. |
| `tracking.dialog_max_width_px` | `900` | Maximum width for the dialog-like window check. |
| `tracking.dialog_max_height_px` | `360` | Maximum height for the dialog-like heuristic check. Title-matched dialogs are caught regardless of size. |
| `tracking.dialog_title_patterns` | _(see default config)_ | Built-in title patterns for dialog detection. Matches common dialog titles like Open, Save, Preferences, etc. Only applies to small floating windows. Set to `[]` to disable. |
| `tracking.ignore_title_patterns` | `[]` | User-defined regex title patterns to unconditionally skip from both restore and save. |
| `restore.size` | `true` | Enables width and height restore. |
| `restore.tiled_width` | `true` | Restores tiled width. Best effort because niri has no `--id` for column width. |
| `restore.tiled_height` | `true` | Restores tiled height. |
| `restore.floating_state` | `true` | Restores whether the app was tiled or floating. |
| `restore.floating_position` | `true` | Restores saved floating X/Y position. |
| `restore.maximized` | `true` | Restores maximized tiled columns with `maximize-column`. Best effort because niri has no `--id` for this action. |
| `restore.fullscreen` | `true` | Restores fullscreen windows with `fullscreen-window`. |
| `restore.adapt_to_output` | `true` | Scales normal-window size from the learned output to the current output. |
| `restore.adapt_floating_position_to_output` | `true` | Scales floating X/Y position during output adaptation and clamps it inside the current output. |
| `detection.fullscreen_tolerance_px` | `2` | Pixel tolerance for fullscreen detection. |
| `detection.maximized_width_tolerance_px` | `32` | Pixel tolerance for maximized-column detection. |

Live updates change in-memory state immediately. The debounce delay only affects
state-file writes.

</details>

## Finding App IDs

<details>
<summary><strong>View Examples</strong></summary>

Use niri's window list:

```bash
niri msg --json windows
```

Look for the `app_id` field, then add it to `apps.include` or `apps.exclude`.

Only manage these two apps:

```json
{
  "apps": {
    "include": ["org.gnome.Nautilus", "kitty"],
    "exclude": []
  }
}
```

Manage everything except one app:

```json
{
  "apps": {
    "include": [],
    "exclude": ["zen"]
  }
}
```

</details>

## CLI Reference

```text
usage: daemon.py [-h] [--config-file CONFIG_FILE] [--state-file STATE_FILE]
                 [--dry-run] [--print-default-config] [--verbose]
```

| Flag | Description |
| --- | --- |
| `--config-file PATH` | Use a config file outside the default config path. |
| `--state-file PATH` | Use a state file outside the default state path. |
| `--dry-run` | Log intended restore commands and state writes without moving windows or writing state. |
| `--print-default-config` | Print the built-in default config and exit. |
| `--verbose` | Enable debug logging. |

## Advanced

<details>
<summary><strong>How Learning Works</strong></summary>

The daemon learns geometry in two ways:

- on `WindowClosed`, using the last cached geometry for that window
- on `WindowLayoutsChanged`, when `tracking.live_updates` is enabled

Live updates are enabled by default. That means if an app is open, you resize
it, and then open another window for the same app, the new window gets restored to the
current open window's size.

State-file writes from live updates are debounced by
`tracking.live_save_delay_ms`, but in-memory state is updated immediately.

</details>

<details>
<summary><strong>Maximized and Fullscreen Detection</strong></summary>

niri's IPC exposes window size and floating state, but not explicit
`is_maximized` or `is_fullscreen` booleans for normal tiled windows.

The daemon handles this by comparing tiled size to output size:

- fullscreen: width and height are both very close to the output size
- maximized: width is very close to the output width

Those checks are controlled by the `detection` config values.

When maximized or fullscreen state is detected, restore uses niri actions instead
of raw saved dimensions:

- maximized: `maximize-column`
- fullscreen: `fullscreen-window`

This matters for mixed-monitor setups. A maximized window learned on a 1080p
output should restore as maximized on a 2K or portrait output, not as a fixed
1080p-sized column.

</details>

<details>
<summary><strong>Output Adaptation</strong></summary>

With the default config, normal window sizes are adapted to the current output.
The daemon stores `output_width` and `output_height` with each learned geometry,
then scales the saved width and height to the output where the app opens next.

Example:

- saved window: `1200x800`
- saved output: `1920x1080`
- current output: `1080x1920`
- restored size: about `675x1422`

Floating positions are scaled too when
`restore.adapt_floating_position_to_output` is enabled.

Older state entries without `output_width` and `output_height` keep using exact
logical pixels until the daemon learns that app again.

Disable output adaptation if you prefer exact logical pixels:

```json
{
  "restore": {
    "adapt_to_output": false
  }
}
```

</details>

<details>
<summary><strong>State File</strong></summary>

Default state path:

```text
~/.local/state/niri-window-geometry/state.json
```

Example:

```json
{
  "apps": {
    "org.gnome.Nautilus": {
      "height": 1024,
      "is_floating": false,
      "mode": "maximized",
      "output_height": 1080,
      "output_width": 1920,
      "updated_at": "2026-05-31T08:00:00+00:00",
      "width": 1904
    }
  },
  "version": 1
}
```

`mode` can be `normal`, `maximized`, or `fullscreen`.

Workspace information is intentionally absent.

</details>

## Calibration

The calibration tool detects working-area offsets caused by panels and bars on
each monitor. This ensures floating window positions are saved and restored
accurately.

Calibration runs automatically during `install` and `reinstall`. To re-run it
manually:

```bash
bash install.sh calibrate
```

Or directly:

```bash
python3 ~/.local/share/niri-window-geometry/scripts/calibrate.py
```

**Re-run calibration when:**

- You add, remove, or rearrange monitors
- You change panel/bar height or position
- Floating windows appear offset after restore

## Troubleshooting

### An app is not restored

Check its `app_id`:

```bash
niri msg --json windows
```

Then check `apps.include` and `apps.exclude`.

Also run with `--verbose` and confirm the daemon logs:

```text
Live updates are enabled
```

### Maximized windows restore as plain large windows

Increase the maximized width tolerance:

```json
{
  "detection": {
    "maximized_width_tolerance_px": 48
  }
}
```

### Fullscreen windows do not restore as fullscreen

Increase the fullscreen tolerance:

```json
{
  "detection": {
    "fullscreen_tolerance_px": 4
  }
}
```

### Floating position is wrong after changing monitors

Output adaptation handles most monitor size and orientation changes. If a
floating position is still wrong, let the daemon learn the app again by moving
or resizing that app while the daemon is running.

If old state is still causing trouble, remove that app's entry from:

```text
~/.local/state/niri-window-geometry/state.json
```

## Uninstall

```bash
bash install.sh remove
```

This stops the daemon, removes the install directory, cleans up the niri config
autostart line, removes the systemd service, and optionally removes config and
learned state.

## Development

```text
daemon.py
niri_window_geometry/
  cli.py
  config.py
  constants.py
  ipc.py
  models.py
  niri.py
  restore.py
  shutdown.py
  store.py
  utils.py
test_daemon.py
config.example.json
install.sh
scripts/
  calibrate.py
```

Run tests:

```bash
python3 -m unittest -v
```

The tests use mocked niri events and do not need a live niri session.

## License

[GPL-3.0](LICENSE)
