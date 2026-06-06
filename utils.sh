#!/usr/bin/env bash
set -euo pipefail

# niri-window-geometry config utility
# Interactive TUI and CLI for managing config.json
#
# Usage:
#   bash utils.sh config              Interactive TUI menu
#   bash utils.sh config show         Pretty-print current config
#   bash utils.sh config set K V      Set a key (dot-notation)
#   bash utils.sh config toggle K     Toggle a boolean key
#   bash utils.sh config add-include [APP]    Add app to include list
#   bash utils.sh config add-exclude [APP]    Add app to exclude list
#   bash utils.sh config remove-include APP   Remove app from include list
#   bash utils.sh config remove-exclude APP   Remove app from exclude list
#   bash utils.sh config reset [KEY]  Reset config (or single key) to defaults
#   bash utils.sh help                Show this help


CONFIG_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/niri-window-geometry"
CONFIG_FILE="${CONFIG_DIR}/config.json"
INSTALL_DIR="${HOME}/.local/share/niri-window-geometry"

# Locate config.example.json (same dir as this script, or install dir)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/config.example.json" ]]; then
    DEFAULTS_FILE="${SCRIPT_DIR}/config.example.json"
elif [[ -f "${INSTALL_DIR}/config.example.json" ]]; then
    DEFAULTS_FILE="${INSTALL_DIR}/config.example.json"
else
    DEFAULTS_FILE=""
fi


RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

say()  { printf "${GREEN}==>${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}==>${NC} %s\n" "$*"; }
err()  { printf "${RED}==>${NC} %s\n" "$*" >&2; }


check_jq() {
    if ! command -v jq &>/dev/null; then
        err "jq is required but not found."
        echo ""
        echo "  Install it with your package manager:"
        echo "    sudo pacman -S jq        # Arch"
        echo "    sudo apt install jq      # Debian/Ubuntu"
        echo "    sudo dnf install jq      # Fedora"
        echo "    sudo zypper install jq   # openSUSE"
        exit 1
    fi
}


load_config() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        err "Config file not found: ${CONFIG_FILE}"
        echo "  Run install.sh first to create a default config."
        exit 1
    fi
    local raw_json
    raw_json="$(cat "$CONFIG_FILE")"

    # Deep-merge with defaults so missing keys show their effective values.
    # User values take precedence; defaults fill in any gaps.
    if [[ -n "$DEFAULTS_FILE" && -f "$DEFAULTS_FILE" ]]; then
        CONFIG_JSON=$(jq -s '.[0] * .[1]' "$DEFAULTS_FILE" <(echo "$raw_json"))
    else
        CONFIG_JSON="$raw_json"
    fi

    # Keep the raw user config for save operations (don't write defaults back)
    CONFIG_RAW_JSON="$raw_json"
}

save_config() {
    local json="$1"
    mkdir -p "$(dirname "$CONFIG_FILE")"
    printf '%s\n' "$json" | jq '.' > "${CONFIG_FILE}.tmp"
    mv "${CONFIG_FILE}.tmp" "$CONFIG_FILE"
}

load_defaults() {
    if [[ -z "$DEFAULTS_FILE" ]]; then
        err "Cannot find config.example.json for defaults."
        return 1
    fi
    cat "$DEFAULTS_FILE"
}


prompt_restart_daemon() {
    echo ""
    local answer
    read -r -p "$(printf "${GREEN}==>${NC} Restart daemon to apply changes? [Y/n]: ")" answer
    if [[ ! "$answer" =~ ^[Nn]$ ]]; then
        restart_daemon
    fi
}

restart_daemon() {
    # Check systemd first
    if systemctl --user is-active niri-window-geometry.service &>/dev/null; then
        say "Restarting systemd service..."
        systemctl --user restart niri-window-geometry.service
        say "Service restarted."
        return
    fi

    # Kill running daemon
    if pgrep -f "daemon.py" >/dev/null 2>&1; then
        say "Stopping running daemon..."
        pkill -f "daemon.py" 2>/dev/null || true
        sleep 0.5
    fi

    # Restart via niri config reload or direct
    local niri_config="${HOME}/.config/niri/config.kdl"
    if [[ -f "$niri_config" ]] && grep -qF "daemon.py" "$niri_config" 2>/dev/null; then
        say "Reloading niri config to restart daemon..."
        niri msg action load-config-file 2>/dev/null || true
        say "Daemon restarted via niri config reload."
    else
        # Start directly from install dir or script dir
        local daemon_path=""
        if [[ -f "${INSTALL_DIR}/daemon.py" ]]; then
            daemon_path="${INSTALL_DIR}/daemon.py"
        elif [[ -f "${SCRIPT_DIR}/daemon.py" ]]; then
            daemon_path="${SCRIPT_DIR}/daemon.py"
        fi

        if [[ -n "$daemon_path" ]]; then
            say "Starting daemon..."
            nohup python3 "$daemon_path" >/dev/null 2>&1 &
            say "Daemon started (pid $!)."
        else
            warn "Could not find daemon.py to restart."
        fi
    fi
}


get_running_app_ids() {
    if ! command -v niri &>/dev/null; then
        return
    fi
    niri msg --json windows 2>/dev/null \
        | jq -r '.[].app_id // empty' 2>/dev/null \
        | sort -u
}

