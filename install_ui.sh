#!/usr/bin/env bash

# 🧠 Understanding Agent — UI Dashboard Standalone Installer
#
# Installs and configures the Understanding Agent Dashboard UI server
# on this machine or remote server without touching Git hooks.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Lizasatasiya/understanding-agentHook/main/install_ui.sh | bash
#   or locally:
#   ./install_ui.sh [--start] [--host 0.0.0.0] [--port 8000]
#
# Options:
#   --start               Immediately launch dashboard after installation
#   --host <HOST>         Host address to bind (default: 0.0.0.0 for LAN/remote access)
#   --port <PORT>         Port to run the dashboard on (default: 8000)
#   --repo-root <PATH>    Path to git repo to load sessions from (default: auto-detect)
#   --status              Check UI installation health
#   --uninstall           Remove UI binary and virtual environment

set -e

# Styling
CYAN='\033[96m'
GREEN='\033[92m'
YELLOW='\033[93m'
RED='\033[91m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

say() { printf "${CYAN}${BOLD}==>${RESET} ${BOLD}%s${RESET}\n" "$1"; }
ok()  { printf "${GREEN}✓ %s${RESET}\n" "$1"; }
warn() { printf "${YELLOW}⚠ %s${RESET}\n" "$1"; }
err() { printf "${RED}✗ ERROR: %s${RESET}\n" "$1" >&2; exit 1; }

REPO_URL="https://github.com/Lizasatasiya/understanding-agentHook.git"
INSTALL_DIR="$HOME/.local/share/understanding-agent-ui"
VENV_DIR="$INSTALL_DIR/venv"
BIN_DIR="$HOME/.local/bin"

# Parse CLI flags
DO_START=false
DO_STATUS=false
DO_UNINSTALL=false
ARG_HOST="0.0.0.0"
ARG_PORT="8000"
ARG_REPO_ROOT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --start) DO_START=true; shift ;;
        --host|-H) ARG_HOST="$2"; shift 2 ;;
        --port|-p) ARG_PORT="$2"; shift 2 ;;
        --repo-root|-r) ARG_REPO_ROOT="$2"; shift 2 ;;
        --status) DO_STATUS=true; shift ;;
        --uninstall) DO_UNINSTALL=true; shift ;;
        -h|--help)
            echo "Usage: install_ui.sh [--start] [--host <HOST>] [--port <PORT>] [--repo-root <PATH>] [--status] [--uninstall]"
            echo ""
            echo "Options:"
            echo "  --start               Start the UI server immediately after installation"
            echo "  --host, -H <HOST>     Host address to bind (default: 0.0.0.0 for LAN access)"
            echo "  --port, -p <PORT>     Port to bind (default: 8000)"
            echo "  --repo-root, -r <DIR> Git repo path to load sessions and history from"
            echo "  --status              Check UI installation health"
            echo "  --uninstall           Remove UI installation and symlinks"
            exit 0
            ;;
        *) shift ;;
    esac
done

# STATUS CHECK
if [ "$DO_STATUS" = true ]; then
    say "Understanding Agent UI Status"
    if [ -x "$VENV_DIR/bin/understanding-agent-ui" ]; then
        ok "UI package installed in: $VENV_DIR"
    elif command -v understanding-agent-ui >/dev/null 2>&1; then
        ok "UI binary found in PATH: $(command -v understanding-agent-ui)"
    else
        warn "understanding-agent-ui binary not found"
    fi

    if [ -L "$BIN_DIR/understanding-agent-ui" ]; then
        ok "Symlink active at: $BIN_DIR/understanding-agent-ui -> $(readlink "$BIN_DIR/understanding-agent-ui")"
    fi
    exit 0
fi

