#!/bin/sh
# One-time machine setup for a developer Mac running understanding-agent.
# Idempotent — safe to run again. Nothing here needs sudo.
#
#   sh scripts/setup_dev_machine.sh
#
# Sets up:
#   1. uv (if missing)          — the package manager used for pythons
#   2. python3 = 3.12 via uv    — ~/.local/bin/python3 (ONE python; stale
#                                 terminals: open a new tab after this)
#   3. pre-commit via uv tool   — ~/.local/bin/pre-commit
#
# Then wire each project with:  sh scripts/wire_project.sh /path/to/project
set -e

say() { printf '\033[96m== %s\033[0m\n' "$1"; }

say "1/3 uv"
if command -v uv >/dev/null 2>&1; then
    echo "uv already installed: $(uv --version)"
else
    echo "installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1 || { echo "uv install failed — add ~/.local/bin to PATH and re-run"; exit 1; }
fi

say "2/3 python3 (one modern python, no sudo)"
# --default puts python/python3 symlinks in ~/.local/bin, which the default
# zshrc PATH block picks up. The hook CLI also self-heals if an old python
# runs it anyway.
PYVER="$(~/.local/bin/python3 --version 2>/dev/null || /usr/bin/python3 --version)"
echo "current python3: $PYVER"
case "$PYVER" in
    Python\ 3.1[2-9].*) echo "python3 already >= 3.12 — ok" ;;
    *) uv python install 3.12 --default ;;
esac
~/.local/bin/python3 --version

say "3/3 pre-commit"
if command -v pre-commit >/dev/null 2>&1; then
    echo "pre-commit already installed: $(pre-commit --version)"
else
    uv tool install pre-commit
    ~/.local/bin/pre-commit --version
fi

echo
printf '\033[92mMachine ready.\033[0m Open a NEW terminal (PATH lookups are cached per session), then:\n'
echo "  sh scripts/wire_project.sh /path/to/your/project [--env /path/to/.env]"
