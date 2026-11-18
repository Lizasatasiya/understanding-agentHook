#!/usr/bin/env bash
 
# 🧠 Understanding Agent — 1-Command Global Installer
 
# Installs the Code Understanding Agent globally across all Git repositories
# on this device with zero repository cloning needed on developer machines.
#
# Usage (run as root; the model key stays on the defense server):
#   REF=dev
#   curl -fsSL "https://raw.githubusercontent.com/Lizasatasiya/understanding-agentHook/${REF}/install.sh" | REF="$REF" sudo REF="$REF" bash -s -- --server "https://defense.internal" --team-token "TEAM_TOKEN"
#
# Options:
#   --server <URL>        Base URL of the defense service. Required.
#   --team-token <TOKEN>  Shared team token sent as X-Team-Token. Required.
#   --team-secret <SECRET>  Optional HMAC secret sent as X-Signature.
#   --ref <BRANCH>        Git branch or tag to install (or set REF). Required for a curl install.
#   --uninstall           Remove the system hooks and restore standard git behavior
#   --status              Check current installation and hook health
#
# Example:
#   sudo bash install.sh --server "https://defense.internal" --team-token "TEAM_TOKEN" --ref dev
 

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
INSTALL_DIR="/usr/local/share/understanding-agent"
VENV_DIR="$INSTALL_DIR/venv"
BIN_DIR="/usr/local/bin"
CONFIG_DIR="/etc/understanding-agent"
HOOKS_DIR="$CONFIG_DIR/hooks"
ENV_FILE="$CONFIG_DIR/config.env"

# Parse CLI flags
ARG_SERVER=""
ARG_TOKEN=""
ARG_SECRET=""
# REF is set on the bash command so the branch in the curl URL is the one installed.
# A piped script cannot see that URL itself.
ARG_REF="${REF:-${UNDERSTANDING_AGENT_INSTALL_REF:-}}"
DO_UNINSTALL=false
DO_STATUS=false

while [ $# -gt 0 ]; do
    case "$1" in
        --key|-k|--model|-m)
            err "The model key stays on the defense server. Pass --server and --team-token."
            ;;
        --server) ARG_SERVER="$2"; shift 2 ;;
        --team-token) ARG_TOKEN="$2"; shift 2 ;;
        --team-secret) ARG_SECRET="$2"; shift 2 ;;
        --ref|--branch) ARG_REF="$2"; shift 2 ;;
        --uninstall) DO_UNINSTALL=true; shift ;;
        --status) DO_STATUS=true; shift ;;
        -h|--help)
            echo "Usage: sudo install.sh --server <URL> --team-token <TOKEN> [--team-secret <SECRET>] [--ref <branch>]"
            echo "The model API key is configured on the server, not on this machine."
            exit 0
            ;;
        *) shift ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    err "Run this installer as root so developers cannot edit the hook. Example: sudo bash install.sh --server URL --team-token TOKEN"
fi

# STATUS CHECK
if [ "$DO_STATUS" = true ]; then
    say "Understanding Agent Status"
    CURRENT_HOOKS="$(git config --system --get core.hooksPath 2>/dev/null || true)"
    if [ "$CURRENT_HOOKS" = "$HOOKS_DIR" ]; then
        ok "System Git hooks configured at: $HOOKS_DIR"
    else
        warn "System Git hooks not set to $HOOKS_DIR (current: '${CURRENT_HOOKS:-default .git/hooks}')"
    fi

    if [ -x "$VENV_DIR/bin/understanding-agent" ]; then
        ok "Package installed in: $VENV_DIR"
    else
        warn "understanding-agent binary not found"
    fi

    if [ -f "$ENV_FILE" ]; then
        ok "Root-owned server config found at $ENV_FILE"
    else
        warn "No server config file found at $ENV_FILE"
    fi
    exit 0
fi

# UNINSTALL
if [ "$DO_UNINSTALL" = true ]; then
    say "Uninstalling Understanding Agent system hooks..."
    CURRENT_HOOKS="$(git config --system --get core.hooksPath 2>/dev/null || true)"
    if [ "$CURRENT_HOOKS" = "$HOOKS_DIR" ]; then
        git config --system --unset core.hooksPath
        ok "Removed system core.hooksPath configuration"
    fi
    # Older installs wrote a per-user global hooks path. Clear it when it
    # still points at the previous user-level hook directory.
    LEGACY_HOOKS="${HOME}/.config/understanding-agent/hooks"
    USER_HOOKS="$(git config --global --get core.hooksPath 2>/dev/null || true)"
    if [ -n "$SUDO_USER" ]; then
        USER_HOME="$(dscl . -read "/Users/$SUDO_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
        if [ -n "$USER_HOME" ]; then
            LEGACY_HOOKS="$USER_HOME/.config/understanding-agent/hooks"
        fi
    fi
    if [ "$USER_HOOKS" = "$LEGACY_HOOKS" ] || [ "$USER_HOOKS" = "${HOME}/.config/understanding-agent/hooks" ]; then
        git config --global --unset core.hooksPath || true
        ok "Removed legacy per-user core.hooksPath"
    fi
    rm -rf "$CONFIG_DIR" "$INSTALL_DIR"
    rm -f "$BIN_DIR/understanding-agent"
    ok "Removed system hooks, config, and package"
    printf "\n${GREEN}${BOLD}Uninstallation complete.${RESET} Git will now use default per-project hooks.\n"
    exit 0
