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
        _os.execve(_cand, [_cand, "-m", "understanding_agent.cli"] + sys.argv[1:], _env)
    sys.exit(
        "understanding-agent needs Python >= 3.10 (this interpreter is "
        f"{sys.version_info[0]}.{sys.version_info[1]}). Install one once:\n"
        "  uv python install 3.12 --default    # then open a NEW terminal"
    )

import json
import os
import hashlib
from .defense_client import DefenseClient, DefenseRejected, DefenseUnavailable
from .identity import IdentityDetector
from .fail_mode import fail_open_enabled

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
    _is_gitignored,
    _interactive_input_available,
    _print_advisory_findings,
    _print_gate_block,
    _launch_gui_terminal_defense,
    _detect_agent_execution,
    _print_agent_block,
    _detect_no_verify,
    _print_no_verify_block,
    _is_prepare_stage,
    _is_revert_or_merge_operation,
    is_already_verified_for_commit,
    mark_verified_for_commit,
    GitIndexLock,
)


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
    
    # 1. Environment Detection
    env_detector = EnvironmentDetector()
    env = env_detector.detect()
    
    # 2. Change Detection
    change_detector = ChangeDetector()
    changes = change_detector.detect()
    
    project_root = env.get("project_root") or os.getcwd()

    # 2b. Credential gate runs even on "no analyzable files" commits: the
    # gate scans git's staged list directly (key FILES are findings by name
    # alone), so a lone force-added key/credential file must never slip past
    # an early exit. Zero LLM cost.
    if security_gate_enabled():
        lens = SecurityLens()
        critical = lens.find_critical_findings({"structured_changes": []})
        if critical:
            _print_gate_block(critical, env, project_root)
            sys.exit(1)

    if not changes.get("files"):
        sys.exit(0)
    head_commit = get_head_commit(project_root)
    commit_msg = get_current_commit_message(project_root)
    diff_hash = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()

    # Check if pre-commit already verified and defended this exact staged change
    if is_already_verified_for_commit(project_root, head_commit, diff_hash):
        sys.exit(0)

    # If running in prepare-commit-msg stage and NOT verified:
    if _is_prepare_stage():
        # git revert, merge, cherry-pick, rebase intentionally skip pre-commit in Git
        if _is_revert_or_merge_operation(project_root):
            sys.exit(0)
        _print_no_verify_block(commit_msg)
        sys.exit(1)
        
    # 3. Stack Detection (languages, frameworks, domains — keys all downstream hints)
    stack = StackDetector().detect(project_root)

    # 4. Code Graph
    graph = CodeGraph()
    graph.build(changes.get("files") or [])

    # 5. Context Builder
    context_builder = ContextBuilder()
    context = context_builder.build(changes, graph)

    # Bad Romance item 3 — deterministic ChangeKind tier: always-on,
    # offline-safe classification attached to the context as a TYPED field;
    # both question paths consume it (prompts gain a CHANGE CLASSIFICATION
    # section; the master analysis can refine it with its LLM tier).
    change_kind = classify_change(changes, context)
    context["change_kind"] = change_kind

    # 5b. Security lens + evidence collection (deterministic, no LLM, fast)
    lens = SecurityLens()
    security_profile = lens.profile_change(context)
    security_hint = security_question_hint(security_profile)
    evidence = collect_evidence(changes.get("files", []), project_root)
    evidence_hint = evidence_question_hint(evidence)
    practice_hint = practice_pack_hint(stack, context, security_hint)

    # 5c. Security gate FIRST: deterministic critical findings (live
    # credentials) block before any prompt or LLM call. Unlike the oral
    # defense, no answer can make committing a real credential safe, and git
    # history is permanent — so a doomed commit must fail fast, before the
    # developer is asked to sit through the standards report or questions.
    if security_gate_enabled():
        critical = lens.find_critical_findings(context)
        if critical:
            _print_gate_block(critical, env, project_root)
            sys.exit(1)

    # 5d. Coding Standards Audit (deterministic, printed after the gate passes)
    standards_checker = CodingStandardsChecker(project_root)
    standards_report = standards_checker.check(changes, context)
    standards_checker.print_report(standards_report)

    # 5d-2. Tool evidence (deterministic, advisory): semgrep/gitleaks results
    # are displayed and recorded regardless of what any LLM later says —
    # the LLM elaborates this evidence into questions; it cannot suppress
    # or fabricate it (docs/LLM_POLICY.md, "Deterministic evidence").
    evidence_findings = (evidence.get("semgrep") or []) + (evidence.get("gitleaks") or [])
    if evidence_findings:
        print(f"\n\033[93mTool Evidence (advisory — informs the questions below):\033[0m")
        for f in evidence_findings[:5]:
            loc = f"{f.get('path', '?')}:{f.get('line', 0)}" if f.get("path") else "?"
            print(f"  \033[93m[{f.get('rule', 'finding')}] {loc} "
                  f"{(f.get('message') or '')[:120]}\033[0m")
        print()

    # 5d-3. Deterministic change classification (ChangeKind tier): shown so
    # the developer sees the lens the questions will use. Signals listed so
    # the classification is always auditable.
    if change_kind.get("dominant"):
        w = change_kind.get("weights") or {}
        strong = ", ".join(f"{d} {w[d]:.0%}" for d in ("ui", "data_model", "control_flow",
                                                      "infra_config", "tests", "docs")
                           if w.get(d, 0) >= 0.25 and d != change_kind["dominant"])
        print(f"\033[96mChange classification: {change_kind['dominant']}"
              + (f" (mixed: {strong})" if strong else "")
              + f" — signals: {'; '.join(change_kind['signals'][:4])}\033[0m")

    hints = {
        "stack": stack,
        "security": security_hint,
        "evidence": evidence_hint,
        "practices": practice_hint,
        "standards": standards_report,
    }

    failed_standards = [s for s in standards_report if not s.get("is_good", True)]

    # 5d-4. --no-verify Check: Block bypass attempts
    # Git skips pre-commit on --no-verify, but prepare-commit-msg intercepts it.
    is_no_verify = _detect_no_verify(project_root, head_commit, diff_hash)
    if is_no_verify:
        _print_no_verify_block(commit_msg)
        sys.exit(1)

    # 5d-5. Trivial change exemption (formatting, imports, comments, docs, annotations):
    # If all staged changes are purely formatting, import organization, comments,
    # docs, or type annotations and there are no coding standards violations:
    if is_commit_formatting_only(changes, project_root) and not failed_standards:
        print(f"\n\033[92m✓ All staged changes are trivial (formatting, imports, comments, docs, type annotations).\033[0m")
        print(f"\033[92m  No oral defense required — commit allowed.\033[0m\n")
        mark_verified_for_commit(project_root, head_commit, diff_hash)
        sys.exit(0)

    # 5e. Judas J2 — agent / non-interactive record-only mode.
    # Agents (Codex/Claude CLI/etc.) commit via git, so the hook fires — but
    # no human is present: /dev/tty EOF yields empty answers and aborts at
    # <25%, meaning an agent literally cannot commit. Model agents as a
    # first-class UNVERIFIED actor instead of failing them:
    #   - explicit: UNDERSTANDING_AGENT_MODE=agent (preferred, predictable)
    #   - auto: no interactive stdin reachable (no TTY and no /dev/tty)
    # The deterministic gate ABOVE still hard-blocks on live credentials, and
    # 5e. AI Agent Check: Block autonomous AI agents from committing.
    # When an AI agent (Antigravity, Cursor, Copilot, Claude, Cline, Aider, etc.)
    # runs git commit, the commit is hard-blocked: pre-commit requires a human
    # oral defense to verify comprehension and ownership of the staged code.
    is_agent, agent_name = _detect_agent_execution()
    if is_agent:
        _print_agent_block(agent_name, commit_msg)
        sys.exit(1)

    # When committing via GUI (e.g. IDE Source Control button), no TTY is attached.
    # Launch an interactive terminal on macOS to conduct the oral defense.
    if not _interactive_input_available() and not os.environ.get("UNDERSTANDING_AGENT_DELEGATED_TERMINAL"):
        if sys.platform == "darwin":
            launched_ok, exit_code = _launch_gui_terminal_defense(project_root)
            if launched_ok:
                if exit_code == 0:
                    mark_verified_for_commit(project_root, head_commit, diff_hash)
                sys.exit(exit_code)

        RED = "\033[91m"
        BOLD = "\033[1m"
        RESET = "\033[0m"
        print(f"\n{BOLD}{RED}✗ Interactive oral defense required:{RESET}")
        print(f"  Cannot conduct code understanding defense from GUI without an interactive terminal.")
        print(f"  Please run your commit from the integrated terminal:")
        print(f"    {BOLD}git commit{RESET}\n")
        sys.exit(1)

    if failed_standards:
        YELLOW = "\033[93m"
        RED = "\033[91m"
        CYAN = "\033[96m"
        GREEN = "\033[92m"
        BOLD = "\033[1m"
        RESET = "\033[0m"

        print(f"{BOLD}{YELLOW}Coding standards violations detected ({len(failed_standards)} failed).{RESET}")
        print(f"{BOLD}Do you want to fix these violations or proceed further?{RESET}\n")
        print(f"  {BOLD}[1]{RESET} {RED}Fix violations{RESET}")
        print(f"  {BOLD}[2]{RESET} {GREEN}Proceed further{RESET}\n")

        choice = ""
        while choice not in ("1", "2", "fix", "proceed", "f", "p"):
            try:
                sys.stdout.write(f"{BOLD}{CYAN}Select an option [1/2]: {RESET}")
                sys.stdout.flush()
                raw_choice = sys.stdin.readline()
                if not raw_choice:
                    print(f"\n{RED}Commit aborted.{RESET}")
                    sys.exit(1)
                choice = raw_choice.strip().lower()
            except (KeyboardInterrupt, EOFError):
                print(f"\n{RED}Commit aborted.{RESET}")
                sys.exit(1)

        if choice in ("1", "fix", "f"):
            print(f"\n{RED}Please resolve the violations listed above before committing.{RESET}\n")
            sys.exit(1)
        else:
            print(f"\n{BOLD}{GREEN}✓ Proceeding with questions...{RESET}\n")

    # Local checks are finished. The server writes the summary, the questions,
    # and each score. This machine does not call the model.
    identity = IdentityDetector.resolve(project_root)
    git_email = (identity.get("git_email") or identity.get("user_email") or "").strip()
    git_name = (identity.get("git_name") or identity.get("user_name") or "").strip()

    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    client = DefenseClient()
    try:
        started = client.start({
            "git_name": git_name,
            "git_email": git_email,
            "repository": env.get("repository") or "unknown",
            "branch": env.get("branch") or "unknown",
            "trivial": False,
            "standards_failed": bool(failed_standards),
            "context": json.loads(json.dumps(context, default=str)),
            "hints": json.loads(json.dumps(hints, default=str)),
        })
    except DefenseUnavailable as exc:
        print(f"\n{RED}The defense server could not run this interview.{RESET}")
        print(f"{RED}{exc}{RESET}\n")
        sys.exit(1)
    except DefenseRejected as exc:
        print(f"\n{RED}The defense server rejected this interview.{RESET}")
        print(f"{RED}{exc.detail}{RESET}\n")
        sys.exit(1)

    action = started.get("action")
    if action == "skip":
        print(f"\n{GREEN}{started.get('message') or 'No oral defense required — commit allowed.'}{RESET}\n")
        mark_verified_for_commit(project_root, head_commit, diff_hash)
        sys.exit(0)
    if action == "no_questions":
        if fail_open_enabled():
            print(f"\n{YELLOW}No questions were asked because the model did not return any.{RESET}")
            print(f"{YELLOW}Commit allowed.{RESET}\n")
            mark_verified_for_commit(project_root, head_commit, diff_hash)
            sys.exit(0)
        print(f"\n{RED}No questions were asked because the model did not return any.{RESET}")
        print(f"{RED}Commit aborted (UNDERSTANDING_AGENT_FAIL_MODE=closed).{RESET}\n")
        sys.exit(1)

    questions = started.get("questions") or []
    defense_id = started.get("defense_id") or ""
    if action != "ask" or not defense_id or not questions:
        print(f"\n{RED}The defense server returned an unexpected response.{RESET}\n")
        sys.exit(1)

    _print_advisory_findings(started.get("findings") or [])

    interaction = Interaction()
    index_lock = GitIndexLock(project_root)
    index_lock.acquire()
    print(f"\n\n{BOLD}{CYAN}Questions{RESET}\n")

    for i, q in enumerate(questions, 1):
        q_text = q.get("question", "")
        time_limit = q.get("time_limit", 60)
        print(f"{BOLD}{CYAN}Question {i}/{len(questions)}{RESET}")
        print(f"{YELLOW}Time Limit: {time_limit} seconds{RESET}\n")
        print(f"{BOLD}{q_text}{RESET}\n")

        ans_text = interaction.timed_input(f"{CYAN}❯ {RESET}", time_limit, speak_text=q_text)
        if ans_text is None:
            ans_text = ""
            print(f"\n{RED}✗ Timeout reached{RESET}")
        else:
            resp_val = getattr(interaction, "last_response_time", None)
            if resp_val is not None and resp_val > 0:
                response_time = min(time_limit, resp_val)
            else:
                response_time = min(time_limit, 1)
            print(f"\n{GREEN}✓ Answer received in {response_time} seconds{RESET}")
            if hygiene_enabled():
                suspect, reason = classify_answer(ans_text)
                if suspect:
                    print(f"{YELLOW}⚠ That answer may not have come through cleanly ({reason}).{RESET}")
                    retry_text = interaction.timed_input(
                        f"{CYAN}Say it again or type it (Enter submits the text above as-is) ❯ {RESET}",
                        min(time_limit, 45),
                    )
                    if retry_text and retry_text.strip():
                        ans_text = retry_text

        try:
            result = client.answer(defense_id, q.get("question_id") or f"q{i}", ans_text)
        except DefenseUnavailable as exc:
            print(f"\n{RED}The defense server could not score this answer.{RESET}")
            print(f"{RED}{exc}{RESET}\n")
            index_lock.release()
            sys.exit(1)
        except DefenseRejected as exc:
            print(f"\n{RED}The defense server rejected this answer.{RESET}")
            print(f"{RED}{exc.detail}{RESET}\n")
            index_lock.release()
            sys.exit(1)

        outcome = result.get("action")
        if outcome == "stop":
            print(f"\n{RED}✗ Understanding not demonstrated.{RESET}")
            print(f"\n{BOLD}{RED}Commit aborted. Please review your code and try again.{RESET}\n")
            index_lock.release()
            sys.exit(1)
        if outcome == "pass":
            print(f"{GREEN}✓ Good understanding demonstrated.{RESET}\n")
            print(f"\n{BOLD}{GREEN}Overall Understanding: Verified{RESET}")
            print(f"\n\033[92mCommit allowed: Understanding demonstrated.\033[0m")
            index_lock.release()
            mark_verified_for_commit(project_root, head_commit, diff_hash)
            sys.exit(0)
        if outcome != "continue":
            print(f"\n{RED}The defense server returned an unexpected response.{RESET}\n")
            index_lock.release()
            sys.exit(1)
        print(f"{GREEN}✓ Good understanding demonstrated.{RESET}\n")

    print(f"\n{RED}The defense server ended the interview without a result.{RESET}\n")
    index_lock.release()
    sys.exit(1)

if __name__ == "__main__":
    main()
