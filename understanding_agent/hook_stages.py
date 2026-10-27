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


_AGENT_ENV_SIGNATURES = (
    ("ANTIGRAVITY_AGENT", "Antigravity Agent"),
    ("ANTIGRAVITY_SOURCE_METADATA", "Antigravity Agent"),
    ("ANTIGRAVITY_CONVERSATION_ID", "Antigravity Agent"),
    ("ANTIGRAVITY_TRAJECTORY_ID", "Antigravity Agent"),
    ("CURSOR_AGENT", "Cursor Agent"),
    ("CURSOR_TRACE_ID", "Cursor Agent"),
    ("CURSOR_COMPOSER", "Cursor Composer"),
    ("CURSOR_EXECUTION_ID", "Cursor Agent"),
    ("CLAUDE_CODE", "Claude Code"),
    ("CLAUDE_CLI", "Claude Code"),
    ("CLAUDE_AGENT", "Claude Code"),
    ("CLINE_ACTIVE", "Cline Agent"),
    ("ROO_CODE", "Roo Code Agent"),
    ("COPILOT_AGENT", "GitHub Copilot Agent"),
    ("GITHUB_COPILOT", "GitHub Copilot Agent"),
    ("AIDER", "Aider AI"),
    ("AI_AGENT", "AI Agent"),
    ("AGENT_MODE", "AI Agent"),
    ("AUTONOMOUS_AGENT", "Autonomous Agent"),
)

_AGENT_PROCESS_KEYWORDS = (
    ("language_server_macos", "Antigravity Agent"),
    ("antigravity-agent", "Antigravity Agent"),
    ("antigravity_agent", "Antigravity Agent"),
    ("cursor-agent", "Cursor Agent"),
    ("cursor_agent", "Cursor Agent"),
    ("claude", "Claude Code"),
    ("cline", "Cline Agent"),
    ("copilot-agent", "GitHub Copilot Agent"),
    ("aider", "Aider AI"),
)


def _check_agent_env() -> tuple[bool, str]:
    """Check explicit environment variables for agent runtime signatures."""
    import os

    mode = os.environ.get("UNDERSTANDING_AGENT_MODE", "").strip().lower()
    if mode == "agent":
        return True, "Autonomous Agent (UNDERSTANDING_AGENT_MODE=agent)"

    for env_var, label in _AGENT_ENV_SIGNATURES:
        val = os.environ.get(env_var)
        if val is not None and val.strip().lower() not in ("0", "false", "no"):
            return True, label

    return False, ""


def _match_agent_keyword(comm: str) -> tuple[bool, str]:
    """Match a process command path against known agent keywords."""
    for kw, label in _AGENT_PROCESS_KEYWORDS:
        if kw in comm:
            return True, label
    return False, ""


def _check_agent_process() -> tuple[bool, str]:
    """Inspect ancestor process hierarchy up to 6 levels for agent processes."""
    import os
    import subprocess

    try:
        curr_pid = os.getppid()
        for _ in range(6):
            if curr_pid <= 1:
                break
            out = subprocess.check_output(
                ["ps", "-o", "ppid,comm", "-p", str(curr_pid)],
                text=True, stderr=subprocess.DEVNULL
            ).strip().splitlines()
            if len(out) < 2:
                break

            parts = out[1].strip().split(None, 1)
            comm_lower = (parts[1] if len(parts) > 1 else "").lower()

            # Skip IDE GUI editor and terminal helper processes
            if "electron" in comm_lower or "helper" in comm_lower:
                curr_pid = int(parts[0])
                continue

            matched, label = _match_agent_keyword(comm_lower)
            if matched:
                return True, label

            curr_pid = int(parts[0])
    except Exception:
        pass

    return False, ""


def _detect_agent_execution() -> tuple[bool, str]:
    """Detect if the current commit is being executed by an autonomous AI agent."""
    import os

    # Delegated interactive terminal launched for human defense is exempt
    if os.environ.get("UNDERSTANDING_AGENT_DELEGATED_TERMINAL") == "1":
        return False, ""

    is_agent, label = _check_agent_env()
    if is_agent:
        return True, label

    return _check_agent_process()


