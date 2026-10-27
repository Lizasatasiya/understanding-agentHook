#!/usr/bin/env bash
# ==============================================================================
# 🧠 Understanding Agent — 1-Command Global Installer
# ==============================================================================
# Installs the Code Understanding Agent globally across all Git repositories
# on this device with zero repository cloning needed on developer machines.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Lizasatasiya/understanding-agentHook/main/install.sh | bash
#
# Options:
#   --key <API_KEY>       Pre-configure the team LLM API key
#   --provider <NAME>     LLM provider: nous (default), groq, openai, ollama
#   --base-url <URL>      Custom central relay endpoint (no API key on client)
#   --uninstall           Remove global hooks and restore standard git behavior
#   --status              Check current installation and hook health
# ==============================================================================

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
INSTALL_DIR="$HOME/.local/share/understanding-agent"
VENV_DIR="$INSTALL_DIR/venv"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/understanding-agent"
HOOKS_DIR="$CONFIG_DIR/hooks"
ENV_FILE="$CONFIG_DIR/.env"

# Parse CLI flags
ARG_KEY=""
ARG_PROVIDER=""
ARG_BASE_URL=""
DO_UNINSTALL=false
DO_STATUS=false

while [ $# -gt 0 ]; do
    case "$1" in
        --key|-k) ARG_KEY="$2"; shift 2 ;;
        --provider|-p) ARG_PROVIDER="$2"; shift 2 ;;
        --base-url|-u) ARG_BASE_URL="$2"; shift 2 ;;
        --uninstall) DO_UNINSTALL=true; shift ;;
        --status) DO_STATUS=true; shift ;;
        -h|--help)
            echo "Usage: install.sh [--key <API_KEY>] [--provider <NAME>] [--base-url <URL>] [--uninstall] [--status]"
            exit 0
            ;;
        *) shift ;;
    esac
done

# ------------------------------------------------------------------------------
# STATUS CHECK
# ------------------------------------------------------------------------------
if [ "$DO_STATUS" = true ]; then
    say "Understanding Agent Status"
    CURRENT_HOOKS="$(git config --global --get core.hooksPath 2>/dev/null || true)"
    if [ "$CURRENT_HOOKS" = "$HOOKS_DIR" ]; then
        ok "Global Git hooks configured at: $HOOKS_DIR"
    else
        warn "Global Git hooks not set to $HOOKS_DIR (current: '${CURRENT_HOOKS:-default .git/hooks}')"
    fi

    if [ -x "$VENV_DIR/bin/understanding-agent" ]; then
        ok "Package installed in: $VENV_DIR"
    elif command -v understanding-agent >/dev/null 2>&1; then
        ok "Package installed in PATH: $(command -v understanding-agent)"
    else
        warn "understanding-agent binary not found"
    fi

    if [ -f "$ENV_FILE" ]; then
        ok "Global config found at $ENV_FILE"
    else
        warn "No global config file found at $ENV_FILE"
    fi
    exit 0
fi

# ------------------------------------------------------------------------------
# UNINSTALL
# ------------------------------------------------------------------------------
if [ "$DO_UNINSTALL" = true ]; then
    say "Uninstalling Understanding Agent Global Hooks..."
    CURRENT_HOOKS="$(git config --global --get core.hooksPath 2>/dev/null || true)"
    if [ "$CURRENT_HOOKS" = "$HOOKS_DIR" ]; then
        git config --global --unset core.hooksPath
        ok "Removed global core.hooksPath configuration"
    fi
    rm -rf "$HOOKS_DIR"
    ok "Removed global hooks directory ($HOOKS_DIR)"
    printf "\n${GREEN}${BOLD}Uninstallation complete.${RESET} Git will now use default per-project hooks.\n"
    exit 0
fi

# ==============================================================================
# INSTALLATION
# ==============================================================================
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
    ok "Installing directly from GitHub: $REPO_URL"
    "$VENV_DIR/bin/pip" install --upgrade --quiet "git+$REPO_URL"
fi

# Create symlink in ~/.local/bin if directory exists in PATH
ln -sf "$VENV_DIR/bin/understanding-agent" "$BIN_DIR/understanding-agent"
ok "Installed understanding-agent CLI binary"

# 3. Configure global credentials / proxy
say "3/4 Configuring global credentials..."
if [ ! -f "$ENV_FILE" ]; then
    touch "$ENV_FILE"
    chmod 600 "$ENV_FILE"
fi

if [ -n "$ARG_KEY" ]; then
    # Update or append key
    if grep -q "^UNDERSTANDING_AGENT_API_KEY=" "$ENV_FILE"; then
        sed -i.bak "s|^UNDERSTANDING_AGENT_API_KEY=.*|UNDERSTANDING_AGENT_API_KEY=$ARG_KEY|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    else
        echo "UNDERSTANDING_AGENT_API_KEY=$ARG_KEY" >> "$ENV_FILE"
    fi
    ok "Saved API key to $ENV_FILE"
fi

if [ -n "$ARG_PROVIDER" ]; then
    if grep -q "^UNDERSTANDING_AGENT_PROVIDER=" "$ENV_FILE"; then
        sed -i.bak "s|^UNDERSTANDING_AGENT_PROVIDER=.*|UNDERSTANDING_AGENT_PROVIDER=$ARG_PROVIDER|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    else
        echo "UNDERSTANDING_AGENT_PROVIDER=$ARG_PROVIDER" >> "$ENV_FILE"
    fi
    ok "Configured provider: $ARG_PROVIDER"