fi

 
# INSTALLATION
 
printf "\n${BOLD}${CYAN}🧠 Code Understanding Agent — Global Setup${RESET}\n"
printf "${DIM}Protecting all Git repositories on this machine with oral defense.${RESET}\n\n"

# 1. Locate / verify modern Python (>= 3.10)
say "1/4 Checking Python runtime..."
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

# 2. Install package into an isolated virtual environment (never breaks system python)
say "2/4 Installing understanding-agent package..."
mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$CONFIG_DIR" "$HOOKS_DIR"

if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_CANDIDATE" -m venv "$VENV_DIR"
fi

# Detect if running from within a local clone (e.g. testing) or via curl
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/setup.py" ] && [ -d "$SCRIPT_DIR/understanding_agent" ]; then
    ok "Installing from local source: $SCRIPT_DIR"
    "$VENV_DIR/bin/pip" install --upgrade --quiet -e "$SCRIPT_DIR"
else
    if [ -z "$ARG_REF" ]; then
        err "No branch given. The branch in the curl URL is not visible to a piped script, so pass it through. Example: REF=dev curl -fsSL \"https://raw.githubusercontent.com/Lizasatasiya/understanding-agentHook/\${REF}/install.sh\" | sudo REF=\"\$REF\" bash -s -- --server URL --team-token TOKEN"
    fi
    case "$ARG_REF" in
        *[!A-Za-z0-9._/-]*|-*|*/.*|*..*) err "Unsupported branch name '$ARG_REF'." ;;
    esac
    ok "Installing directly from GitHub: $REPO_URL @ $ARG_REF"
    "$VENV_DIR/bin/pip" install --upgrade --force-reinstall --quiet "git+${REPO_URL}@${ARG_REF}"
fi

ln -sf "$VENV_DIR/bin/understanding-agent" "$BIN_DIR/understanding-agent"
ok "Installed understanding-agent CLI binary"

if [ -z "$ARG_SERVER" ] || [ -z "$ARG_TOKEN" ]; then
    err "Pass --server and --team-token. The model API key is configured on the server, not on this machine."
fi

# 3. Server address only. The model key is not written here.
say "3/4 Writing the root-owned server config..."
mkdir -p "$CONFIG_DIR"
umask 077
cat > "$ENV_FILE" <<EOF
# Read by the system hook. Developers cannot change this file.
UNDERSTANDING_AGENT_SERVER_URL=${ARG_SERVER}
UNDERSTANDING_AGENT_TEAM_TOKEN=${ARG_TOKEN}
UNDERSTANDING_AGENT_TEAM_SECRET=${ARG_SECRET}
EOF
chmod 600 "$ENV_FILE"
chown root "$ENV_FILE" 2>/dev/null || true
ok "Saved server URL to $ENV_FILE"

# 4. Create and register system Git hooks. Developers cannot edit these files.
say "4/4 Activating system Git hooks..."
mkdir -p "$HOOKS_DIR"
chmod 755 "$CONFIG_DIR" "$HOOKS_DIR"

# 4a. pre-commit hook
cat > "$HOOKS_DIR/pre-commit" <<'EOF'
#!/bin/sh
# System Understanding Agent — pre-commit hook

