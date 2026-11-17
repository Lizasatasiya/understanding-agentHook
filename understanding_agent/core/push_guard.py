"""Push guard (Judas — install enforcement aid): a pre-push hook that gives
the developer a LOUD second chance when the pre-commit hook is not actually
installed. Complements the ledger check: CI catches uninstalled hooks after
the push; this catches them before it.

Checks (deterministic, fast, zero network):
  1. running inside a git repository
  2. a pre-commit hook is installed where git will look for it:
     core.hooksPath if configured, else .git/hooks/pre-commit
  3. the installed hook mentions the pre-commit framework OR our entrypoint
     (understanding-agent) — anything else is likely a different tool

Exit 0 with a green one-liner when healthy; exit 1 with exact remediation
steps when not. NEVER blocks based on session contents — installation state
only.
"""

import os
import subprocess
import sys


def _git(args, cwd=None):
    return subprocess.run(["git"] + args, cwd=cwd or ".",
                          capture_output=True, text=True)


def precommit_hook_status(repo_root: str = ".") -> dict:
    """{healthy: bool, hook_path: str, reason: str} — testable core."""
    if not os.path.isdir(os.path.join(repo_root, ".git")):
        return {"healthy": False, "hook_path": "",
                "reason": "not a git repository"}

    r = _git(["config", "--get", "core.hooksPath"], cwd=repo_root)
    hooks_dir = (r.stdout.strip() if r.returncode == 0
                 else os.path.join(repo_root, ".git", "hooks"))
    hook_path = os.path.join(hooks_dir, "pre-commit")

    if not os.path.exists(hook_path):
        return {"healthy": False, "hook_path": hook_path,
                "reason": "no pre-commit hook installed"}

    try:
        with open(hook_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return {"healthy": False, "hook_path": hook_path,
                "reason": f"pre-commit hook unreadable at {hook_path}"}

    if "pre-commit" not in content and "understanding-agent" not in content:
        return {"healthy": False, "hook_path": hook_path,
                "reason": f"a pre-commit hook exists at {hook_path} but does not "
                          f"reference pre-commit or understanding-agent — verify "
                          f"it is the understanding hook"}
    return {"healthy": True, "hook_path": hook_path, "reason": ""}


def main() -> int:
    status = precommit_hook_status()
    if status["healthy"]:
        print(f"✓ understanding-agent push guard: pre-commit hook installed "
              f"({status['hook_path']})")
        return 0

    print("\033[93m⚠ PUSH GUARD: the understanding pre-commit hook is NOT "
          "installed in this repository.\033[0m")
    print(f"  Reason: {status['reason']}")
    print("  Fix before pushing (commits made without the hook are unaudited "
          "and will fail CI's proof-of-defense check):")
    print("    1. cd <this repo> && pre-commit install")
    print("    2. if you use a custom core.hooksPath, install there")
    print("  To push anyway in an emergency, use: git push --no-verify "
          "(the bypass stays visible in CI).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