def _detect_no_verify(project_root: str = None, head_commit: str = None, diff_hash: str = None) -> bool:
    """Detect if the commit attempt is trying to bypass pre-commit via --no-verify or -n."""
    import os
    import sys
    import subprocess
    import shlex
    import re

    # 1. Direct explicit environment flag
    if os.environ.get("UNDERSTANDING_AGENT_NO_VERIFY", "").strip().lower() in ("1", "true", "yes"):
        return True

    # 2. Inspect ancestor git commit command line
    try:
        from .commit_context import find_git_commit_ancestor
        _, args = find_git_commit_ancestor()
        if args:
            is_commit = False
            for i, a in enumerate(args):
                low = a.lower()
                if (low == "git" or low.endswith("/git")) and i + 1 < len(args) and args[i + 1].lower() == "commit":
                    is_commit = True
                    continue
                if is_commit:
                    if a == "--no-verify":
                        return True
                    if a == "-n":
                        return True
                    if a.startswith("-") and not a.startswith("--") and "n" in a:
                        return True
    except Exception:
        pass

    # 3. Process ancestry inspection via ps
    try:
        curr_pid = os.getpid()
        for _ in range(8):
            if curr_pid <= 1:
                break
            out = subprocess.check_output(
                ["ps", "-o", "ppid,args", "-p", str(curr_pid)],
                text=True, stderr=subprocess.DEVNULL
            ).strip().splitlines()
            if len(out) < 2:
                break
            parts = out[1].strip().split(None, 1)
            ppid = int(parts[0])
            cmd_line = parts[1] if len(parts) > 1 else ""

            if "git" in cmd_line and "commit" in cmd_line:
                if "--no-verify" in cmd_line:
                    return True
                try:
                    tokens = shlex.split(cmd_line)
                except Exception:
                    tokens = cmd_line.split()
                in_commit = False
                for t in tokens:
                    if t == "commit":
                        in_commit = True
                        continue
                    if in_commit:
                        if t in ("--no-verify", "-n"):
                            return True
                        if re.match(r"^-[a-zA-Z]*n[a-zA-Z]*$", t):
                            return True
            curr_pid = ppid
    except Exception:
        pass

    # 4. Check if prepare-commit-msg hook was reached without prior pre-commit verification
    if project_root and head_commit and diff_hash:
        is_prepare_stage = any("COMMIT_EDITMSG" in a for a in sys.argv) or \
                           os.environ.get("PRE_COMMIT_HOOK_TYPE") == "prepare-commit-msg"
        if is_prepare_stage:
            if not is_already_verified_for_commit(project_root, head_commit, diff_hash):
                return True

    return False


