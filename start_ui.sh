#!/usr/bin/env bash

# 🧠 Understanding Agent — UI Dashboard Quick Launcher
#
# Starts the Understanding Agent web dashboard on this machine.
# Access locally or from other devices on the same network.
#
# Usage:
#   ./start_ui.sh
#   ./start_ui.sh --port 8080
#   ./start_ui.sh --host 0.0.0.0 --port 8000
#   ./start_ui.sh --repo-root /path/to/repo

set -e

# Styling
CYAN='\033[96m'
GREEN='\033[92m'
YELLOW='\033[93m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"

# Default settings
HOST="${UNDERSTANDING_AGENT_UI_HOST:-0.0.0.0}"
PORT="${UNDERSTANDING_AGENT_UI_PORT:-8000}"
REPO_ROOT="${UNDERSTANDING_AGENT_REPO_ROOT:-}"
EXTRA_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --port|-p) PORT="$2"; shift 2 ;;
        --host|-H) HOST="$2"; shift 2 ;;
        --repo-root|-r) REPO_ROOT="$2"; shift 2 ;;
        --reload) EXTRA_ARGS+=(--reload); shift ;;
        -h|--help)
            echo "Usage: ./start_ui.sh [--host <HOST>] [--port <PORT>] [--repo-root <PATH>] [--reload]"
            echo ""
            echo "Options:"
            echo "  --host, -H <HOST>     Host address to bind (default: 0.0.0.0 for remote/LAN access)"
            echo "  --port, -p <PORT>     Port to bind (default: 8000)"
            echo "  --repo-root, -r <DIR> Git repo path to load commit sessions from"
            echo "  --reload              Enable auto-reload on code change"
            exit 0
            ;;
        *) EXTRA_ARGS+=("$1"); shift ;;
    esac
done

# Locate understanding-agent-ui binary
UI_BIN=""
if [ -x "$HOME/.local/share/understanding-agent-ui/venv/bin/understanding-agent-ui" ]; then
    UI_BIN="$HOME/.local/share/understanding-agent-ui/venv/bin/understanding-agent-ui"
elif [ -x "$HOME/.local/share/understanding-agent/venv/bin/understanding-agent-ui" ]; then
    UI_BIN="$HOME/.local/share/understanding-agent/venv/bin/understanding-agent-ui"
elif command -v understanding-agent-ui >/dev/null 2>&1; then
    UI_BIN="$(command -v understanding-agent-ui)"
elif [ -x "$HOME/.local/bin/understanding-agent-ui" ]; then
    UI_BIN="$HOME/.local/bin/understanding-agent-ui"
fi

# Auto-install UI dependencies if not found
if [ -z "$UI_BIN" ]; then
    printf "${YELLOW}⚠ UI binary not found. Running installer first...${RESET}\n"
    if [ -f "$SCRIPT_DIR/install_ui.sh" ]; then
        bash "$SCRIPT_DIR/install_ui.sh"
        UI_BIN="$HOME/.local/share/understanding-agent-ui/venv/bin/understanding-agent-ui"
    else
        printf "${RED}✗ Error: Please run install_ui.sh first.${RESET}\n" >&2
        exit 1
    fi
fi

# Detect Local IP address
LOCAL_IP=""
if command -v ipconfig >/dev/null 2>&1; then
    LOCAL_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
elif command -v hostname >/dev/null 2>&1; then
    LOCAL_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
fi
[ -z "$LOCAL_IP" ] && LOCAL_IP="<DEVICE_IP>"

printf "\n${BOLD}${CYAN}📊 Starting Understanding Agent UI Dashboard...${RESET}\n\n"
printf "  ${BOLD}URLs:${RESET}\n"
printf "  • Local:   ${CYAN}http://localhost:%s/ui${RESET}\n" "$PORT"
if [ "$HOST" = "0.0.0.0" ]; then
    printf "  • Network: ${GREEN}${BOLD}http://%s:%s/ui${RESET}  ${DIM}(access from phone / laptop on same Wi-Fi)${RESET}\n" "$LOCAL_IP" "$PORT"
fi
printf "\n  ${DIM}Press Ctrl+C to stop the server.${RESET}\n\n"

CMD=("$UI_BIN" --host "$HOST" --port "$PORT")
if [ -n "$REPO_ROOT" ]; then
    CMD+=(--repo-root "$REPO_ROOT")
fi
if [ ${#EXTRA_ARGS[@]} -gt 0 ]; then
    CMD+=("${EXTRA_ARGS[@]}")
fi

exec "${CMD[@]}"
