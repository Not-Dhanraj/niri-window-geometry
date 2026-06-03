#!/usr/bin/env bash
set -euo pipefail

# niri-window-geometry install script
# First time (from repo root):  bash install.sh install
# After install (anywhere):      bash install.sh [update|reinstall|calibrate|remove|status] [--dry-run|--force]

INSTALL_DIR="${HOME}/.local/share/niri-window-geometry"
CONFIG_DIR="${HOME}/.config/niri-window-geometry"
STATE_DIR="${HOME}/.local/state/niri-window-geometry"
CONFIG_FILE="${CONFIG_DIR}/config.json"
STATE_FILE="${STATE_DIR}/state.json"
GITHUB_REPO="https://github.com/Not-Dhanraj/niri-window-geometry.git"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

DRY_RUN=false
FORCE=false
LOCAL=false
MODE=""

say()  { printf "${GREEN}==>${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}==>${NC} %s\n" "$*"; }
err()  { printf "${RED}==>${NC} %s\n" "$*"; }
header() { printf "\n${BOLD}%s${NC}\n" "$*"; }

usage() {
    cat <<EOF
Usage: bash install.sh <MODE> [--dry-run] [--force] [--local]

Modes:
  install     Copy files, create config, calibrate, and set up autostart.
  update      Fetch latest from GitHub. Preserves config and calibration.
  reinstall   Remove and re-install. Keeps config, re-runs calibration.
  calibrate   Only run the working-area calibration tool.
  remove      Remove everything installed by this script.
  status      Show what is currently installed and where.

Options:
  --dry-run   Show what would be done without actually doing it.
  --force     Skip all prompts and use defaults (niri autostart, no verbose).
  --local     Use local code instead of fetching from GitHub (for update).
  --help, -h  Show this help.

Run this from the root of the cloned repository:
  cd niri-window-geometry
  bash install.sh install
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            install|update|reinstall|calibrate|remove|status)
                MODE="$1"
                ;;
            --dry-run)
                DRY_RUN=true
                ;;
            --local)
                LOCAL=true
                ;;
            --force)
                FORCE=true
                ;;
            --help|-h)
                usage; exit 0
                ;;
            *)
                err "Unknown argument: $1"
                usage; exit 1
                ;;
        esac
        shift
    done

    if [[ -z "$MODE" ]]; then
        err "No mode given. Use one of: install, update, reinstall, calibrate, remove, status"
        usage; exit 1
    fi
}

run_cmd() {
    if $DRY_RUN; then
        say "[dry-run] $*"
    else
        "$@"
    fi
}

calibrate() {
    local cal_script
    if [[ -x "${INSTALL_DIR}/scripts/calibrate.py" ]]; then
        cal_script="${INSTALL_DIR}/scripts/calibrate.py"
    elif [[ -x "./scripts/calibrate.py" ]]; then
        cal_script="./scripts/calibrate.py"
    else
        err "Cannot find calibrate.py. Run this from the repo root or install first."
        exit 1
    fi

    if $DRY_RUN; then
        say "[dry-run] python3 ${cal_script}"
        return
    fi

    if python3 -c "import json, subprocess" 2>/dev/null; then
        python3 "$cal_script"
    else
        err "python3 is required for calibration."
        exit 1
    fi
}