def _print_no_verify_block(commit_msg: str) -> None:
    RED = "\033[91m"
    BOLD = "\033[1m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RESET = "\033[0m"

    msg = commit_msg.strip() if commit_msg else "<commit message>"
    print(f"{BOLD}{RED}  ✗ COMMIT BLOCKED: --no-verify is Prohibited               {RESET}\n")
    print(f"  Try to commit with:")
    print(f"    {BOLD}{CYAN}git commit -m \"{msg}\"{RESET}\n")
    


def _print_agent_block(agent_name: str, commit_msg: str) -> None:
    RED = "\033[91m"
    BOLD = "\033[1m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RESET = "\033[0m"

    msg = commit_msg.strip() if commit_msg else "<commit message>"
    print(f"\n{BOLD}{RED}============================================================{RESET}")
    print(f"{BOLD}{RED}  ✗ COMMIT BLOCKED: Autonomous AI Agent Commits Prohibited  {RESET}")
    print(f"{BOLD}{RED}============================================================{RESET}\n")
    print(f"  {BOLD}Detected Agent:{RESET} {CYAN}{agent_name}{RESET}")
    print(f"  {YELLOW}The Code Understanding Agent requires a human oral defense to{RESET}")
    print(f"  {YELLOW}verify comprehension before changes can be committed to git.{RESET}")
    print(f"  {YELLOW}AI agents are NOT permitted to commit code on behalf of developers.{RESET}\n")
    print(f"{BOLD}{YELLOW}👉 Action Required:{RESET}")
    print(f"  A human developer must run the commit directly in their terminal:")
    print(f"    {BOLD}{CYAN}git commit -m \"{msg}\"{RESET}\n")
    print(f"{BOLD}{RED}============================================================{RESET}\n")
   



def _cleanup_temp_files(paths: list) -> None:
    import os
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def _read_int_file(path: str):
    """Read an integer from file, returning None on error or missing."""
    import os
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            raw = f.read().strip()
            return int(raw) if raw else None
    except Exception:
        return None


def _is_pid_alive(pid: int) -> bool:
    """Check if process is still running."""
    import os
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _write_gui_defense_runner(project_root: str) -> tuple[str, str, str]:
    """Create the executable bash runner script for delegated terminal defense."""
    import os
    import sys
    import tempfile

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
unset ANTIGRAVITY_AGENT
unset ANTIGRAVITY_SOURCE_METADATA
unset ANTIGRAVITY_CONVERSATION_ID
unset ANTIGRAVITY_TRAJECTORY_ID
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
    return runner_path, pid_file, status_file


def _poll_gui_defense_result(pid_file: str, status_file: str, timeout: int = 600) -> int:
    """Wait for runner script to finish and return exit code."""
    import time

    start_time = time.time()
    pid = None

    while time.time() - start_time < 5:
        pid = _read_int_file(pid_file)
        if pid is not None:
            break
        time.sleep(0.2)

    while time.time() - start_time < timeout:
        code = _read_int_file(status_file)
        if code is not None:
            return code

        if pid is not None and not _is_pid_alive(pid):
            time.sleep(0.5)
            final_code = _read_int_file(status_file)
            return final_code if final_code is not None else 1

        time.sleep(0.3)

    return 1


def _launch_gui_terminal_defense(project_root: str) -> tuple[bool, int]:
    """Launch an interactive macOS Terminal window to conduct the oral defense when committed via GUI."""
    if sys.platform != "darwin":
        return False, 1

    import shutil
    import subprocess

    if not shutil.which("osascript"):
        return False, 1

    runner_path, pid_file, status_file = _write_gui_defense_runner(project_root)

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

    exit_code = _poll_gui_defense_result(pid_file, status_file)
    _cleanup_temp_files([runner_path, pid_file, status_file])
    return True, exit_code


def is_already_verified_for_commit(project_root: str, head_commit: str, diff_hash: str) -> bool:
    """Check if the current staged diff was already defended and passed in pre-commit.
    
    Prevents prepare-commit-msg from asking questions again when a normal (verified)
    commit is in progress. Returns False if pre-commit was skipped (e.g. via --no-verify).
    """
    import os
    import json
    import time

    state_file = os.path.join(project_root, ".git", "understanding_agent_verified.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                data = json.load(f)
                if (data.get("head_commit") == head_commit and
                    data.get("diff_hash") == diff_hash and
                    time.time() - data.get("timestamp", 0) < 120):
                    return True
        except Exception:
            pass
    return False


def mark_verified_for_commit(project_root: str, head_commit: str, diff_hash: str) -> None:
    """Mark the current staged diff as verified in pre-commit."""
    import os
    import json
    import time

    state_file = os.path.join(project_root, ".git", "understanding_agent_verified.json")
    try:
        with open(state_file, "w") as f:
            json.dump({
                "head_commit": head_commit,
                "diff_hash": diff_hash,
                "timestamp": time.time(),
            }, f)
    except Exception:
        pass


def clear_verified_for_commit(project_root: str) -> None:
    """Clean up verified diff state after a commit is finalized."""
    import os
    state_file = os.path.join(project_root, ".git", "understanding_agent_verified.json")
    try:
        if os.path.exists(state_file):
            os.remove(state_file)
    except Exception:
        pass