interactive_app_picker() {
    local current_list="$1"  # JSON array as string
    local action="$2"        # "add" or "remove"

    if [[ "$action" == "remove" ]]; then
        # Show current list items to pick from
        local count
        count=$(echo "$current_list" | jq 'length')
        if [[ "$count" -eq 0 ]]; then
            warn "List is empty, nothing to remove."
            return 1
        fi

        echo ""
        printf "${BOLD}Current items:${NC}\n"
        local i
        for (( i=0; i<count; i++ )); do
            local item
            item=$(echo "$current_list" | jq -r ".[$i]")
            printf "  ${CYAN}%d)${NC} %s\n" "$((i+1))" "$item"
        done

        echo ""
        local choice
        read -r -p "Enter number to remove (or 'b' to go back): " choice
        if [[ "$choice" == "b" || -z "$choice" ]]; then
            return 1
        fi
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= count )); then
            local removed
            removed=$(echo "$current_list" | jq -r ".[$(( choice - 1 ))]")
            echo "$removed"
            return 0
        else
            err "Invalid selection."
            return 1
        fi
    fi

    # action == "add"
    echo ""
    printf "${BOLD}Currently open apps (from niri):${NC}\n"
    local apps=()
    while IFS= read -r app; do
        [[ -n "$app" ]] && apps+=("$app")
    done < <(get_running_app_ids)

    if [[ ${#apps[@]} -eq 0 ]]; then
        warn "Could not get running apps from niri."
        echo ""
        local manual
        read -r -p "Type an app_id manually (or 'b' to go back): " manual
        if [[ -n "$manual" && "$manual" != "b" ]]; then
            echo "$manual"
            return 0
        fi
        return 1
    fi

    local i
    for i in "${!apps[@]}"; do
        local app="${apps[$i]}"
        # Check if already in list
        local in_list
        in_list=$(echo "$current_list" | jq --arg a "$app" 'map(select(. == $a)) | length')
        if [[ "$in_list" -gt 0 ]]; then
            printf "  ${DIM}%d) %s (already in list)${NC}\n" "$((i+1))" "$app"
        else
            printf "  ${CYAN}%d)${NC} %s\n" "$((i+1))" "$app"
        fi
    done

    echo ""
    local choice
    read -r -p "Enter number to add, type an app_id, or 'b' to go back: " choice
    if [[ "$choice" == "b" || -z "$choice" ]]; then
        return 1
    fi

    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#apps[@]} )); then
        local selected="${apps[$((choice-1))]}"
        local in_list
        in_list=$(echo "$current_list" | jq --arg a "$selected" 'map(select(. == $a)) | length')
        if [[ "$in_list" -gt 0 ]]; then
            warn "'${selected}' is already in the list."
            return 1
        fi
        echo "$selected"
        return 0
    else
        # Treat as manual app_id
        echo "$choice"
        return 0
    fi
}


# Each entry: "section|jq_path|type|display_name|description"
# section: display group  |  jq_path: path in JSON  |  type: bool/int/list
# display_name: shown in TUI  |  description: help text shown under the key

SCHEMA=(
    "General|.enabled|bool|enabled|Enable or disable the daemon entirely"
    "General|.restore_delay_ms|int|restore_delay_ms|Delay (ms) before restoring a newly opened window"
    "Apps|.apps.include|list-app|include|Only track these apps (empty = track all)"
    "Apps|.apps.exclude|list-app|exclude|Never track or restore these apps"
    "Restore|.restore.size|bool|size|Restore window width and height"
    "Restore|.restore.floating_state|bool|floating_state|Restore whether a window was floating or tiled"
    "Restore|.restore.floating_position|bool|floating_position|Restore floating window x,y position"
    "Restore|.restore.tiled_width|bool|tiled_width|Restore column width for tiled windows"
    "Restore|.restore.tiled_height|bool|tiled_height|Restore height for tiled windows"
    "Restore|.restore.maximized|bool|maximized|Restore maximized column state"
    "Restore|.restore.fullscreen|bool|fullscreen|Restore fullscreen state"
    "Restore|.restore.adapt_to_output|bool|adapt_to_output|Scale window size when output resolution differs"
    "Restore|.restore.adapt_floating_position_to_output|bool|adapt_floating_position_to_output|Scale floating position when output resolution differs"
    "Detection|.detection.fullscreen_tolerance_px|int|fullscreen_tolerance_px|Pixel tolerance for detecting fullscreen windows"
    "Detection|.detection.maximized_width_tolerance_px|int|maximized_width_tolerance_px|Pixel tolerance for detecting maximized columns"
    "Tracking|.tracking.live_updates|bool|live_updates|Track window geometry changes in real-time"
    "Tracking|.tracking.live_save_delay_ms|int|live_save_delay_ms|Delay (ms) before writing live changes to disk"
    "Tracking|.tracking.ignore_dialog_like_windows|bool|ignore_dialog_like_windows|Auto-ignore small floating windows that look like dialogs"
    "Tracking|.tracking.dialog_max_width_px|int|dialog_max_width_px|Max width (px) to consider a window dialog-like"
    "Tracking|.tracking.dialog_max_height_px|int|dialog_max_height_px|Max height (px) to consider a window dialog-like"
    "Tracking|.tracking.dialog_title_patterns|list-pattern|dialog_title_patterns|Title regex patterns that indicate a dialog window"
    "Tracking|.tracking.ignore_title_patterns|list-pattern|ignore_title_patterns|Ignore windows with titles matching these patterns (all apps)"
    "Tracking|.tracking.ignore_app_title_patterns|list-app-title|ignore_app_title_patterns|Ignore windows matching both a specific app_id AND title pattern"
)

# Map dot-notation key to jq path
dotkey_to_jqpath() {
    local dotkey="$1"
    case "$dotkey" in
        enabled)                        echo ".enabled" ;;
        restore_delay_ms)               echo ".restore_delay_ms" ;;
        apps.include)                   echo ".apps.include" ;;
        apps.exclude)                   echo ".apps.exclude" ;;
        restore.size)                   echo ".restore.size" ;;
        restore.floating_state)         echo ".restore.floating_state" ;;
        restore.floating_position)      echo ".restore.floating_position" ;;
        restore.tiled_width)            echo ".restore.tiled_width" ;;
        restore.tiled_height)           echo ".restore.tiled_height" ;;
        restore.maximized)              echo ".restore.maximized" ;;
        restore.fullscreen)             echo ".restore.fullscreen" ;;
        restore.adapt_to_output)        echo ".restore.adapt_to_output" ;;
        restore.adapt_floating_position_to_output) echo ".restore.adapt_floating_position_to_output" ;;
        detection.fullscreen_tolerance_px)      echo ".detection.fullscreen_tolerance_px" ;;
        detection.maximized_width_tolerance_px) echo ".detection.maximized_width_tolerance_px" ;;
        tracking.live_updates)                  echo ".tracking.live_updates" ;;
        tracking.live_save_delay_ms)            echo ".tracking.live_save_delay_ms" ;;
        tracking.ignore_dialog_like_windows)    echo ".tracking.ignore_dialog_like_windows" ;;
        tracking.dialog_max_width_px)           echo ".tracking.dialog_max_width_px" ;;
        tracking.dialog_max_height_px)          echo ".tracking.dialog_max_height_px" ;;
        tracking.dialog_title_patterns)         echo ".tracking.dialog_title_patterns" ;;
        tracking.ignore_title_patterns)         echo ".tracking.ignore_title_patterns" ;;
        tracking.ignore_app_title_patterns)     echo ".tracking.ignore_app_title_patterns" ;;
        *)
            err "Unknown key: ${dotkey}"
            echo ""
            return 1
            ;;
    esac
}

