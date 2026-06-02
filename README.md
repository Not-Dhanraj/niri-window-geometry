# niri-window-geometry

`niri-window-geometry` is a small helper daemon for
[niri](https://github.com/YaLTeR/niri). It remembers how an app looked when you
closed it and applies that geometry the next time the same app opens.

The daemon keys state by niri `app_id`. It deliberately does not remember
workspaces, so apps still open wherever niri would normally put them. What it
does remember is the useful window state around that app: tiled size, floating
state, floating position, maximized column state, and fullscreen state.

The code uses only the Python standard library.

## Requirements

- niri with `niri msg` IPC available
- Python 3.10 or newer
- a running niri session

This was developed against niri `26.04`.

## Install

Clone the repository somewhere stable. The examples below use
`~/.local/share/niri-window-geometry`.

```bash
mkdir -p ~/.local/share
git clone <repo-url> ~/.local/share/niri-window-geometry
cd ~/.local/share/niri-window-geometry
```

Replace `<repo-url>` with this repository's Git URL.

Try it from a terminal inside niri:

```bash
python3 daemon.py --verbose
```

The daemon writes state the first time it learns an app:

```text
~/.local/state/niri-window-geometry/state.json
```

## Configuration

Config is optional. If the file is missing, defaults are used.

Default config path:

```text
~/.config/niri-window-geometry/config.json
```

Print the default config:

```bash
python3 daemon.py --print-default-config
```

Use a custom config:

```bash
python3 daemon.py --config-file ~/.config/niri-window-geometry/config.json
```

Example config:

```json
{
  "apps": {
    "exclude": [],
    "include": []
  },
  "enabled": true,
  "detection": {
    "fullscreen_tolerance_px": 2,
    "maximized_width_tolerance_px": 32
  },
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
    "live_save_delay_ms": 500,
    "live_updates": true
  }
}
```

### Config Keys

`enabled`

Turns the daemon on or off without changing your autostart setup.

`restore_delay_ms`

How long to wait after a window opens before applying restore commands. A small
delay helps with apps that resize themselves immediately after launch.

`tracking.live_updates`

Learns geometry from open windows as their layouts change, instead of waiting
until the window closes. With this on, resizing one open app window can affect
the next window of the same `app_id` that opens.

`tracking.live_save_delay_ms`

How long to debounce state-file writes after live layout changes. The daemon
updates its in-memory state immediately, so same-session restores do not wait
for this delay.

`apps.include`

An allowlist of app IDs. Leave it empty to manage all apps unless they are
excluded.

`apps.exclude`

A blocklist of app IDs. This is useful for apps where geometry restore feels
wrong or gets in the way.

`restore.size`

Enables width and height restore.

`restore.adapt_to_output`

Scales restored sizes from the output where the geometry was learned to the
output where the window opens. This helps when moving between landscape,
portrait, 1080p, 2K, or mixed-scale monitor layouts.

`restore.adapt_floating_position_to_output`

Scales floating X/Y position along with floating size when output adaptation is
enabled.

`restore.floating_state`

Restores whether the app was tiled or floating.

`restore.floating_position`

Restores the saved floating X/Y position.

`restore.tiled_width`

Restores the width of normal tiled windows.

`restore.tiled_height`

Restores tiled window height.

`restore.maximized`

Restores maximized tiled columns with niri's `maximize-column` action.

`restore.fullscreen`

Restores fullscreen windows with niri's `fullscreen-window` action.

`detection.fullscreen_tolerance_px`

Tolerance used when comparing a saved tiled window size to the output size.

`detection.maximized_width_tolerance_px`

Tolerance used when deciding whether a tiled window was effectively a maximized
column.

### Finding App IDs

Use niri's window list:

```bash
niri msg --json windows
```

Look for the `app_id` field, then add it to `apps.include` or `apps.exclude`.

For example, only manage Nautilus and kitty:

```json
{
  "apps": {
    "include": ["org.gnome.Nautilus", "kitty"],
    "exclude": []
  }
}
```

Or manage everything except a browser:

```json
{
  "apps": {
    "include": [],
    "exclude": ["zen"]
  }
}
```

## How Maximized and Fullscreen Detection Works

niri's IPC exposes window size and floating state, but it does not currently
send explicit `is_maximized` or `is_fullscreen` booleans for normal windows.

The daemon handles that by comparing the saved tiled size to the output size:

- fullscreen: width and height are both very close to the output size
- maximized: width is very close to the output width

Those checks are controlled by the `detection` config values. If a normal large
window is detected as maximized, lower `maximized_width_tolerance_px`. If a
maximized column is missed, raise it.

## Live Updates

With the default config, geometry is also learned while windows are still open:

```json
{
  "tracking": {
    "live_updates": true,
    "live_save_delay_ms": 500
  }
}
```

niri sends `WindowLayoutsChanged` events when window layout information changes.
The daemon uses those events to update the app's in-memory geometry right away.
That means if Nautilus is open, you resize it, and then open another Nautilus
window, the new window can restore to the current open window's size.

The state file write is debounced with `live_save_delay_ms` so dragging a window
resize handle does not write to disk for every single layout event. Set it to
`0` if you want every live update written immediately.

Set `live_updates` to `false` if you only want geometry learned when windows
close.

## Output Adaptation

With the default config, normal window sizes are adapted to the current output.
The daemon stores the logical output size where a geometry was learned, then
scales the saved width and height to the output where the app opens next.
Older state entries without `output_width` and `output_height` keep using exact
logical pixels until the daemon learns that app again.

For example, a 1200x800 normal window saved on a 1920x1080 landscape monitor
opens around 675x1422 on a 1080x1920 portrait monitor. Floating positions are
scaled too, then clamped so the floating window stays inside the current output.

Maximized and fullscreen windows do not use raw saved dimensions for the main
restore. They use niri actions instead: `maximize-column` and
`fullscreen-window`.

Disable size adaptation if you prefer exact logical pixels:

```json
{
  "restore": {
    "adapt_to_output": false
  }
}
```

## State File

Default state path:

```text
~/.local/state/niri-window-geometry/state.json
```

Example:

```json
{
  "version": 1,
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
  }
}
```

`mode` can be `normal`, `maximized`, or `fullscreen`.

Workspace information is intentionally absent.

## Start From niri Config

You can start the daemon from niri with `spawn-sh-at-startup`.

For a single-file config, add this to `~/.config/niri/config.kdl`:

```kdl
spawn-sh-at-startup "python3 ~/.local/share/niri-window-geometry/daemon.py"
```

If your config uses included files, put it in your startup file, for example
`~/.config/niri/cfg/autostart.kdl`:

```kdl
spawn-sh-at-startup "python3 ~/.local/share/niri-window-geometry/daemon.py --verbose"
```

If `~` is not expanded on your setup, use the absolute path:

```kdl
spawn-sh-at-startup "python3 /home/YOUR_USER/.local/share/niri-window-geometry/daemon.py"
```

Reload niri config:

```bash
niri msg action load-config-file
```

For automatic restart after a crash, use a systemd user service instead.

## Start With systemd

Create this file:

```text
~/.config/systemd/user/niri-window-geometry.service
```

Service contents:

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

Enable it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now niri-window-geometry.service
```

If you cloned the repo somewhere else, update `ExecStart`.

## Uninstall

If you used niri autostart, remove or comment out the line you added:

```kdl
// spawn-sh-at-startup "python3 ~/.local/share/niri-window-geometry/daemon.py"
```

Then reload niri:

```bash
niri msg action load-config-file
```

If you used systemd:

```bash
systemctl --user disable --now niri-window-geometry.service
rm ~/.config/systemd/user/niri-window-geometry.service
systemctl --user daemon-reload
```

Remove the cloned repo:

```bash
rm -rf ~/.local/share/niri-window-geometry
```

Optionally remove config and learned state:

```bash
rm -rf ~/.config/niri-window-geometry
rm -rf ~/.local/state/niri-window-geometry
```

Keep the state directory if you plan to reinstall and want the old app sizes
back.

## CLI

```text
usage: daemon.py [-h] [--config-file CONFIG_FILE] [--state-file STATE_FILE]
                 [--dry-run] [--print-default-config] [--verbose]
```

`--dry-run` logs what would happen without moving windows or writing state.

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
```

Important classes:

- `WindowRestoreDaemon`: event handling and restore logic
- `WindowGeometry`: the saved geometry record
- `WindowSnapshot`: one window as reported by niri
- `OutputSize`: logical output size
- `StateStore`: JSON state file
- `NiriClient`: wrapper around `niri msg`
- `ShutdownController`: signal-aware shutdown helper

Run tests:

```bash
python3 -m unittest -v
```

Compile check:

```bash
python3 -m py_compile daemon.py test_daemon.py niri_window_geometry/*.py
```

The tests use mocked niri events and do not need a live niri session.

## Troubleshooting

### An app is not restored

Check its `app_id`:

```bash
niri msg --json windows
```

Then check `apps.include` and `apps.exclude`.

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

Floating positions are saved in niri logical coordinates. After changing
monitor layout, scale, or resolution, delete that app from the state file and
let the daemon learn it again.
