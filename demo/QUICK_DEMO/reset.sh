#!/bin/sh
# Reset the shopcart demo to its demo-ready state:
# base commit checked out + the "AI-generated" change STAGED, hook installed.
set -e
cd "$(dirname "$0")/shopcart"
git reset --hard -q HEAD
git clean -fdq
git apply --index --whitespace=nowarn ../staged.patch
[ -f .env ] || { cp /Users/yash.c/understanding-agentHook/.env .env; chmod 600 .env; }
~/.local/bin/pre-commit install >/dev/null 2>&1 || true
echo "Demo state ready — staged files:"
git status --short