# Get dot-notation key from schema entry
schema_dotkey() {
    local entry="$1"
    local section jq_path type name
    IFS='|' read -r section jq_path type name <<< "$entry"

    # Convert jq path to dot-notation
    # .enabled → enabled, .restore.size → restore.size, .apps.exclude → apps.exclude
    local dotkey="${jq_path#.}"
    echo "$dotkey"
}

# Get type for a dot-notation key
get_key_type() {
    local dotkey="$1"
    local jq_path
    jq_path=$(dotkey_to_jqpath "$dotkey") || return 1

    for entry in "${SCHEMA[@]}"; do
        local section ep_jq_path type name
        IFS='|' read -r section ep_jq_path type name <<< "$entry"
        if [[ "$ep_jq_path" == "$jq_path" ]]; then
            echo "$type"
            return 0
        fi
    done
    echo "unknown"
}


cmd_config_show() {
    load_config

    echo ""
    printf "${BOLD}Config:${NC} %s\n" "$CONFIG_FILE"
    echo ""

    local last_section=""
    for entry in "${SCHEMA[@]}"; do
        local section jq_path type name desc
        IFS='|' read -r section jq_path type name desc <<< "$entry"

        if [[ "$section" != "$last_section" ]]; then
            [[ -n "$last_section" ]] && echo ""
            printf "  ${CYAN}▸${NC} ${BOLD}%s${NC}\n" "$section"
            last_section="$section"
        fi

        local value
        value=$(echo "$CONFIG_JSON" | jq -r "$jq_path // empty" 2>/dev/null)
        local raw_value
        raw_value=$(echo "$CONFIG_JSON" | jq "$jq_path" 2>/dev/null)

        local display_val color
        case "$type" in
            bool)
                if [[ "$value" == "true" ]]; then
                    color="$GREEN"
                    display_val="true"
                else
                    color="$RED"
                    display_val="false"
                fi
                printf "  %-44s ${color}%s${NC}\n" "$name" "$display_val"
                ;;
            int)
                printf "  %-44s ${YELLOW}%s${NC}\n" "$name" "$value"
                ;;
            list-app|list-pattern)
                local count
                count=$(echo "$CONFIG_JSON" | jq "$jq_path | length" 2>/dev/null || echo "0")
                if [[ "$count" == "0" ]]; then
                    printf "  %-44s ${DIM}[]${NC}\n" "$name"
                else
                    local compact
                    compact=$(echo "$CONFIG_JSON" | jq -c "$jq_path" 2>/dev/null)
                    if [[ ${#compact} -le 50 ]]; then
                        printf "  %-44s ${CYAN}%s${NC}\n" "$name" "$compact"
                    else
                        printf "  %-44s ${CYAN}[%s items]${NC}\n" "$name" "$count"
                    fi
                fi
                ;;
            list-app-title)
                local count
                count=$(echo "$CONFIG_JSON" | jq "$jq_path | length" 2>/dev/null || echo "0")
                if [[ "$count" == "0" ]]; then
                    printf "  %-44s ${DIM}[]${NC}\n" "$name"
                else
                    printf "  %-44s ${CYAN}[%s rules]${NC}\n" "$name" "$count"
                fi
                ;;
        esac
        printf "  ${DIM}%s${NC}\n" "$desc"
    done

    # Show working_area_offsets if present
    local offsets
    offsets=$(echo "$CONFIG_JSON" | jq '.working_area_offsets // empty' 2>/dev/null)
    if [[ -n "$offsets" && "$offsets" != "null" && "$offsets" != "{}" ]]; then
        echo ""
        printf "  ${CYAN}▸${NC} ${BOLD}Working Area Offsets${NC}\n"
        echo "$CONFIG_JSON" | jq -r '.working_area_offsets | to_entries[] | "    \(.key): x=\(.value.x), y=\(.value.y)"' 2>/dev/null
    fi
    echo ""
}


tui_menu() {
    load_config
    local working_json="$CONFIG_JSON"

    # Track which indices have been modified (0-based)
    declare -A modified=()

    while true; do
        clear 2>/dev/null || true
        echo ""
        printf "  ${CYAN}▶${NC}  ${BOLD}niri-window-geometry${NC} config editor\n"
        printf "  ${DIM}%s${NC}\n" "$CONFIG_FILE"
        echo ""

        local last_section=""
        local idx=0

        for entry in "${SCHEMA[@]}"; do
            local section jq_path type name desc
            IFS='|' read -r section jq_path type name desc <<< "$entry"
            idx=$((idx + 1))

            if [[ "$section" != "$last_section" ]]; then
                [[ -n "$last_section" ]] && echo ""
                printf "  ${CYAN}▸${NC} ${BOLD}%s${NC}\n" "$section"
                last_section="$section"
            fi

            local value
            value=$(echo "$working_json" | jq -r "$jq_path // empty" 2>/dev/null)
            local mod_marker="  "
            if [[ -n "${modified[$idx]+_}" ]]; then
                mod_marker="${YELLOW}* ${NC}"
            fi

            local type_label display_val
            case "$type" in
                bool)
                    type_label="${DIM}[bool]${NC}"
                    if [[ "$value" == "true" ]]; then
                        display_val="${GREEN}true${NC}"
                    else
                        display_val="${RED}false${NC}"
                    fi
                    printf "${mod_marker}${CYAN}%2d)${NC} %-42s ${display_val}  ${type_label}\n" "$idx" "$name"
                    ;;
                int)
                    type_label="${DIM}[int]${NC}"
                    display_val="${YELLOW}${value}${NC}"
                    printf "${mod_marker}${CYAN}%2d)${NC} %-42s ${display_val}  ${type_label}\n" "$idx" "$name"
                    ;;
                list-app|list-pattern)
                    type_label="${DIM}[list]${NC}"
                    local count
                    count=$(echo "$working_json" | jq "$jq_path | length" 2>/dev/null || echo "0")
                    if [[ "$count" == "0" ]]; then
                        display_val="${DIM}[]${NC}"
                    else
                        local compact
                        compact=$(echo "$working_json" | jq -c "$jq_path" 2>/dev/null)
                        if [[ ${#compact} -le 40 ]]; then
                            display_val="${CYAN}${compact}${NC}"
                        else
                            display_val="${CYAN}[${count} items]${NC}"
                        fi
                    fi
                    printf "${mod_marker}${CYAN}%2d)${NC} %-42s ${display_val}  ${type_label}\n" "$idx" "$name"
                    ;;
                list-app-title)
                    type_label="${DIM}[list]${NC}"
                    local count
                    count=$(echo "$working_json" | jq "$jq_path | length" 2>/dev/null || echo "0")
                    if [[ "$count" == "0" ]]; then
                        display_val="${DIM}[]${NC}"
                    else
                        display_val="${CYAN}[${count} rules]${NC}"
                    fi
                    printf "${mod_marker}${CYAN}%2d)${NC} %-42s ${display_val}  ${type_label}\n" "$idx" "$name"
                    ;;
            esac
            printf "      ${DIM}%s${NC}\n" "$desc"
        done

        local has_unsaved=false
        if [[ ${#modified[@]} -gt 0 ]]; then
            has_unsaved=true
        fi

        echo ""
        if $has_unsaved; then
            printf "${YELLOW}*${NC} = unsaved changes\n"
        fi
        echo ""
        local prompt_str="Enter number to edit"
        if $has_unsaved; then
            prompt_str="${prompt_str}, ${BOLD}s${NC} to save"
        fi
        prompt_str="${prompt_str}, ${BOLD}r${NC} to reset, ${BOLD}q${NC} to quit: "
        local choice
        read -r -p "$(printf "$prompt_str")" choice

        case "$choice" in
            q|Q)
                if $has_unsaved; then
                    local save_answer
                    read -r -p "$(printf "${YELLOW}==>${NC} You have unsaved changes. Save before quitting? [Y/n]: ")" save_answer
                    if [[ ! "$save_answer" =~ ^[Nn]$ ]]; then
                        save_config "$working_json"
                        say "Config saved."
                        prompt_restart_daemon
                    fi
                fi
                break
                ;;
            s|S)
                if ! $has_unsaved; then
                    say "No changes to save."
                    sleep 1
                    continue
                fi
                save_config "$working_json"
                CONFIG_JSON="$working_json"
                modified=()
                say "Config saved."
                prompt_restart_daemon
                echo ""
                read -r -p "Press Enter to continue..."
                ;;
            r|R)
                local defaults_json
                if ! defaults_json=$(load_defaults); then
                    read -r -p "Press Enter to continue..."
                    continue
                fi
                local reset_answer
                read -r -p "$(printf "${YELLOW}==>${NC} Reset all settings to defaults? (working_area_offsets preserved) [y/N]: ")" reset_answer
                if [[ "$reset_answer" =~ ^[Yy]$ ]]; then
                    # Preserve working_area_offsets and unknown keys
                    local preserved_offsets
                    preserved_offsets=$(echo "$working_json" | jq '.working_area_offsets // null' 2>/dev/null)

                    # Start from defaults, then merge back preserved data
                    working_json="$defaults_json"
                    if [[ "$preserved_offsets" != "null" && -n "$preserved_offsets" ]]; then
                        working_json=$(echo "$working_json" | jq --argjson off "$preserved_offsets" '.working_area_offsets = $off')
                    fi

                    # Mark all as modified
                    local i
                    for (( i=1; i<=${#SCHEMA[@]}; i++ )); do
                        modified[$i]=1
                    done
                    say "Reset to defaults. Press 's' to save."
                    sleep 1
                fi
                ;;
            *)
                if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#SCHEMA[@]} )); then
                    local entry="${SCHEMA[$((choice-1))]}"
                    local section jq_path type name
                    IFS='|' read -r section jq_path type name <<< "$entry"

                    case "$type" in
                        bool)
                            local current
                            current=$(echo "$working_json" | jq -r "$jq_path" 2>/dev/null)
                            if [[ "$current" == "true" ]]; then
                                working_json=$(echo "$working_json" | jq "$jq_path = false")
                            else
                                working_json=$(echo "$working_json" | jq "$jq_path = true")
                            fi
                            modified[$choice]=1
                            ;;
                        int)
                            local current
                            current=$(echo "$working_json" | jq -r "$jq_path" 2>/dev/null)
                            echo ""
                            local new_val
                            read -r -p "$(printf "New value for ${BOLD}%s${NC} (current: ${YELLOW}%s${NC}, 'b' to cancel): " "$name" "$current")" new_val
                            if [[ "$new_val" == "b" || "$new_val" == "B" ]]; then
                                : # cancel
                            elif [[ "$new_val" =~ ^[0-9]+$ ]]; then
                                working_json=$(echo "$working_json" | jq "$jq_path = $new_val")
                                modified[$choice]=1
                            elif [[ -n "$new_val" ]]; then
                                err "Invalid integer: ${new_val}"
                                sleep 1
                            fi
                            ;;
                        list-app)
                            tui_edit_app_list "$jq_path" "$name"
                            ;;
                        list-pattern)
                            tui_edit_pattern_list "$jq_path" "$name"
                            ;;
                        list-app-title)
                            tui_edit_app_title_list "$jq_path" "$name"
                            ;;
                    esac
                fi
                ;;
        esac
    done
}

