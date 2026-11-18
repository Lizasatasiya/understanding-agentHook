import sys

# ----------------------------------------------------------------------------
# Interpreter self-heal (runs BEFORE any version-sensitive import).
# The package needs Python >= 3.10, but stale shells and clean-PATH contexts
# still resolve `python3` to Apple's 3.9. Rather than crashing with a cryptic
# PEP-604 TypeError, when this module is invoked DIRECTLY (python -m
# understanding_agent.cli) and the interpreter is too old, re-exec the same
# command under a modern interpreter found in known locations. Library-style
# imports (import understanding_agent.cli) are NOT hijacked — they keep the
# honest import error.
# ----------------------------------------------------------------------------
if __name__ == "__main__" and sys.version_info < (3, 10):
    import os as _os
    import subprocess as _sp

    def _modern_python():
        import glob as _glob
        candidates = [
            _os.environ.get("VIRTUAL_ENV", "") + "/bin/python",
            _os.path.join(_os.getcwd(), "venv", "bin", "python"),
            _os.path.join(_os.getcwd(), ".venv", "bin", "python"),
            "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            _os.path.expanduser("~/.local/bin/python3"),
            "/usr/local/bin/managed_python3",
            _os.path.expanduser("~/.pyenv/shims/python3"),
        ] + _glob.glob(_os.path.expanduser("~/.local/share/uv/python/*/bin/python3"))

        for cand in candidates:
            if not cand or not (_os.path.isfile(cand) and _os.access(cand, _os.X_OK)):
                continue
            try:
                r = _sp.run([cand, "-c",
                             "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
                            capture_output=True)
                if r.returncode == 0:
                    return cand
            except Exception:
                continue
        return None

    _cand = _modern_python()
    if _cand:
        _env = dict(_os.environ)
        _env["PYTHONPATH"] = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))) \
            + (_os.pathsep + _env["PYTHONPATH"] if _env.get("PYTHONPATH") else "")
        _os.execve(_cand, [_cand, "-m", "understanding_agent.core.cli"] + sys.argv[1:], _env)
    sys.exit(
        "understanding-agent needs Python >= 3.10 (this interpreter is "
        f"{sys.version_info[0]}.{sys.version_info[1]}). Install one once:\n"
        "  uv python install 3.12 --default    # then open a NEW terminal"
    )

import json
import os
import hashlib
from .identity import IdentityDetector
from .defense_client import DefenseClient, DefenseRejected, DefenseUnavailable

from ..context.environment_detector import EnvironmentDetector
from ..context.change_detector import ChangeDetector
from ..context.stack_detector import StackDetector
from ..context.code_graph import CodeGraph
from ..context.context_builder import ContextBuilder
from ..context.evidence_collector import collect_evidence, evidence_question_hint
from ..context.change_kind import classify_change
from ..context.formatting_detector import is_commit_formatting_only
from ..context.commit_context import (
    get_head_commit,
    get_current_commit_message,
)

from ..questions.practice_packs import practice_pack_hint

from ..answers.interaction import Interaction
from ..answers.answer_hygiene import classify_answer, hygiene_enabled

from ..evaluation.coding_standards import CodingStandardsChecker
from ..evaluation.security_lens import SecurityLens, security_question_hint, security_gate_enabled

# Phase 9 (PLAN_V1_COMPLETION.md): stage helpers live in hook_stages.py now;
# these aliases keep any external patch targets (cli._print_gate_block, ...)
# working unchanged.
from .hook_stages import (
    _interactive_input_available,
    _print_gate_block,
    _launch_gui_terminal_defense,
    _detect_agent_execution,
    _print_agent_block,
    _detect_no_verify,
    _print_no_verify_block,
    _is_prepare_stage,
    consume_pass_marker,
    mark_verified_for_commit,
    GitIndexLock,
)

# The server decides pass or stop. This constant is only the local display threshold.
PASSING_SCORE_THRESHOLD = int(os.environ.get("UNDERSTANDING_AGENT_PASSING_SCORE", "75"))


def _json_ready(value):
    """Drop values the defense payload cannot encode."""
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return {}


def _block(message: str) -> None:
    print(f"\n\033[1m\033[91m{message}\033[0m\n")