fi

if [ -n "$ARG_BASE_URL" ]; then
    if grep -q "^UNDERSTANDING_AGENT_BASE_URL=" "$ENV_FILE"; then
        sed -i.bak "s|^UNDERSTANDING_AGENT_BASE_URL=.*|UNDERSTANDING_AGENT_BASE_URL=$ARG_BASE_URL|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    else
        echo "UNDERSTANDING_AGENT_BASE_URL=$ARG_BASE_URL" >> "$ENV_FILE"
    fi
    ok "Configured central relay base URL: $ARG_BASE_URL"
fi

# If file is empty, write a friendly template with notes
if [ ! -s "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<'EOF'
# Understanding Agent Global Configuration
# Optional: If your team uses a central relay server or shared key, configure it here:
# UNDERSTANDING_AGENT_API_KEY=
# UNDERSTANDING_AGENT_PROVIDER=nous
# UNDERSTANDING_AGENT_BASE_URL=
EOF
    chmod 600 "$ENV_FILE"
fi

# 4. Create and register Global Git Hooks
say "4/4 Activating Global Git Hooks..."

# 4a. pre-commit hook
cat > "$HOOKS_DIR/pre-commit" <<'EOF'
#!/bin/sh
# Global Understanding Agent — pre-commit hook

# 1. Chain project-specific hook if present in the target repo
if [ -z "$UNDERSTANDING_AGENT_CHAIN_RUNNING" ]; then
    GIT_DIR="$(git rev-parse --git-dir 2>/dev/null)"
    if [ -n "$GIT_DIR" ] && [ -x "$GIT_DIR/hooks/pre-commit" ]; then
        UNDERSTANDING_AGENT_CHAIN_RUNNING=1 "$GIT_DIR/hooks/pre-commit" "$@" || exit $?
    fi
fi

# 2. Execute understanding-agent
AGENT_BIN=""
if [ -x "$HOME/.local/share/understanding-agent/venv/bin/understanding-agent" ]; then
    AGENT_BIN="$HOME/.local/share/understanding-agent/venv/bin/understanding-agent"
elif command -v understanding-agent >/dev/null 2>&1; then
    AGENT_BIN="$(command -v understanding-agent)"
elif [ -x "$HOME/.local/bin/understanding-agent" ]; then
    AGENT_BIN="$HOME/.local/bin/understanding-agent"
fi

if [ -n "$AGENT_BIN" ]; then
    exec "$AGENT_BIN" "$@"
fi
EOF
chmod +x "$HOOKS_DIR/pre-commit"

# 4b. prepare-commit-msg hook (anti-bypass guard against --no-verify)
cat > "$HOOKS_DIR/prepare-commit-msg" <<'EOF'
#!/bin/sh
# Global Understanding Agent — prepare-commit-msg hook (blocks --no-verify bypass)

# 1. Chain project-specific hook if present in the target repo
if [ -z "$UNDERSTANDING_AGENT_CHAIN_RUNNING" ]; then
    GIT_DIR="$(git rev-parse --git-dir 2>/dev/null)"
    if [ -n "$GIT_DIR" ] && [ -x "$GIT_DIR/hooks/prepare-commit-msg" ]; then
        UNDERSTANDING_AGENT_CHAIN_RUNNING=1 "$GIT_DIR/hooks/prepare-commit-msg" "$@" || exit $?
    fi
fi

# 2. Execute understanding-agent to detect bypass
AGENT_BIN=""
if [ -x "$HOME/.local/share/understanding-agent/venv/bin/understanding-agent" ]; then
    AGENT_BIN="$HOME/.local/share/understanding-agent/venv/bin/understanding-agent"
elif command -v understanding-agent >/dev/null 2>&1; then
    AGENT_BIN="$(command -v understanding-agent)"
elif [ -x "$HOME/.local/bin/understanding-agent" ]; then
    AGENT_BIN="$HOME/.local/bin/understanding-agent"
fi

if [ -n "$AGENT_BIN" ]; then
    exec "$AGENT_BIN" "$@"
fi
EOF
chmod +x "$HOOKS_DIR/prepare-commit-msg"

# 4c. Register global hook directory in Git
git config --global core.hooksPath "$HOOKS_DIR"
ok "Configured git config --global core.hooksPath '$HOOKS_DIR'"

# ------------------------------------------------------------------------------
# FINISHED
# ------------------------------------------------------------------------------
printf "\n${GREEN}${BOLD}🎉 Understanding Agent is now ACTIVE on this device!${RESET}\n\n"
printf "  ${BOLD}What happens now:${RESET}\n"
printf "  • ${GREEN}Every Git repository${RESET} on this machine will automatically require oral defense on commit.\n"
printf "  • No repository cloning or per-project configuration needed.\n"
printf "  • Anti-bypass guard against ${CYAN}--no-verify${RESET} is permanently enabled.\n"
printf "  • Global configuration lives at: ${DIM}%s${RESET}\n" "$ENV_FILE"
printf "  • To uninstall anytime, run: ${CYAN}install.sh --uninstall${RESET}\n\n"