copy_repo() {
    if [[ ! -f "./daemon.py" ]] || [[ ! -d "./niri_window_geometry" ]]; then
        err "Run this script from the root of the niri-window-geometry repository."
        exit 1
    fi

    local src
    src="$(realpath .)"

    if [[ "$src" == "$(realpath "${INSTALL_DIR}" 2>/dev/null || echo /nonexistent)" ]]; then
        say "Already in the install directory. Nothing to copy."
        return
    fi

    if [[ -d "${INSTALL_DIR}" ]] && [[ -f "${INSTALL_DIR}/daemon.py" ]]; then
        warn "Install directory already exists: ${INSTALL_DIR}"
        warn "Use 'update' instead, or 'remove' first to reinstall."
        exit 1
    fi

    say "Copying repository to ${INSTALL_DIR} ..."
    run_cmd mkdir -p "$(dirname "${INSTALL_DIR}")"

    if git rev-parse --is-inside-work-tree &>/dev/null; then
        local branch
        branch="$(git branch --show-current 2>/dev/null || echo "")"
        local clone_args=()
        if [[ -n "$branch" ]]; then
            clone_args+=(--branch "$branch")
        fi
        say "Cloning from local git working tree..."
        run_cmd git clone "${clone_args[@]}" "$src" "${INSTALL_DIR}"

        # Add GitHub remote so future 'update' calls can fetch from it
        if ! $DRY_RUN; then
            local installed_remote
            installed_remote=$(git -C "${INSTALL_DIR}" remote get-url origin 2>/dev/null || echo "")
            if [[ "$installed_remote" == /* ]] || [[ "$installed_remote" == file://* ]] || [[ -z "$installed_remote" ]]; then
                if [[ -n "$installed_remote" ]]; then
                    git -C "${INSTALL_DIR}" remote set-url origin "${GITHUB_REPO}"
                else
                    git -C "${INSTALL_DIR}" remote add origin "${GITHUB_REPO}"
                fi
                say "Set git remote to ${GITHUB_REPO}"
            fi
        fi
    else
        say "Not a git repository. Cloning from GitHub instead..."
        run_cmd git clone "${GITHUB_REPO}" "${INSTALL_DIR}"
    fi
}

init_config() {
    if [[ -f "${CONFIG_FILE}" ]]; then
        say "Config already exists: ${CONFIG_FILE}"
        return
    fi

    say "Creating default config at ${CONFIG_FILE} ..."
    if ! $DRY_RUN; then
        run_cmd mkdir -p "${CONFIG_DIR}"
        if [[ -f "${INSTALL_DIR}/config.example.json" ]]; then
            run_cmd cp "${INSTALL_DIR}/config.example.json" "${CONFIG_FILE}"
        else
            # Generate from daemon
            run_cmd python3 "${INSTALL_DIR}/daemon.py" --print-default-config > "${CONFIG_FILE}"
        fi
    fi
}

# Interactive setup helpers

ask_autostart() {
    # Sets AUTOSTART_METHOD: niri | systemd | none
    if $FORCE; then
        AUTOSTART_METHOD="niri"
        return
    fi

    echo ""
    header "Autostart Setup"
    say "How should the daemon start automatically?"
    echo ""
    echo "  1) niri config  — add spawn-sh-at-startup to config.kdl (recommended)"
    echo "  2) systemd      — create a systemd user service (auto-restarts on crash)"
    echo "  3) none         — skip autostart, start manually"
    echo ""
    local choice
    read -r -p "Choose [1/2/3] (default: 1): " choice
    case "${choice:-1}" in
        1) AUTOSTART_METHOD="niri" ;;
        2) AUTOSTART_METHOD="systemd" ;;
        3) AUTOSTART_METHOD="none" ;;
        *) warn "Invalid choice, defaulting to niri config."; AUTOSTART_METHOD="niri" ;;
    esac
}

ask_verbose() {
    # Sets ENABLE_VERBOSE: true | false
    if $FORCE; then
        ENABLE_VERBOSE=false
        return
    fi

    echo ""
    say "Enable verbose (debug) logging? Useful for the first run to verify"
    say "everything works. You can change this later in your autostart config."
    local answer
    read -r -p "Enable verbose logging? [y/N]: " answer
    if [[ "$answer" =~ ^[Yy]$ ]]; then
        ENABLE_VERBOSE=true
    else
        ENABLE_VERBOSE=false
    fi
}

setup_autostart_niri() {
    local daemon_cmd="python3 ${INSTALL_DIR}/daemon.py"
    if $ENABLE_VERBOSE; then
        daemon_cmd="${daemon_cmd} --verbose"
    fi
    local spawn_line="spawn-sh-at-startup \"${daemon_cmd}\""

    # Detect niri config location
    local niri_config="${HOME}/.config/niri/config.kdl"
    if [[ ! -f "$niri_config" ]]; then
        warn "niri config not found at ${niri_config}"
        say "Add this line to your niri config manually:"
        echo "  ${spawn_line}"
        return
    fi

    # Check if already present
    if grep -qF "niri-window-geometry" "$niri_config" 2>/dev/null || \
       grep -qF "daemon.py" "$niri_config" 2>/dev/null; then
        warn "An autostart line for niri-window-geometry already exists in ${niri_config}"
        say "Skipping. Edit it manually if you need to change it."
        return
    fi

    say "Adding autostart line to ${niri_config} ..."
    if $DRY_RUN; then
        say "[dry-run] echo '${spawn_line}' >> ${niri_config}"
    else
        printf '\n// niri-window-geometry daemon\n%s\n' "${spawn_line}" >> "$niri_config"
        say "Added. Reload niri config to apply:"
        echo "  niri msg action load-config-file"
    fi
}

setup_autostart_systemd() {
    local daemon_cmd="/usr/bin/python3 %h/.local/share/niri-window-geometry/daemon.py"
    if $ENABLE_VERBOSE; then
        daemon_cmd="${daemon_cmd} --verbose"
    fi

    local service_dir="${HOME}/.config/systemd/user"
    local service_file="${service_dir}/niri-window-geometry.service"

    say "Creating systemd user service at ${service_file} ..."
    if $DRY_RUN; then
        say "[dry-run] mkdir -p ${service_dir}"
        say "[dry-run] write ${service_file}"
        say "[dry-run] systemctl --user daemon-reload"
        say "[dry-run] systemctl --user enable --now niri-window-geometry.service"
    else
        mkdir -p "$service_dir"
        cat > "$service_file" <<SERVICEEOF
[Unit]
Description=Restore niri window geometry
After=graphical-session.target

[Service]
ExecStart=${daemon_cmd}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
SERVICEEOF
        systemctl --user daemon-reload
        systemctl --user enable --now niri-window-geometry.service
        say "systemd service enabled and started."
    fi
}

setup_autostart() {
    case "$AUTOSTART_METHOD" in
        niri)    setup_autostart_niri ;;
        systemd) setup_autostart_systemd ;;
        none)
            say "Skipping autostart setup."
            say "Start the daemon manually:"
            local cmd="python3 ${INSTALL_DIR}/daemon.py"
            if $ENABLE_VERBOSE; then cmd="${cmd} --verbose"; fi
            echo "  ${cmd}"
            ;;
    esac
}


do_install() {
    header "Installing niri-window-geometry"

    # 1. Copy repo
    copy_repo

    # 2. Init config
    init_config

    # 3. Calibrate
    header "Calibration"
    say "Now detecting working area offsets for each monitor..."
    say "This corrects floating window position drift caused by panels/bars."
    echo ""
    calibrate

    # 4. Ask about autostart and verbose
    ask_autostart
    ask_verbose

    # 5. Set up autostart
    header "Setting up autostart"
    setup_autostart

    # 6. Done
    header "Installation complete"
    echo ""
    say "The daemon is ready."
    case "$AUTOSTART_METHOD" in
        niri)
            say "Reload niri config to start:"
            echo "  niri msg action load-config-file"
            ;;
        systemd)
            say "The systemd service is already running."
            say "Check status with: systemctl --user status niri-window-geometry"
            ;;
        none)
            say "Start manually:"
            local cmd="python3 ${INSTALL_DIR}/daemon.py"
            if $ENABLE_VERBOSE; then cmd="${cmd} --verbose"; fi
            echo "  ${cmd}"
            ;;
    esac
    echo ""
    if $DRY_RUN; then
        say "(dry-run: nothing was actually changed)"
    fi
}

restart_daemon() {
    local service_active=false
    if systemctl --user is-active niri-window-geometry.service &>/dev/null; then
        service_active=true
    fi

    if $service_active; then
        say "Restarting systemd service..."
        if $DRY_RUN; then
            say "[dry-run] systemctl --user restart niri-window-geometry.service"
        else
            systemctl --user restart niri-window-geometry.service
            say "systemd service restarted."
        fi
        return
    fi

    if pgrep -f "daemon.py" >/dev/null 2>&1; then
        say "Stopping running daemon..."
        if $DRY_RUN; then
            say "[dry-run] pkill -f daemon.py"
        else
            pkill -f "daemon.py" 2>/dev/null || true
            sleep 0.5
        fi

        local niri_config="${HOME}/.config/niri/config.kdl"
        if [[ -f "$niri_config" ]] && grep -qF "daemon.py" "$niri_config" 2>/dev/null; then
            say "Reloading niri config to restart daemon..."
            if $DRY_RUN; then
                say "[dry-run] niri msg action load-config-file"
            else
                niri msg action load-config-file 2>/dev/null || true
                say "Daemon restarted via niri config reload."
            fi
        else
            say "Starting daemon directly..."
            if $DRY_RUN; then
                say "[dry-run] python3 ${INSTALL_DIR}/daemon.py &"
            else
                nohup python3 "${INSTALL_DIR}/daemon.py" >/dev/null 2>&1 &
                say "Daemon started (pid $!)."
            fi
        fi
    else
        say "Starting daemon..."
        if $DRY_RUN; then
            say "[dry-run] python3 ${INSTALL_DIR}/daemon.py &"
        else
            nohup python3 "${INSTALL_DIR}/daemon.py" >/dev/null 2>&1 &
            say "Daemon started (pid $!)."
        fi
    fi
}

do_update() {
    header "Updating niri-window-geometry"

    if [[ ! -d "${INSTALL_DIR}" ]]; then
        err "Not installed at ${INSTALL_DIR}. Use 'install' first."
        exit 1
    fi

    if $LOCAL; then
        if [[ ! -f "./daemon.py" ]] || [[ ! -d "./niri_window_geometry" ]]; then
            err "Run with --local from the root of the niri-window-geometry repository."
            exit 1
        fi

        local src
        src="$(realpath .)"
        if [[ "$src" == "$(realpath "${INSTALL_DIR}" 2>/dev/null || echo /nonexistent)" ]]; then
            say "Already in the install directory. Nothing to copy."
        else
            say "Copying local code to ${INSTALL_DIR} ..."
            if $DRY_RUN; then
                say "[dry-run] rsync local files to ${INSTALL_DIR}"
            else
                cp -f ./daemon.py "${INSTALL_DIR}/daemon.py"
                cp -rf ./niri_window_geometry/ "${INSTALL_DIR}/niri_window_geometry/"
                if [[ -d "./scripts" ]]; then
                    cp -rf ./scripts/ "${INSTALL_DIR}/scripts/"
                fi
                if [[ -f "./config.example.json" ]]; then
                    cp -f ./config.example.json "${INSTALL_DIR}/config.example.json"
                fi
            fi
        fi
    elif [[ -d "${INSTALL_DIR}/.git" ]]; then
        say "Fetching latest changes from GitHub..."
        if $DRY_RUN; then
            say "[dry-run] git -C ${INSTALL_DIR} fetch origin"
            say "[dry-run] git -C ${INSTALL_DIR} reset --hard origin/master"
        else
            local remote; remote=$(git -C "${INSTALL_DIR}" remote get-url origin 2>/dev/null || echo "")
            if [[ -z "$remote" ]]; then
                warn "No git remote configured. Adding origin: ${GITHUB_REPO}"
                git -C "${INSTALL_DIR}" remote add origin "${GITHUB_REPO}"
            fi
            git -C "${INSTALL_DIR}" fetch origin --tags --force
            local branch; branch=$(git -C "${INSTALL_DIR}" branch --show-current 2>/dev/null || echo "master")
            if git -C "${INSTALL_DIR}" rev-parse --verify "origin/${branch}" &>/dev/null; then
                git -C "${INSTALL_DIR}" reset --hard "origin/${branch}"
            else
                git -C "${INSTALL_DIR}" reset --hard "origin/master"
            fi
        fi
    else
        warn "Not a git repository. Re-cloning from GitHub..."
        if $DRY_RUN; then
            say "[dry-run] rm -rf ${INSTALL_DIR}"
            say "[dry-run] git clone ${GITHUB_REPO} ${INSTALL_DIR}"
        else
            rm -rf "${INSTALL_DIR}"
            git clone "${GITHUB_REPO}" "${INSTALL_DIR}"
        fi
    fi

    say "Update complete. Config and calibration offsets are preserved."

    restart_daemon

    if $DRY_RUN; then
        say "(dry-run: nothing was actually changed)"
    fi
}

do_reinstall() {
    header "Reinstalling niri-window-geometry"

    if [[ ! -d "${INSTALL_DIR}" ]]; then
        err "Not installed at ${INSTALL_DIR}. Use 'install' first."
        exit 1
    fi

    if ! $FORCE && ! $DRY_RUN; then
        echo ""
        warn "This will remove ${INSTALL_DIR} and reinstall from scratch."
        warn "Config and state files will be kept."
        read -r -p "Proceed? [y/N] " answer
        [[ "$answer" =~ ^[Yy]$ ]] || { say "Aborted."; return; }
    fi

    say "Removing current install directory..."
    run_cmd rm -rf "${INSTALL_DIR}"

    say "Cloning fresh copy from GitHub..."
    if $DRY_RUN; then
        say "[dry-run] git clone ${GITHUB_REPO} ${INSTALL_DIR}"
    else
        git clone "${GITHUB_REPO}" "${INSTALL_DIR}"
    fi

    if [[ ! -f "${CONFIG_FILE}" ]]; then
        init_config
    else
        say "Keeping existing config: ${CONFIG_FILE}"
    fi

    # Re-run calibration
    header "Calibration"
    say "Re-detecting working area offsets..."
    echo ""
    calibrate

    header "Reinstall complete"
    echo ""
    say "Restart the daemon to apply:"
    say "  If using niri autostart: niri msg action load-config-file"
    say "  If using systemd: systemctl --user restart niri-window-geometry"
    if $DRY_RUN; then
        say "(dry-run: nothing was actually changed)"
    fi
}

do_calibrate() {
    header "Calibrating working area offsets"
    calibrate
}

confirm_remove() {
    if $FORCE || $DRY_RUN; then
        DELETE_CONFIG=true
        DELETE_STATE=true
        return 0
    fi
    echo ""
    warn "This will remove:"
    echo "  ${INSTALL_DIR}/"
    read -r -p "Remove repository? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || return 1

    if [[ -d "${CONFIG_DIR}" ]]; then
        read -r -p "Remove config at ${CONFIG_DIR}/? [y/N] " answer
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            DELETE_CONFIG=true
        fi
    fi
    if [[ -d "${STATE_DIR}" ]]; then
        read -r -p "Remove learned state at ${STATE_DIR}/? [y/N] " answer
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            DELETE_STATE=true
        fi
    fi
    return 0
}

do_remove() {
    header "Removing niri-window-geometry"

    DELETE_CONFIG=false
    DELETE_STATE=false
    if ! confirm_remove; then
        say "Aborted."
        return
    fi

    say "Stopping any running daemon..."
    if ! $DRY_RUN; then
        pkill -f "daemon.py" 2>/dev/null || true
        # Also try systemd
        systemctl --user stop niri-window-geometry.service 2>/dev/null || true
        systemctl --user disable niri-window-geometry.service 2>/dev/null || true
    fi

    say "Removing install directory..."
    run_cmd rm -rf "${INSTALL_DIR}"

    if $DELETE_CONFIG; then
        say "Removing config..."
        run_cmd rm -rf "${CONFIG_DIR}"
    else
        say "Keeping config at ${CONFIG_DIR}/"
    fi

    if $DELETE_STATE; then
        say "Removing state..."
        run_cmd rm -rf "${STATE_DIR}"
    else
        say "Keeping state at ${STATE_DIR}/"
    fi

    local service_file="${HOME}/.config/systemd/user/niri-window-geometry.service"
    if [[ -f "$service_file" ]]; then
        say "Removing systemd service..."
        run_cmd rm -f "$service_file"
        run_cmd systemctl --user daemon-reload 2>/dev/null || true
    fi

    local niri_config="${HOME}/.config/niri/config.kdl"
    if [[ -f "$niri_config" ]] && grep -qF "niri-window-geometry" "$niri_config" 2>/dev/null; then
        say "Removing autostart lines from ${niri_config} ..."
        if $DRY_RUN; then
            say "[dry-run] remove niri-window-geometry lines from ${niri_config}"
        else
            sed -i '/\/\/ niri-window-geometry daemon/d' "$niri_config"
            sed -i '/spawn-sh-at-startup.*niri-window-geometry/d' "$niri_config"
            say "Removed. Reload niri config: niri msg action load-config-file"
        fi
    elif [[ -f "$niri_config" ]] && grep -qF "daemon.py" "$niri_config" 2>/dev/null; then
        warn "Found a daemon.py autostart line in ${niri_config}"
        say "Remove it manually if it belongs to niri-window-geometry."
    fi

    echo ""
    if $DRY_RUN; then
        say "(dry-run: nothing was actually changed)"
    fi
}

do_status() {
    header "niri-window-geometry status"
    echo ""

    if [[ -d "${INSTALL_DIR}" ]] && [[ -f "${INSTALL_DIR}/daemon.py" ]]; then
        say "Repository:    installed at ${INSTALL_DIR}"
        if [[ -d "${INSTALL_DIR}/.git" ]]; then
            local remote; remote=$(git -C "${INSTALL_DIR}" remote get-url origin 2>/dev/null || echo "unknown")
            local branch; branch=$(git -C "${INSTALL_DIR}" branch --show-current 2>/dev/null || echo "unknown")
            echo "               git: ${remote} (${branch})"
        fi
    else
        warn "Repository:    not installed"
    fi

    if [[ -f "${CONFIG_FILE}" ]]; then
        say "Config:        ${CONFIG_FILE}"
        local offsets; offsets=$(python3 -c "
import json, sys
try:
    cfg = json.load(open('${CONFIG_FILE}'))
    off = cfg.get('working_area_offsets', {})
    if off:
        for name, o in off.items():
            print(f'  {name}: x={o.get(\"x\",0)}, y={o.get(\"y\",0)}')
    else:
        print('  (no working area offsets configured)')
except Exception as e:
    print(f'  (error reading: {e})')
" 2>/dev/null || echo "  (error reading config)")
        echo "$offsets"
    else
        warn "Config:        not found"
    fi

    if [[ -f "${STATE_FILE}" ]]; then
        say "State:         ${STATE_FILE}"
    else
        warn "State:         not found (created on first run)"
    fi

    # Check if daemon is running
    echo ""
    if pgrep -f "daemon.py" >/dev/null 2>&1; then
        say "Daemon:        running ($(pgrep -f daemon.py | head -3 | paste -sd ' ' -))"
    else
        warn "Daemon:        not running"
    fi

    # Check systemd
    if systemctl --user is-enabled niri-window-geometry.service &>/dev/null; then
        say "systemd:       enabled"
    fi
}

parse_args "$@"

case "$MODE" in
    install)   do_install ;;
    update)    do_update ;;
    reinstall) do_reinstall ;;
    calibrate) do_calibrate ;;
    remove)    do_remove ;;
    status)    do_status ;;
    *)         err "Unknown mode: $MODE"; usage; exit 1 ;;
esac