def main():
    no_verify_early = _detect_no_verify()
    agent_mode_early = _detect_agent_execution()[0]
    if not agent_mode_early and not no_verify_early:
        try:
            con = 'CON' if sys.platform == 'win32' else '/dev/tty'
            sys.stdin = open(con, 'r', encoding='utf-8')
            sys.stdout = open(con, 'w', encoding='utf-8')
        except Exception:
            pass
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    env = EnvironmentDetector().detect()
    changes = ChangeDetector().detect()
    project_root = env.get("project_root") or os.getcwd()

    # Live credentials never leave the machine, and they never open an interview.
    if security_gate_enabled():
        critical = SecurityLens().find_critical_findings({"structured_changes": []})
        if critical:
            _print_gate_block(critical, env, project_root)
            sys.exit(1)

    if not changes.get("files"):
        sys.exit(0)

    head_commit = get_head_commit(project_root)
    commit_msg = get_current_commit_message(project_root)
    diff_hash = hashlib.sha256(json.dumps(changes, sort_keys=True, default=str).encode()).hexdigest()

    # prepare-commit-msg only confirms the interview that just passed. It does
    # not start another one, and the next git commit always starts fresh.
    if _is_prepare_stage():
        if consume_pass_marker(project_root, head_commit, diff_hash):
            sys.exit(0)
        _print_no_verify_block(commit_msg)
        sys.exit(1)

    stack = StackDetector().detect(project_root)
    graph = CodeGraph()
    graph.build(changes.get("files") or [])
    context = ContextBuilder().build(changes, graph)
    change_kind = classify_change(changes, context)
    context["change_kind"] = change_kind

    lens = SecurityLens()
    security_profile = lens.profile_change(context)
    security_hint = security_question_hint(security_profile)
    evidence = collect_evidence(changes.get("files", []), project_root)
    evidence_hint = evidence_question_hint(evidence)
    practice_hint = practice_pack_hint(stack, context, security_hint)
    identity = IdentityDetector.resolve(project_root)

    if security_gate_enabled():
        critical = lens.find_critical_findings(context)
        if critical:
            _print_gate_block(critical, env, project_root)
            sys.exit(1)

    standards_checker = CodingStandardsChecker(project_root)
    standards_report = standards_checker.check(changes, context)
    standards_checker.print_report(standards_report)

    evidence_findings = (evidence.get("semgrep") or []) + (evidence.get("gitleaks") or [])
    if evidence_findings:
        print("\n\033[93mTool Evidence (advisory — informs the questions below):\033[0m")
        for finding in evidence_findings[:5]:
            loc = f"{finding.get('path', '?')}:{finding.get('line', 0)}" if finding.get("path") else "?"
            print(f"  \033[93m[{finding.get('rule', 'finding')}] {loc} "
                  f"{(finding.get('message') or '')[:120]}\033[0m")
        print()

    if change_kind.get("dominant"):
        weights = change_kind.get("weights") or {}
        strong = ", ".join(
            f"{name} {weights[name]:.0%}"
            for name in ("ui", "data_model", "control_flow", "infra_config", "tests", "docs")
            if weights.get(name, 0) >= 0.25 and name != change_kind["dominant"]
        )
        print(
            f"\033[96mChange classification: {change_kind['dominant']}"
            + (f" (mixed: {strong})" if strong else "")
            + f" — signals: {'; '.join(change_kind['signals'][:4])}\033[0m"
        )

    hints = {
        "stack": stack,
        "security": security_hint,
        "evidence": evidence_hint,
        "practices": practice_hint,
        "standards": standards_report,
    }
    failed_standards = [item for item in standards_report if not item.get("is_good", True)]

    is_no_verify = _detect_no_verify(project_root, head_commit, diff_hash)
    if is_no_verify:
        _print_no_verify_block(commit_msg)
        sys.exit(1)

    is_agent, agent_name = _detect_agent_execution()
    if is_agent:
        _print_agent_block(agent_name, commit_msg)
        sys.exit(1)

    if not _interactive_input_available() and not os.environ.get("UNDERSTANDING_AGENT_DELEGATED_TERMINAL"):
        if sys.platform == "darwin":
            launched_ok, exit_code = _launch_gui_terminal_defense(project_root)
            if launched_ok:
                if exit_code == 0:
                    mark_verified_for_commit(project_root, head_commit, diff_hash)
                sys.exit(exit_code)
        print("\n\033[1m\033[91m✗ Interactive oral defense required:\033[0m")
        print("  Cannot conduct code understanding defense from GUI without an interactive terminal.")
        print("  Please run your commit from the integrated terminal:")
        print("    \033[1mgit commit\033[0m\n")
        sys.exit(1)

    if failed_standards:
        print(f"\033[1m\033[93mCoding standards violations detected ({len(failed_standards)} failed).\033[0m")
        print("\033[1mDo you want to fix these violations or proceed further?\033[0m\n")
        print("  \033[1m[1]\033[0m \033[91mFix violations\033[0m")
        print("  \033[1m[2]\033[0m \033[92mProceed further\033[0m\n")
        choice = ""
        while choice not in ("1", "2", "fix", "proceed", "f", "p"):
            try:
                sys.stdout.write("\033[1m\033[96mSelect an option [1/2]: \033[0m")
                sys.stdout.flush()
                raw_choice = sys.stdin.readline()
                if not raw_choice:
                    _block("Commit aborted.")
                    sys.exit(1)
                choice = raw_choice.strip().lower()
            except (KeyboardInterrupt, EOFError):
                _block("Commit aborted.")
                sys.exit(1)
        if choice in ("1", "fix", "f"):
            print("\n\033[91mPlease resolve the violations listed above before committing.\033[0m\n")
            sys.exit(1)
        print("\n\033[1m\033[92m✓ Proceeding with questions...\033[0m\n")

    git_name = (identity.get("git_name") or "").strip()
    git_email = (identity.get("git_email") or "").strip().lower()
    if not git_name or "@" not in git_email:
        _block("Commit aborted. This commit has no git author name and email.")
        print("  Set them with `git config --global user.name` and `git config --global user.email`, then commit again.\n")
        sys.exit(1)

    client = DefenseClient()
    try:
        started = client.start({
            "git_name": git_name,
            "git_email": git_email,
            "repository": env.get("repository") or identity.get("repo_slug") or "unknown",
            "branch": env.get("branch") or "unknown",
            "head_commit": head_commit,
            "commit_message": commit_msg,
            "diff_hash": diff_hash,
            "trivial": bool(is_commit_formatting_only(changes, project_root)),
            "standards_failed": bool(failed_standards),
            "context": _json_ready(context),
            "hints": _json_ready(hints),
        })
    except DefenseUnavailable as exc:
        _block("Commit aborted. The defense service is unavailable.")
        print(f"  {exc}\n")
        sys.exit(1)
    except DefenseRejected as exc:
        _block("Commit aborted. The defense service rejected this interview.")
        print(f"  {exc}\n")
        sys.exit(1)

    if started.get("action") == "skip":
        message = started.get("message") or "No oral defense required — commit allowed."
        print(f"\n\033[92m✓ {message}\033[0m\n")
        mark_verified_for_commit(project_root, head_commit, diff_hash)
        sys.exit(0)

    questions = started.get("questions") or []
    defense_id = started.get("defense_id") or ""
    if not defense_id or not questions:
        _block("Commit aborted. The defense service did not return an interview.")
        sys.exit(1)

    interaction = Interaction()
    index_lock = GitIndexLock(project_root)
    index_lock.acquire()
    try:
        print(f"\n\n\033[1m\033[96mQuestions\033[0m\n")
        for index, question in enumerate(questions, 1):
            question_id = question.get("question_id") or f"q{index}"
            question_text = question.get("question") or ""
            time_limit = int(question.get("time_limit") or 60)
            print(f"\033[1m\033[96mQuestion {index}/{len(questions)}\033[0m")
            print(f"\033[93mTime Limit: {time_limit} seconds\033[0m\n")
            print(f"\033[1m{question_text}\033[0m\n")

            answer_text = interaction.timed_input(f"\033[96m❯ \033[0m", time_limit, speak_text=question_text)
            if answer_text is None:
                answer_text = ""
                print("\n\033[91m✗ Timeout reached\033[0m")
            else:
                print("\n\033[92m✓ Answer received\033[0m")
                if hygiene_enabled():
                    suspect, reason = classify_answer(answer_text)
                    if suspect:
                        print(f"\033[93m⚠ That answer may not have come through cleanly ({reason}).\033[0m")
                        retry_text = interaction.timed_input(
                            "\033[96mSay it again or type it (Enter submits the text above as-is) ❯ \033[0m",
                            min(time_limit, 45),
                        )
                        if retry_text and retry_text.strip():
                            answer_text = retry_text

            try:
                result = client.answer(defense_id, question_id, answer_text)
            except DefenseUnavailable as exc:
                _block("Commit aborted. The defense service is unavailable.")
                print(f"  {exc}\n")
                sys.exit(1)
            except DefenseRejected as exc:
                _block("Commit aborted. The defense service rejected this answer.")
                print(f"  {exc}\n")
                sys.exit(1)

            action = result.get("action")
            if action == "stop":
                print("\n\033[91m✗ Understanding not demonstrated.\033[0m")
                _block("Commit aborted. Please review your code and try again.")
                sys.exit(1)
            if action == "pass":
                print("\033[92m✓ Good understanding demonstrated.\033[0m\n")
                print("\n\033[92mCommit allowed: Understanding demonstrated.\033[0m")
                mark_verified_for_commit(project_root, head_commit, diff_hash)
                sys.exit(0)
            if action != "continue":
                _block("Commit aborted. The defense service returned an unknown result.")
                sys.exit(1)
            print("\033[92m✓ Good understanding demonstrated.\033[0m\n")

        _block("Commit aborted. The defense service did not finish the interview.")
        sys.exit(1)
    finally:
        index_lock.release()


if __name__ == "__main__":
    main()


