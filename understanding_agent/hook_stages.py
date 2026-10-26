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


def _cleanup_temp_files(paths: list) -> None:
    import os
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def _launch_gui_terminal_defense(project_root: str) -> tuple[bool, int]:
    """Launch an interactive macOS Terminal window to conduct the oral defense when committed via GUI."""
    if sys.platform != "darwin":
        return False, 1

    import os
    import shutil
    import tempfile
    import time

    if not shutil.which("osascript"):
        return False, 1

    hook_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    python_bin = sys.executable

    with tempfile.NamedTemporaryFile(suffix=".sh", mode="w", delete=False) as f:
        runner_path = f.name
        pid_file = runner_path + ".pid"
        status_file = runner_path + ".status"

        f.write(f"""#!/bin/bash
echo $$ > "{pid_file}"
cd "{project_root}"
export PYTHONPATH="{hook_dir}:$PYTHONPATH"
export UNDERSTANDING_AGENT_DELEGATED_TERMINAL=1

echo ""
echo -e "\\033[1;36m============================================================\\033[0m"
echo -e "\\033[1;36m     CODE UNDERSTANDING AGENT — PRE-COMMIT DEFENSE        \\033[0m"
echo -e "\\033[1;36m============================================================\\033[0m"
echo ""
echo -e "\\033[93mInteractive oral defense launched for IDE Source Control commit.\\033[0m"
echo ""

"{python_bin}" -m understanding_agent.cli
EXIT_CODE=$?
echo $EXIT_CODE > "{status_file}"

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo -e "\\033[1;32m✓ Defense PASSED — Committing changes in IDE...\\033[0m"
    sleep 2
else
    echo ""
    echo -e "\\033[1;31m✗ Defense FAILED — Commit aborted.\\033[0m"
    sleep 3
fi
exit $EXIT_CODE
""")

    os.chmod(runner_path, 0o755)

    apple_script = f'''
    tell application "Terminal"
        activate
        do script "{runner_path}"
    end tell
    '''
    try:
        res = subprocess.run(["osascript", "-e", apple_script], capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            _cleanup_temp_files([runner_path, pid_file, status_file])
            return False, 1
    except Exception:
        _cleanup_temp_files([runner_path, pid_file, status_file])
        return False, 1

    pid = None
    start_time = time.time()
    # Wait up to 5s for runner to start and write pid
    while time.time() - start_time < 5:
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as pf:
                    raw = pf.read().strip()
                    if raw:
                        pid = int(raw)
                        break
            except Exception:
                pass
        time.sleep(0.2)

    # Poll status or process exit for up to 10 minutes
    exit_code = 1
    while time.time() - start_time < 600:
        if os.path.exists(status_file):
            try:
                with open(status_file, "r") as sf:
                    raw = sf.read().strip()
                    if raw:
                        exit_code = int(raw)
                        break
            except Exception:
                pass

        if pid is not None:
            try:
                os.kill(pid, 0)
            except OSError:
                time.sleep(0.5)
                if os.path.exists(status_file):
                    try:
                        with open(status_file, "r") as sf:
                            raw = sf.read().strip()
                            if raw:
                                exit_code = int(raw)
                    except Exception:
                        pass
                break

        time.sleep(0.3)

    _cleanup_temp_files([runner_path, pid_file, status_file])
    return True, exit_code