# UNINSTALL
if [ "$DO_UNINSTALL" = true ]; then
    say "Uninstalling Understanding Agent UI..."
    rm -f "$BIN_DIR/understanding-agent-ui"
    ok "Removed binary symlink ($BIN_DIR/understanding-agent-ui)"
    if [ -d "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
        ok "Removed UI installation directory ($INSTALL_DIR)"
    fi
    printf "\n${GREEN}${BOLD}Uninstallation complete.${RESET}\n"
    exit 0
fi

# INSTALLATION
printf "\n${BOLD}${CYAN}📊 Understanding Agent — UI Dashboard Setup${RESET}\n"
printf "${DIM}Visual commit intelligence, defense attempts, and standards compliance.${RESET}\n\n"

# 1. Locate / verify modern Python (>= 3.10)
say "1/3 Checking Python runtime..."
PYTHON_CANDIDATE=""
for cand in \
    "$HOME/.local/bin/python3" \
    "$(command -v python3 2>/dev/null || true)" \
    "/opt/homebrew/bin/python3" \
    "/usr/local/bin/python3" \
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3" \
    "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3" \
    "/Library/Frameworks/Python.framework/Versions/3.10/bin/python3"
do
    if [ -n "$cand" ] && [ -x "$cand" ]; then
        if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            PYTHON_CANDIDATE="$cand"
            break
        fi
    fi
done

if [ -z "$PYTHON_CANDIDATE" ]; then
    warn "Python >= 3.10 not found. Attempting to install via uv..."
    if ! command -v uv >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
    uv python install 3.12 --default
    PYTHON_CANDIDATE="$HOME/.local/bin/python3"
fi

PY_VER="$("$PYTHON_CANDIDATE" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
ok "Using Python $PY_VER ($PYTHON_CANDIDATE)"

# 2. Install UI package with [ui] dependencies into isolated virtualenv
say "2/3 Installing UI dashboard package & dependencies..."
mkdir -p "$INSTALL_DIR" "$BIN_DIR"

if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_CANDIDATE" -m venv "$VENV_DIR"
fi

# Detect if running from within a local clone or via curl
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/setup.py" ] && [ -d "$SCRIPT_DIR/understanding_agent" ]; then
    ok "Installing from local source: $SCRIPT_DIR (with [ui] dependencies)"
    "$VENV_DIR/bin/pip" install --upgrade --quiet -e "$SCRIPT_DIR[ui]"
else
    ok "Installing directly from GitHub: $REPO_URL (with [ui] dependencies)"
    "$VENV_DIR/bin/pip" install --upgrade --quiet "git+$REPO_URL#egg=understanding-agent[ui]"
fi

# 3. Create symlink in ~/.local/bin
say "3/3 Registering UI binary..."
ln -sf "$VENV_DIR/bin/understanding-agent-ui" "$BIN_DIR/understanding-agent-ui"
ok "Installed understanding-agent-ui in $BIN_DIR"

# Detect host LAN IP for remote access
LOCAL_IP=""
if command -v ipconfig >/dev/null 2>&1; then
    LOCAL_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
elif command -v hostname >/dev/null 2>&1; then
    LOCAL_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
fi
[ -z "$LOCAL_IP" ] && LOCAL_IP="<DEVICE_IP>"

# Ensure ~/.local/bin is in PATH for current shell
export PATH="$BIN_DIR:$PATH"

printf "\n${GREEN}${BOLD}🎉 Understanding Agent UI is installed!${RESET}\n\n"
printf "  ${BOLD}Access URLs:${RESET}\n"
printf "  • Local:   ${CYAN}http://localhost:%s/ui${RESET}\n" "$ARG_PORT"
printf "  • Network: ${CYAN}http://%s:%s/ui${RESET}  ${DIM}${RESET}\n\n" "$LOCAL_IP" "$ARG_PORT"
printf "  ${BOLD}Run anytime with:${RESET}\n"
printf "    ${BOLD}understanding-agent-ui --host 0.0.0.0 --port %s${RESET}\n" "$ARG_PORT"
printf "  or:\n"
printf "    ${BOLD}./start_ui.sh${RESET}\n\n"

# Launch if requested
if [ "$DO_START" = true ]; then
    say "Starting Understanding Agent UI Dashboard..."
    RUN_ARGS=(--host "$ARG_HOST" --port "$ARG_PORT")
    if [ -n "$ARG_REPO_ROOT" ]; then
        RUN_ARGS+=(--repo-root "$ARG_REPO_ROOT")
    fi
    exec "$VENV_DIR/bin/understanding-agent-ui" "${RUN_ARGS[@]}"
fi
