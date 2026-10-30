#!/usr/bin/env bash
 
# 🧠 Understanding Agent — 1-Command Global Installer
 
# Installs the Code Understanding Agent globally across all Git repositories
# on this device with zero repository cloning needed on developer machines.
#
# Usage (write the branch once; the same name is installed):
#   REF=dev
#   curl -fsSL "https://raw.githubusercontent.com/Lizasatasiya/understanding-agentHook/${REF}/install.sh" | REF="$REF" bash -s -- --key "YOUR_API_KEY" --model haiku
#
# Options:
#   --key <API_KEY>       API key for the selected model
#   --model <NAME>        sonnet, haiku, or nous (default: nous)
#   --ref <BRANCH>        Git branch or tag to install (or set REF). Required for a curl install.
#   --uninstall           Remove global hooks and restore standard git behavior
#   --status              Check current installation and hook health
#
# Example:
#   REF=main
#   curl -fsSL "https://raw.githubusercontent.com/Lizasatasiya/understanding-agentHook/${REF}/install.sh" | REF="$REF" bash -s -- --key "YOUR_API_KEY" --model sonnet
 

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
ARG_MODEL=""
# REF is set on the bash command so the branch in the curl URL is the one installed.
# A piped script cannot see that URL itself.
ARG_REF="${REF:-${UNDERSTANDING_AGENT_INSTALL_REF:-}}"
DO_UNINSTALL=false
DO_STATUS=false

while [ $# -gt 0 ]; do
    case "$1" in
        --key|-k) ARG_KEY="$2"; shift 2 ;;
        --model|-m) ARG_MODEL="$2"; shift 2 ;;
        --ref|--branch) ARG_REF="$2"; shift 2 ;;
        --uninstall) DO_UNINSTALL=true; shift ;;
        --status) DO_STATUS=true; shift ;;
        -h|--help)
            echo "Usage: install.sh [--ref <branch>] [--key <API_KEY>] [--model sonnet|haiku|nous] [--uninstall] [--status]"
            echo "Curl install: REF=dev curl -fsSL \"https://raw.githubusercontent.com/Lizasatasiya/understanding-agentHook/\${REF}/install.sh\" | REF=\"\$REF\" bash -s -- --key KEY --model haiku"
            exit 0
            ;;
        *) shift ;;
    esac
done

if [ -z "$ARG_MODEL" ]; then
    ARG_MODEL="nous"
fi
ARG_MODEL="$(printf '%s' "$ARG_MODEL" | tr '[:upper:]' '[:lower:]')"
case "$ARG_MODEL" in
    sonnet|haiku|nous) ;;
    *) err "Unsupported model '$ARG_MODEL'. Choose one of: sonnet, haiku, nous." ;;
esac

# STATUS CHECK
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

# UNINSTALL
if [ "$DO_UNINSTALL" = true ]; then
    say "Uninstalling Understanding Agent Global Hooks..."
    CURRENT_HOOKS="$(git config --global --get core.hooksPath 2>/dev/null || true)"
    if [ "$CURRENT_HOOKS" = "$HOOKS_DIR" ]; then
        git config --global --unset core.hooksPath
        ok "Removed global core.hooksPath configuration"
    fi
    rm -rf "$HOOKS_DIR"
    ok "Removed global hooks directory ($HOOKS_DIR)"
    rm -rf "$VENV_DIR"
    rm -f "$BIN_DIR/understanding-agent"
    ok "Removed installed package ($VENV_DIR)"
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
        err "No branch given. The branch in the curl URL is not visible to a piped script, so pass it through. Example: REF=dev curl -fsSL \"https://raw.githubusercontent.com/Lizasatasiya/understanding-agentHook/\${REF}/install.sh\" | REF=\"\$REF\" bash -s -- --key \"YOUR_API_KEY\" --model haiku"
    fi
    case "$ARG_REF" in
        *[!A-Za-z0-9._/-]*|-*|*/.*|*..*) err "Unsupported branch name '$ARG_REF'." ;;
    esac
    ok "Installing directly from GitHub: $REPO_URL @ $ARG_REF"
    "$VENV_DIR/bin/pip" install --upgrade --force-reinstall --quiet "git+${REPO_URL}@${ARG_REF}"
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

upsert_env() {
    local key="$1" value="$2"
    if grep -q "^${key}=" "$ENV_FILE"; then
        # '|' is safe: model names have none, and keys are written with a quoted replacement.
        local escaped
        escaped="$(printf '%s' "$value" | sed 's/[|&\\]/\\&/g')"
        sed -i.bak "s|^${key}=.*|${key}=${escaped}|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
    else
        printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

if [ -n "$ARG_KEY" ]; then
    upsert_env "UNDERSTANDING_AGENT_API_KEY" "$ARG_KEY"
    ok "Saved API key to $ENV_FILE"
fi

upsert_env "UNDERSTANDING_AGENT_MODEL" "$ARG_MODEL"
ok "Selected model: $ARG_MODEL (questions and answer checks)"

# If file is empty, write a friendly template with notes
if [ ! -s "$ENV_FILE" ]; then
    cat > "$ENV_FILE" <<'EOF'
# Understanding Agent Global Configuration
# Optional: If your team uses a central relay server or shared key, configure it here:
# UNDERSTANDING_AGENT_API_KEY=
# UNDERSTANDING_AGENT_MODEL=nous
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