# Helper: reset a specific jq_path to its default value and mark modified
tui_reset_field() {
    local jq_path="$1"
    local defaults_json
    if ! defaults_json=$(load_defaults); then
        err "Cannot find config.example.json for defaults."
        sleep 1
        return 1
    fi
    local default_val
    default_val=$(echo "$defaults_json" | jq "$jq_path" 2>/dev/null)
    if [[ -z "$default_val" || "$default_val" == "null" ]]; then
        default_val="[]"
    fi
    working_json=$(echo "$working_json" | jq --argjson v "$default_val" "$jq_path = \$v")
    # Mark modified
    local idx=0
    for entry in "${SCHEMA[@]}"; do
        idx=$((idx + 1))
        local s jp t n
        IFS='|' read -r s jp t n <<< "$entry"
        if [[ "$jp" == "$jq_path" ]]; then
            modified[$idx]=1
            break
        fi
    done
    say "Reset to default."
    sleep 1
}

tui_edit_app_list() {
    local jq_path="$1"
    local name="$2"

    while true; do
        clear 2>/dev/null || true
        printf "${BOLD}Editing: %s${NC}\n" "$name"
        echo ""

        local current_list
        current_list=$(echo "$working_json" | jq -c "$jq_path" 2>/dev/null)
        local count
        count=$(echo "$current_list" | jq 'length' 2>/dev/null || echo "0")

        printf "Current: "
        if [[ "$count" == "0" ]]; then
            printf "${DIM}[]${NC}\n"
        else
            printf "${CYAN}%s${NC}\n" "$current_list"
        fi

        echo ""
        printf "${BOLD}Actions:${NC}\n"
        printf "  ${CYAN}a)${NC} Add app\n"
        if [[ "$count" -gt 0 ]]; then
            printf "  ${CYAN}d)${NC} Remove app\n"
        fi
        printf "  ${CYAN}r)${NC} Reset to default\n"
        printf "  ${CYAN}b)${NC} Back\n"
        echo ""

        local action
        read -r -p "> " action
        case "$action" in
            a|A)
                local app_id
                if app_id=$(interactive_app_picker "$current_list" "add"); then
                    if [[ -n "$app_id" ]]; then
                        working_json=$(echo "$working_json" | jq --arg a "$app_id" "$jq_path += [\$a]")
                        # Find schema index to mark modified
                        local idx=0
                        for entry in "${SCHEMA[@]}"; do
                            idx=$((idx + 1))
                            local s jp t n
                            IFS='|' read -r s jp t n <<< "$entry"
                            if [[ "$jp" == "$jq_path" ]]; then
                                modified[$idx]=1
                                break
                            fi
                        done
                        say "Added '${app_id}'"
                        sleep 1
                    fi
                fi
                ;;
            d|D)
                if [[ "$count" -gt 0 ]]; then
                    local app_id
                    if app_id=$(interactive_app_picker "$current_list" "remove"); then
                        if [[ -n "$app_id" ]]; then
                            working_json=$(echo "$working_json" | jq --arg a "$app_id" "$jq_path |= map(select(. != \$a))")
                            local idx=0
                            for entry in "${SCHEMA[@]}"; do
                                idx=$((idx + 1))
                                local s jp t n
                                IFS='|' read -r s jp t n <<< "$entry"
                                if [[ "$jp" == "$jq_path" ]]; then
                                    modified[$idx]=1
                                    break
                                fi
                            done
                            say "Removed '${app_id}'"
                            sleep 1
                        fi
                    fi
                fi
                ;;
            r|R)
                tui_reset_field "$jq_path"
                ;;
            b|B) return ;;
        esac
    done
}

