"""Hook stage helpers (phase 9, PLAN_V1_COMPLETION.md — cli decomposition).

Presentation/prompting stages extracted from cli.py so main() keeps only
orchestration: the credential-gate block printer, the advisory-findings
printer, the interactive-input probe, and the gitignore helper. Behavior is
byte-identical — this is a mechanical move, not a redesign; cli.py imports
these names and any external patch targets keep working through the aliases.
"""

import subprocess
import sys


def _is_gitignored(repo_root: str, path: str) -> bool:
    """True when the given path matches a .gitignore rule (uses check-ignore)."""
    try:
        code = subprocess.call(
            ["git", "check-ignore", "-q", path],
            cwd=repo_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return code == 0
    except Exception:
        return False


def _interactive_input_available() -> bool:
    """Judas J2: can a human actually answer questions here?

    True when stdin is a TTY, or /dev/tty (CON on Windows) is openable —
    interaction.timed_input falls back to /dev/tty, so a piped stdin in a
    real terminal is still interactive. False means no interactive answer is
    possible (agents, CI, cron) and the hook must record-and-pass rather
    than abort a defense nobody can perform.
    """
    try:
        if sys.stdin.isatty():
            return True
    except Exception:
        pass
    try:
        con = 'CON' if sys.platform == 'win32' else '/dev/tty'
        with open(con, 'r'):
            return True
    except Exception:
        return False


def _print_advisory_findings(findings: list) -> None:
    """Print the master-analysis LLM review block.

    Advisory only — these findings cannot block anything; they inform the
    questions that follow. Severity-sorted, citation-bearing.
    """
    if not findings:
        return
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
    print(f"\n{BOLD}{YELLOW}LLM Review (advisory — informs the questions below){RESET}")
    for f in findings[:3]:
        loc = f"{f['file']}:{f['line']}" if f.get("line") else f.get("file", "?")
        print(f"  {YELLOW}[{f.get('category', 'note')} sev {f.get('severity', '?')}] "
              f"{f.get('description', '')} ({loc}){RESET}")
    print()


def _print_gate_block(critical: list, env: dict, project_root: str) -> None:
    """Print the credential-gate block message and unstage hints."""
    print(f"\n\033[91mCommit blocked: credential detected in staged code.\033[0m\n")
    for f in critical:
        print(f"  \033[91m✗ {f['description']} ({f['redacted']}) in {f['file']}"
              + (f" :: {f['function']}()" if f["function"] else "") + "\033[0m")
    # B6: actionable unstage suggestion per finding
    unstaged_files = sorted({f["file"] for f in critical if f.get("file")})
    if unstaged_files:
        print("\n\033[93m  Unstage the offending file(s) to abort this commit path:\033[0m")
        for path in unstaged_files:
            print(f"    git restore --staged {path}")
    # B6: call out staged-but-gitignored files (the exact mistake)
    ignored = [p for p in unstaged_files if _is_gitignored(project_root, p)]
    if ignored:
        print(f"\n\033[93m  Note: {', '.join(ignored)} is listed in .gitignore — it was staged "
              "explicitly (e.g. `git add -f` or `git add .` after editing).\033[0m")
    print("""
\033[93m  Remove the credential before committing:\033[0m
  1. Move it to an environment variable or your .env (already gitignored)
  2. If it was ever committed before, rotate it — history keeps it forever
  3. If this is a false positive, set UNDERSTANDING_AGENT_SECURITY_GATE=off
     for this commit: git commit  # after unsetting, or use --no-verify
""")
