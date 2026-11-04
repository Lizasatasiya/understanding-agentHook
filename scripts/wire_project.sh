#!/bin/sh
# Wire a target project repo to run the understanding-agent pre-commit hook.
#
#   sh scripts/wire_project.sh /path/to/your/project [--env /path/to/.env]
#
# What it does in the TARGET project:
#   1. writes .pre-commit-config.yaml pointing at the hook repo (rev: main;
#      pin a release tag like v0.4.0 for real adoption — see docs/ONBOARDING.md)
#   2. .env with the LLM key: copied from --env if given, else scaffolded
#      with a template you must fill (get the key from the team vault)
#   3. installs the pre-commit hook into the project (.git/hooks/pre-commit)
#   4. sanity-checks git identity (needed for session attribution)
#
# Then: stage a change and `git commit` — the defense fires.
set -e

say() { printf '\033[96m== %s\033[0m\n' "$1"; }
die() { printf '\033[91mERROR: %s\033[0m\n' "$1" >&2; exit 1; }

PROJECT=""
ENV_SRC=""
while [ $# -gt 0 ]; do
    case "$1" in
        --env) ENV_SRC="$2"; shift 2 ;;
        *) PROJECT="$1"; shift ;;
    esac
done
[ -n "$PROJECT" ] || die "usage: sh scripts/wire_project.sh /path/to/project [--env /path/to/.env]"
[ -d "$PROJECT/.git" ] || die "$PROJECT is not a git repository"
cd "$PROJECT"

HOOK_REPO="$(cd "$(dirname "$0")/.." && pwd)"
PC="$HOME/.local/bin/pre-commit"
[ -x "$PC" ] || die "pre-commit not found — run scripts/setup_dev_machine.sh first"

say "writing .pre-commit-config.yaml (hook repo: $HOOK_REPO)"
cat > .pre-commit-config.yaml <<YML
repos:
-   repo: $HOOK_REPO
    rev: main
    hooks:
    -   id: understanding-agent
YML
# NOTE: a local path repo means the hook updates when the local clone does.
# For team-wide adoption, use the git URL + a pinned tag instead:
#   repo: https://github.com/Lizasatasiya/understanding-agentHook.git
#   rev: v0.4.0

say "provisioning .env"
if [ -n "$ENV_SRC" ]; then
    [ -f "$ENV_SRC" ] || die "--env file not found: $ENV_SRC"
    cp "$ENV_SRC" .env
    chmod 600 .env
    echo "copied .env from $ENV_SRC"
elif [ -f .env ]; then
    echo ".env already present — left untouched"
else
    printf '\033[93mNo project .env created. The hook uses only the key and model in ~/.config/understanding-agent/.env (install.sh --key --model).\033[0m\n'
fi
grep -q "^\.env$" .gitignore 2>/dev/null || echo ".env" >> .gitignore

say "installing the pre-commit hook"
"$PC" install

say "git identity check"
if [ -z "$(git config user.email)" ]; then
    printf '\033[93mNOTE: no git user.email set — run:\033[0m\n'
    printf '  git config user.email "you@company.com"; git config user.name "Your Name"\n'
fi

echo
printf '\033[92mProject wired: %s\033[0m\n' "$PWD"
echo "Next:"
echo "  1. open a NEW terminal (first time only)"
echo "  2. stage a change, then: git commit -m '...'   → the defense fires"
echo "  3. verify with: python3 $HOOK_REPO/scripts/demo_probe.py   (from this project)"