tui_edit_pattern_list() {
    local jq_path="$1"
    local name="$2"

    while true; do
        clear 2>/dev/null || true
        printf "${BOLD}Editing: %s${NC}\n" "$name"
        echo ""

        local current_list
        current_list=$(echo "$working_json" | jq -c "$jq_path" 2>/dev/null)
        local count
        count=$(echo "$current_list" | jq 'length' 2>/dev/null || echo "0")

        if [[ "$count" == "0" ]]; then
            printf "Current: ${DIM}[]${NC}\n"
        else
            printf "Current:\n"
            local i
            for (( i=0; i<count; i++ )); do
                local item
                item=$(echo "$working_json" | jq -r "${jq_path}[$i]" 2>/dev/null)
                printf "  ${CYAN}%d)${NC} %s\n" "$((i+1))" "$item"
            done
        fi

        echo ""
        printf "${BOLD}Actions:${NC}\n"
        printf "  ${CYAN}a)${NC} Add pattern\n"
        if [[ "$count" -gt 0 ]]; then
            printf "  ${CYAN}d)${NC} Remove pattern\n"
        fi
        printf "  ${CYAN}r)${NC} Reset to default\n"
        printf "  ${CYAN}b)${NC} Back\n"
        echo ""

        local action
        read -r -p "> " action
        case "$action" in
            a|A)
                echo ""
                local titles=()
                # Fetch currently open window titles for interactive picking
                if command -v niri &>/dev/null; then
                    local win_data
                    win_data=$(niri msg --json windows 2>/dev/null)
                    if [[ -n "$win_data" ]]; then
                        printf "${BOLD}Currently open windows:${NC}\n"
                        while IFS= read -r line; do
                            [[ -n "$line" ]] && titles+=("$line")
                        done < <(echo "$win_data" | jq -r '.[] | "\(.app_id // "?") — \(.title // "(no title)")"' 2>/dev/null | sort -u)

                        local i
                        for i in "${!titles[@]}"; do
                            printf "  ${CYAN}%d)${NC} %s\n" "$((i+1))" "${titles[$i]}"
                        done
                        echo ""
                    fi
                fi

                local pattern
                if [[ ${#titles[@]} -gt 0 ]]; then
                    read -r -p "Enter number to use title, type a regex, or 'b' to cancel: " pattern
                    if [[ "$pattern" == "b" || "$pattern" == "B" ]]; then
                        continue
                    fi
                    if [[ "$pattern" =~ ^[0-9]+$ ]] && (( pattern >= 1 && pattern <= ${#titles[@]} )); then
                        # Extract just the title part (after " — ")
                        local selected="${titles[$((pattern-1))]}"
                        pattern="${selected#* — }"
                        # Escape regex special chars to match literally
                        pattern=$(printf '%s' "$pattern" | sed 's/[.[\(*+?{|^$\\]/\\&/g')
                        pattern="^${pattern}$"
                    fi
                else
                    read -r -p "Enter pattern (regex to match titles, or 'b' to cancel): " pattern
                    if [[ "$pattern" == "b" || "$pattern" == "B" ]]; then
                        continue
                    fi
                fi

                if [[ -n "$pattern" ]]; then
                    working_json=$(echo "$working_json" | jq --arg p "$pattern" "$jq_path += [\$p]")
                    local idx=0
                    for entry in "${SCHEMA[@]}"; do
                        idx=$((idx + 1))
                        local s jp t n
                        IFS='|' read -r s jp t n <<< "$entry"
                        if [[ "$jp" == "$jq_path" ]]; then
                            modified[$idx]=1
                            break
                        fi
                    done
                    say "Added pattern: ${pattern}"
                    sleep 1
                fi
                ;;
            d|D)
                if [[ "$count" -gt 0 ]]; then
                    echo ""
                    local num
                    read -r -p "Enter number to remove (or 'b' to cancel): " num
                    if [[ "$num" == "b" || "$num" == "B" ]]; then
                        continue
                    fi
                    if [[ "$num" =~ ^[0-9]+$ ]] && (( num >= 1 && num <= count )); then
                        local removed
                        removed=$(echo "$working_json" | jq -r "${jq_path}[$((num-1))]" 2>/dev/null)
                        working_json=$(echo "$working_json" | jq "del(${jq_path}[$((num-1))])")
                        local idx=0
                        for entry in "${SCHEMA[@]}"; do
                            idx=$((idx + 1))
                            local s jp t n
                            IFS='|' read -r s jp t n <<< "$entry"
                            if [[ "$jp" == "$jq_path" ]]; then
                                modified[$idx]=1
                                break
                            fi
                        done
                        say "Removed: ${removed}"
                        sleep 1
                    else
                        err "Invalid selection."
                        sleep 1
                    fi
                fi
                ;;
            r|R)
                tui_reset_field "$jq_path"
                ;;
            b|B) return ;;
        esac
    done
}

tui_edit_app_title_list() {
    local jq_path="$1"
    local name="$2"

    while true; do
        clear 2>/dev/null || true
        printf "${BOLD}Editing: %s${NC}\n" "$name"
        printf "${DIM}Each rule is an {app_id, title} pair. Windows matching both are ignored.${NC}\n"
        echo ""

        local count
        count=$(echo "$working_json" | jq "$jq_path | length" 2>/dev/null || echo "0")

        if [[ "$count" == "0" ]]; then
            printf "Current: ${DIM}[]${NC}\n"
        else
            printf "Current:\n"
            local i
            for (( i=0; i<count; i++ )); do
                local app_id title_pat
                app_id=$(echo "$working_json" | jq -r "${jq_path}[$i].app_id // \"\"" 2>/dev/null)
                title_pat=$(echo "$working_json" | jq -r "${jq_path}[$i].title // \"\"" 2>/dev/null)
                printf "  ${CYAN}%d)${NC} app_id: ${GREEN}%s${NC}  title: ${YELLOW}%s${NC}\n" "$((i+1))" "$app_id" "$title_pat"
            done
        fi

        echo ""
        printf "${BOLD}Actions:${NC}\n"
        printf "  ${CYAN}a)${NC} Add rule\n"
        if [[ "$count" -gt 0 ]]; then
            printf "  ${CYAN}d)${NC} Remove rule\n"
        fi
        printf "  ${CYAN}r)${NC} Reset to default\n"
        printf "  ${CYAN}b)${NC} Back\n"
        echo ""

        local action
        read -r -p "> " action
        case "$action" in
            a|A)
                echo ""
                # Show all open windows with app_id and title
                local win_lines=()
                local win_app_ids=()
                local win_titles=()
                if command -v niri &>/dev/null; then
                    local win_data
                    win_data=$(niri msg --json windows 2>/dev/null)
                    if [[ -n "$win_data" ]]; then
                        while IFS=$'\t' read -r wapp wtitle; do
                            [[ -n "$wapp" ]] || continue
                            win_app_ids+=("$wapp")
                            win_titles+=("$wtitle")
                            win_lines+=("${wapp} — ${wtitle}")
                        done < <(echo "$win_data" | jq -r '.[] | "\(.app_id // "?")\t\(.title // "(no title)")"' 2>/dev/null | sort)
                    fi
                fi

                if [[ ${#win_lines[@]} -gt 0 ]]; then
                    printf "${BOLD}Currently open windows:${NC}\n"
                    local i
                    for i in "${!win_lines[@]}"; do
                        printf "  ${CYAN}%d)${NC} %s\n" "$((i+1))" "${win_lines[$i]}"
                    done
                    echo ""
                    local choice
                    read -r -p "Pick a window number, type an app_id, or 'b' to cancel: " choice
                    if [[ "$choice" == "b" || "$choice" == "B" ]]; then
                        continue
                    fi

                    local app_id title_prefill=""
                    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#win_lines[@]} )); then
                        app_id="${win_app_ids[$((choice-1))]}"
                        # Pre-fill title as escaped exact-match regex
                        local raw_title="${win_titles[$((choice-1))]}"
                        title_prefill=$(printf '%s' "$raw_title" | sed 's/[.[\(*+?{|^$\\]/\\&/g')
                        title_prefill="^${title_prefill}$"
                        printf "${DIM}Auto-filled app_id: %s${NC}\n" "$app_id"
                    else
                        app_id="$choice"
                    fi
                else
                    warn "Could not get running windows from niri."
                    local app_id title_prefill=""
                    read -r -p "Type app_id (or 'b' to cancel): " app_id
                    if [[ "$app_id" == "b" || "$app_id" == "B" ]]; then
                        continue
                    fi
                fi

                if [[ -z "$app_id" ]]; then
                    continue
                fi

                local title_pat
                if [[ -n "$title_prefill" ]]; then
                    printf "Pre-filled title pattern: ${YELLOW}%s${NC}\n" "$title_prefill"
                    read -r -p "Press Enter to accept, type a new regex, or 'b' to cancel: " title_pat
                    if [[ "$title_pat" == "b" || "$title_pat" == "B" ]]; then
                        continue
                    fi
                    if [[ -z "$title_pat" ]]; then
                        title_pat="$title_prefill"
                    fi
                else
                    read -r -p "Enter title pattern (regex, or 'b' to cancel): " title_pat
                    if [[ "$title_pat" == "b" || "$title_pat" == "B" ]]; then
                        continue
                    fi
                    if [[ -z "$title_pat" ]]; then
                        err "Title pattern cannot be empty."
                        sleep 1
                        continue
                    fi
                fi

                working_json=$(echo "$working_json" | jq --arg a "$app_id" --arg t "$title_pat" \
                    "$jq_path += [{\"app_id\": \$a, \"title\": \$t}]")
                local idx=0
                for entry in "${SCHEMA[@]}"; do
                    idx=$((idx + 1))
                    local s jp t n
                    IFS='|' read -r s jp t n <<< "$entry"
                    if [[ "$jp" == "$jq_path" ]]; then
                        modified[$idx]=1
                        break
                    fi
                done
                say "Added rule: app_id='${app_id}', title='${title_pat}'"
                sleep 1
                ;;
            d|D)
                if [[ "$count" -gt 0 ]]; then
                    echo ""
                    local num
                    read -r -p "Enter number to remove (or 'b' to cancel): " num
                    if [[ "$num" == "b" || "$num" == "B" ]]; then
                        continue
                    fi
                    if [[ "$num" =~ ^[0-9]+$ ]] && (( num >= 1 && num <= count )); then
                        local removed_app removed_title
                        removed_app=$(echo "$working_json" | jq -r "${jq_path}[$((num-1))].app_id" 2>/dev/null)
                        removed_title=$(echo "$working_json" | jq -r "${jq_path}[$((num-1))].title" 2>/dev/null)
                        working_json=$(echo "$working_json" | jq "del(${jq_path}[$((num-1))])")
                        local idx=0
                        for entry in "${SCHEMA[@]}"; do
                            idx=$((idx + 1))
                            local s jp t n
                            IFS='|' read -r s jp t n <<< "$entry"
                            if [[ "$jp" == "$jq_path" ]]; then
                                modified[$idx]=1
                                break
                            fi
                        done
                        say "Removed: app_id='${removed_app}', title='${removed_title}'"
                        sleep 1
                    else
                        err "Invalid selection."
                        sleep 1
                    fi
                fi
                ;;
            r|R)
                tui_reset_field "$jq_path"
                ;;
            b|B) return ;;
        esac
    done
}


cmd_config_set() {
    local dotkey="$1"
    local value="$2"

    load_config
    local jq_path
    jq_path=$(dotkey_to_jqpath "$dotkey") || exit 1
    if [[ -z "$jq_path" ]]; then
        exit 1
    fi

    local key_type
    key_type=$(get_key_type "$dotkey")

    case "$key_type" in
        bool)
            if [[ "$value" != "true" && "$value" != "false" ]]; then
                err "Value for boolean key '${dotkey}' must be 'true' or 'false', got '${value}'"
                exit 1
            fi
            CONFIG_JSON=$(echo "$CONFIG_JSON" | jq "$jq_path = $value")
            ;;
        int)
            if [[ ! "$value" =~ ^[0-9]+$ ]]; then
                err "Value for integer key '${dotkey}' must be a number, got '${value}'"
                exit 1
            fi
            CONFIG_JSON=$(echo "$CONFIG_JSON" | jq "$jq_path = $value")
            ;;
        list-app|list-pattern|list-app-title)
            err "Use add-include/add-exclude/remove-include/remove-exclude for list keys."
            exit 1
            ;;
        *)
            err "Unknown key type for '${dotkey}'"
            exit 1
            ;;
    esac

    save_config "$CONFIG_JSON"
    say "Set ${dotkey} = ${value}"
    prompt_restart_daemon
}