# 1. Chain project-specific hook if present in the target repo
if [ -z "$UNDERSTANDING_AGENT_CHAIN_RUNNING" ]; then
    GIT_DIR="$(git rev-parse --git-dir 2>/dev/null)"
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"

    if [ -n "$GIT_DIR" ] && [ -x "$GIT_DIR/hooks/pre-commit" ]; then
        UNDERSTANDING_AGENT_CHAIN_RUNNING=1 "$GIT_DIR/hooks/pre-commit" "$@" || exit $?
    elif [ -n "$REPO_ROOT" ] && [ -x "$REPO_ROOT/.husky/pre-commit" ]; then
        UNDERSTANDING_AGENT_CHAIN_RUNNING=1 "$REPO_ROOT/.husky/pre-commit" "$@" || exit $?
    elif [ -n "$REPO_ROOT" ] && [ -f "$REPO_ROOT/.pre-commit-config.yaml" ]; then
        PRECOMMIT_BIN=""
        if command -v pre-commit >/dev/null 2>&1; then
            PRECOMMIT_BIN="pre-commit"
        elif [ -x "$REPO_ROOT/.venv/bin/pre-commit" ]; then
            PRECOMMIT_BIN="$REPO_ROOT/.venv/bin/pre-commit"
        elif [ -x "$REPO_ROOT/venv/bin/pre-commit" ]; then
            PRECOMMIT_BIN="$REPO_ROOT/venv/bin/pre-commit"
        fi

        if [ -n "$PRECOMMIT_BIN" ]; then
            UNDERSTANDING_AGENT_CHAIN_RUNNING=1 "$PRECOMMIT_BIN" run --hook-stage commit || exit $?
        fi
    fi
fi

# 2. Execute understanding-agent. The binary path is fixed so PATH cannot redirect it.
AGENT_BIN="/usr/local/share/understanding-agent/venv/bin/understanding-agent"
if [ -x "$AGENT_BIN" ]; then
    exec "$AGENT_BIN" "$@"
fi
echo "understanding-agent is not installed at $AGENT_BIN" >&2
exit 1
EOF

# 4b. prepare-commit-msg hook (anti-bypass guard against --no-verify)
cat > "$HOOKS_DIR/prepare-commit-msg" <<'EOF'
#!/bin/sh
# System Understanding Agent — prepare-commit-msg hook (blocks --no-verify bypass)

# 1. Chain project-specific hook if present in the target repo
if [ -z "$UNDERSTANDING_AGENT_CHAIN_RUNNING" ]; then
    GIT_DIR="$(git rev-parse --git-dir 2>/dev/null)"
    REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"

    if [ -n "$GIT_DIR" ] && [ -x "$GIT_DIR/hooks/prepare-commit-msg" ]; then
        UNDERSTANDING_AGENT_CHAIN_RUNNING=1 "$GIT_DIR/hooks/prepare-commit-msg" "$@" || exit $?
    elif [ -n "$REPO_ROOT" ] && [ -x "$REPO_ROOT/.husky/prepare-commit-msg" ]; then
        UNDERSTANDING_AGENT_CHAIN_RUNNING=1 "$REPO_ROOT/.husky/prepare-commit-msg" "$@" || exit $?
    fi
fi

# 2. Execute understanding-agent to confirm the interview that just passed
AGENT_BIN="/usr/local/share/understanding-agent/venv/bin/understanding-agent"
if [ -x "$AGENT_BIN" ]; then
    exec "$AGENT_BIN" "$@"
fi
echo "understanding-agent is not installed at $AGENT_BIN" >&2
exit 1
EOF

# 4c. post-commit deletes the one-commit pass marker
cat > "$HOOKS_DIR/post-commit" <<'EOF'
#!/bin/sh
# Drop the pass marker once the commit exists. The next commit starts a new interview.
GIT_DIR="$(git rev-parse --git-dir 2>/dev/null)" || exit 0
rm -f "$GIT_DIR/understanding_agent_verified.json"
exit 0
EOF

chmod 755 "$HOOKS_DIR/pre-commit" "$HOOKS_DIR/prepare-commit-msg" "$HOOKS_DIR/post-commit"
chown root "$HOOKS_DIR/pre-commit" "$HOOKS_DIR/prepare-commit-msg" "$HOOKS_DIR/post-commit" 2>/dev/null || true

# 4d. Register the hook directory for every user on this machine
git config --system core.hooksPath "$HOOKS_DIR"
ok "Configured git config --system core.hooksPath '$HOOKS_DIR'"

# ------------------------------------------------------------------------------
# FINISHED
# ------------------------------------------------------------------------------
printf "\n${GREEN}${BOLD}🎉 Understanding Agent is now ACTIVE on this device!${RESET}\n\n"
printf "  ${BOLD}What happens now:${RESET}\n"
printf "  • ${GREEN}Every Git repository${RESET} on this machine will require an oral defense on commit.\n"
printf "  • The hook calls the defense server. This machine has no model API key.\n"
printf "  • Hook files are root-owned. Developers cannot edit them.\n"
printf "  • Anti-bypass guard against ${CYAN}--no-verify${RESET} is enabled.\n"
printf "  • Server configuration lives at: ${DIM}%s${RESET}\n" "$ENV_FILE"
printf "  • To uninstall, run: ${CYAN}sudo install.sh --uninstall${RESET}\n\n"