cmd_config_toggle() {
    local dotkey="$1"

    load_config
    local jq_path
    jq_path=$(dotkey_to_jqpath "$dotkey") || exit 1
    if [[ -z "$jq_path" ]]; then
        exit 1
    fi

    local key_type
    key_type=$(get_key_type "$dotkey")
    if [[ "$key_type" != "bool" ]]; then
        err "Cannot toggle non-boolean key '${dotkey}' (type: ${key_type})"
        exit 1
    fi

    local current
    current=$(echo "$CONFIG_JSON" | jq -r "$jq_path" 2>/dev/null)
    local new_val
    if [[ "$current" == "true" ]]; then
        new_val="false"
    else
        new_val="true"
    fi

    CONFIG_JSON=$(echo "$CONFIG_JSON" | jq "$jq_path = $new_val")
    save_config "$CONFIG_JSON"
    say "Toggled ${dotkey}: ${current} → ${new_val}"
    prompt_restart_daemon
}

cmd_config_add_list() {
    local list_name="$1"  # "include" or "exclude"
    local app_id="${2:-}"
    local jq_path=".apps.${list_name}"

    load_config

    if [[ -z "$app_id" ]]; then
        # Interactive picker
        local current_list
        current_list=$(echo "$CONFIG_JSON" | jq -c "$jq_path" 2>/dev/null)
        if app_id=$(interactive_app_picker "$current_list" "add"); then
            if [[ -z "$app_id" ]]; then
                return
            fi
        else
            return
        fi
    fi

    # Check if already in list
    local in_list
    in_list=$(echo "$CONFIG_JSON" | jq --arg a "$app_id" "$jq_path | map(select(. == \$a)) | length" 2>/dev/null)
    if [[ "$in_list" -gt 0 ]]; then
        warn "'${app_id}' is already in ${list_name} list."
        return
    fi

    CONFIG_JSON=$(echo "$CONFIG_JSON" | jq --arg a "$app_id" "$jq_path += [\$a]")
    save_config "$CONFIG_JSON"
    say "Added '${app_id}' to ${list_name} list."
    prompt_restart_daemon
}

cmd_config_remove_list() {
    local list_name="$1"  # "include" or "exclude"
    local app_id="$2"
    local jq_path=".apps.${list_name}"

    load_config

    # Check if in list
    local in_list
    in_list=$(echo "$CONFIG_JSON" | jq --arg a "$app_id" "$jq_path | map(select(. == \$a)) | length" 2>/dev/null)
    if [[ "$in_list" -eq 0 ]]; then
        warn "'${app_id}' is not in ${list_name} list."
        return
    fi

    CONFIG_JSON=$(echo "$CONFIG_JSON" | jq --arg a "$app_id" "$jq_path |= map(select(. != \$a))")
    save_config "$CONFIG_JSON"
    say "Removed '${app_id}' from ${list_name} list."
    prompt_restart_daemon
}

cmd_config_reset() {
    local dotkey="${1:-}"

    load_config
    local defaults_json
    if ! defaults_json=$(load_defaults); then
        exit 1
    fi

    if [[ -n "$dotkey" ]]; then
        # Reset a single key
        local jq_path
        jq_path=$(dotkey_to_jqpath "$dotkey") || exit 1
        if [[ -z "$jq_path" ]]; then
            exit 1
        fi

        local default_val
        default_val=$(echo "$defaults_json" | jq "$jq_path" 2>/dev/null)
        if [[ -z "$default_val" || "$default_val" == "null" ]]; then
            err "No default value found for '${dotkey}'"
            exit 1
        fi

        CONFIG_JSON=$(echo "$CONFIG_JSON" | jq --argjson v "$default_val" "$jq_path = \$v")
        save_config "$CONFIG_JSON"
        say "Reset ${dotkey} to default: ${default_val}"
        prompt_restart_daemon
    else
        # Reset all (preserve working_area_offsets and unknown keys)
        local answer
        read -r -p "$(printf "${YELLOW}==>${NC} Reset all settings to defaults? (working_area_offsets preserved) [y/N]: ")" answer
        if [[ ! "$answer" =~ ^[Yy]$ ]]; then
            say "Aborted."
            return
        fi

        local preserved_offsets
        preserved_offsets=$(echo "$CONFIG_JSON" | jq '.working_area_offsets // null' 2>/dev/null)

        # Collect unknown keys from current config (keys not in defaults)
        local unknown_keys
        unknown_keys=$(echo "$CONFIG_JSON" | jq --argjson def "$defaults_json" '
            to_entries | map(select(.key as $k | ($def | has($k) | not) and $k != "working_area_offsets"))
            | from_entries
        ' 2>/dev/null)

        CONFIG_JSON="$defaults_json"

        # Restore preserved data
        if [[ "$preserved_offsets" != "null" && -n "$preserved_offsets" ]]; then
            CONFIG_JSON=$(echo "$CONFIG_JSON" | jq --argjson off "$preserved_offsets" '.working_area_offsets = $off')
        fi
        if [[ -n "$unknown_keys" && "$unknown_keys" != "{}" ]]; then
            CONFIG_JSON=$(echo "$CONFIG_JSON" | jq --argjson extra "$unknown_keys" '. + $extra')
        fi

        save_config "$CONFIG_JSON"
        say "Config reset to defaults."
        prompt_restart_daemon
    fi
}


show_help() {
    cat <<'EOF'
Usage: bash utils.sh <command> [sub-command] [args...]

Commands:
  config                    Interactive TUI config editor
  config show               Pretty-print current config with colors
  config set <key> <value>  Set a config key (dot-notation)
  config toggle <key>       Toggle a boolean config key
  config add-include [app]  Add app to include list (interactive if no app given)
  config add-exclude [app]  Add app to exclude list (interactive if no app given)
  config remove-include <app>  Remove app from include list
  config remove-exclude <app>  Remove app from exclude list
  config reset [key]        Reset all settings or a single key to defaults
  help, --help, -h          Show this help

Key Format (dot-notation):
  enabled                       restore_delay_ms
  apps.include                  apps.exclude
  restore.size                  restore.floating_state
  restore.floating_position     restore.tiled_width
  restore.tiled_height          restore.maximized
  restore.fullscreen            restore.adapt_to_output
  restore.adapt_floating_position_to_output
  detection.fullscreen_tolerance_px
  detection.maximized_width_tolerance_px
  tracking.live_updates         tracking.live_save_delay_ms
  tracking.ignore_dialog_like_windows
  tracking.dialog_max_width_px  tracking.dialog_max_height_px
  tracking.dialog_title_patterns
  tracking.ignore_title_patterns
  tracking.ignore_app_title_patterns

Examples:
  bash utils.sh config                          # Open interactive editor
  bash utils.sh config show                     # Show current config
  bash utils.sh config set restore.size false   # Disable size restore
  bash utils.sh config toggle restore.maximized # Toggle maximized restore
  bash utils.sh config add-exclude              # Pick app to exclude (interactive)
  bash utils.sh config add-exclude zen          # Add 'zen' to exclude list
  bash utils.sh config reset restore.size       # Reset single key to default
  bash utils.sh config reset                    # Reset entire config to defaults
EOF
}


main() {
    if [[ $# -eq 0 ]]; then
        show_help
        exit 0
    fi

    check_jq

    local command="$1"
    shift

    case "$command" in
        config)
            if [[ $# -eq 0 ]]; then
                tui_menu
                exit 0
            fi

            local subcmd="$1"
            shift
            case "$subcmd" in
                show)
                    cmd_config_show
                    ;;
                set)
                    if [[ $# -lt 2 ]]; then
                        err "Usage: utils.sh config set <key> <value>"
                        exit 1
                    fi
                    cmd_config_set "$1" "$2"
                    ;;
                toggle)
                    if [[ $# -lt 1 ]]; then
                        err "Usage: utils.sh config toggle <key>"
                        exit 1
                    fi
                    cmd_config_toggle "$1"
                    ;;
                add-include)
                    cmd_config_add_list "include" "${1:-}"
                    ;;
                add-exclude)
                    cmd_config_add_list "exclude" "${1:-}"
                    ;;
                remove-include)
                    if [[ $# -lt 1 ]]; then
                        err "Usage: utils.sh config remove-include <app_id>"
                        exit 1
                    fi
                    cmd_config_remove_list "include" "$1"
                    ;;
                remove-exclude)
                    if [[ $# -lt 1 ]]; then
                        err "Usage: utils.sh config remove-exclude <app_id>"
                        exit 1
                    fi
                    cmd_config_remove_list "exclude" "$1"
                    ;;
                reset)
                    cmd_config_reset "${1:-}"
                    ;;
                *)
                    err "Unknown config sub-command: ${subcmd}"
                    show_help
                    exit 1
                    ;;
            esac
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            err "Unknown command: ${command}"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
