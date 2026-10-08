import sys
import time
import json
import os
import re
import hashlib
import subprocess
from .environment_detector import EnvironmentDetector
from .change_detector import ChangeDetector
from .stack_detector import StackDetector
from .code_graph import CodeGraph
from .context_builder import ContextBuilder
from .change_summary import ChangeSummary
from .security_lens import SecurityLens, security_question_hint, security_gate_enabled
from .evidence_collector import collect_evidence, evidence_question_hint
from .practice_packs import practice_pack_hint
from .question_generator import QuestionGenerator
from .followup_generator import FollowUpGenerator
from .interaction import Interaction
from .server_client import ServerClient
from .answer_evaluator import AnswerEvaluator
from .coding_standards import CodingStandardsChecker
from .session_store import save_session
from .api_utils import load_nous_api_key
from .commit_context import (
    get_head_commit,
    get_current_commit_message,
    compute_attempt_key,
    get_next_attempt_number
)

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


def _write_state(state_file: str, attempt_key: str, head_commit, commit_msg,
                 diff_hash: str, attempts: int, valid_questions: list) -> None:
    """Persist hook state so attempts and answered questions survive retries.

    Best-effort: a state-file failure must never abort the commit flow.
    """
    try:
        with open(state_file, "w") as f:
            json.dump({
                "attempt_key": attempt_key,
                "head_commit": head_commit,
                "commit_message": commit_msg,
                "diff_hash": diff_hash,
                "attempts": attempts,
                "questions": valid_questions
            }, f)
    except Exception:
        pass


def main():
    try:
        con = 'CON' if sys.platform == 'win32' else '/dev/tty'
        sys.stdin = open(con, 'r', encoding='utf-8')
        sys.stdout = open(con, 'w', encoding='utf-8')
    except Exception:
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
    attempt_key = compute_attempt_key(head_commit, diff_hash, commit_msg)
        
    # 3. Stack Detection (languages, frameworks, domains — keys all downstream hints)
    stack = StackDetector().detect(project_root)

    # 4. Code Graph
    graph = CodeGraph()
    graph.build(changes.get("files") or [])

    # 5. Context Builder
    context_builder = ContextBuilder()
    context = context_builder.build(changes, graph)

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

    hints = {
        "stack": stack,
        "security": security_hint,
        "evidence": evidence_hint,
        "practices": practice_hint,
        "standards": standards_report,
    }

    failed_standards = [s for s in standards_report if not s.get("is_good", True)]
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
            try:
                from .session_store import load_sessions
                _existing = load_sessions(project_root)
                fix_attempt_num = get_next_attempt_number(
                    _existing, head_commit, diff_hash, commit_msg, state_attempts=0
                )
            except Exception:
                fix_attempt_num = 1
            session_data = {
                "session_id": f"sess_{int(time.time()*1000)}_{fix_attempt_num}",
                "attempt_id": f"sess_{int(time.time()*1000)}_{fix_attempt_num}",
                "attempt_number": fix_attempt_num,
                "commit_id": "staged",
                "head_commit": head_commit,
                "commit_message": commit_msg,
                "attempt_key": attempt_key,
                "diff_hash": diff_hash,
                "repository": env.get("repository", "unknown"),
                "branch": env.get("branch", "unknown"),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "status": "FAILED",
                "score": 0,
                "changed_files": changes.get("files", []),
                "change_summary": {"what_changed": "Staged changes blocked by coding standards."},
                "standards_report": standards_report,
                "violations_count": len(failed_standards),
                "questions": []
            }
            try:
                save_session(project_root, session_data)
                ServerClient().send(session_data)
            except Exception:
                pass
            sys.exit(1)
        else:
            print(f"\n{BOLD}{GREEN}✓ Proceeding with questions...{RESET}\n")

    # 6. Change Summary
    summary = ChangeSummary().generate(context)
    
    # 6. Question Generator & State Management
    # Attempt count depends solely on attempts made towards the next commit (under head_commit).
    # Commit message is NOT checked.
    state_file = ".git/understanding_agent_state.json"
    
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                state = json.load(f)
        except Exception:
            pass

    existing_sessions = []
    try:
        from .session_store import load_sessions
        existing_sessions = load_sessions(project_root)
    except Exception:
        pass

    norm_head = (head_commit or "").strip()
    matching_sessions = [
        s for s in existing_sessions
        if (s.get("commit_id") == "staged" or s.get("is_staged") or not s.get("commit_id")) and
        (s.get("head_commit") or "").strip() == norm_head
    ]
    recorded_attempts = [s.get("attempt_number", 0) for s in matching_sessions]
    max_recorded_attempt = max(recorded_attempts + [len(matching_sessions)], default=0)

    # State belongs to current commit cycle if head matches
    state_head = (state.get("head_commit") or "").strip()
    state_matches_head = (state_head == norm_head) if (state_head or norm_head) else True
    state_attempts = state.get("attempts", 0) if state_matches_head else 0

    # Attempts only depend on how many attempts made to next commit
    attempts = max(max_recorded_attempt + 1, state_attempts + 1, 1)

    # Cached questions can be reused if the diff hasn't changed
    state_matches_diff = state_matches_head and (state.get("diff_hash") == diff_hash)
    cached_questions = state.get("questions", []) if state_matches_diff else []

    # If state has no questions but matching previous session exists for this diff, restore questions & scores
    if not cached_questions and state_matches_head:
        candidates = [s for s in matching_sessions if s.get("diff_hash") == diff_hash and s.get("questions")]
        if candidates:
            def _rank_sess(s):
                qs = s.get("questions", [])
                ans = sum(1 for q in qs if ((q.get("evaluation") or {}).get("score", 0) >= 70 or q.get("score", 0) >= 70))
                return (ans, len(qs), s.get("attempt_number", 0))
            best_session = max(candidates, key=_rank_sess)
            cached_questions = []
            for sq in best_session.get("questions", []):
                sq_eval = sq.get("evaluation") or {}
                score = sq_eval.get("score") if isinstance(sq_eval, dict) and "score" in sq_eval else sq.get("score", 0)
                cq = {
                    "question_id": sq.get("question_id"),
                    "question": sq.get("question"),
                    "type": sq.get("type", "Reasoning"),
                    "time_limit": sq.get("time_limit_seconds") or sq.get("time_limit", 60),
                    "expected_concepts": sq.get("expected_concepts", []),
                    "evaluation_criteria": sq.get("evaluation_criteria", []),
                    "is_fallback": sq.get("is_fallback", False),
                    "answer": sq.get("answer", ""),
                    "status": sq.get("status", ""),
                    "evaluation": sq_eval,
                    "response_time_seconds": sq.get("response_time_seconds", 0),
                    "best_score": score,
                    "answered_in_attempt": sq.get("answered_in_attempt") or best_session.get("attempt_number"),
                }
                if score >= 70:
                    cq["answered"] = True
                    cq["passed"] = True
                else:
                    cq["answered"] = False
                    cq["passed"] = False
                cached_questions.append(cq)

    if state_matches_diff and cached_questions:
        valid_questions = cached_questions
        # Reconcile scores and answers with all matching sessions for this diff
        for q in valid_questions:
            q_text = (q.get("question") or "").strip().lower()
            q_id = q.get("question_id")
            for s in matching_sessions:
                if s.get("diff_hash") == diff_hash:
                    for sq in s.get("questions", []):
                        sq_text = (sq.get("question") or "").strip().lower()
                        sq_id = sq.get("question_id")
                        if (q_id and q_id == sq_id) or (q_text and q_text == sq_text):
                            sq_eval = sq.get("evaluation") or {}
                            score = sq_eval.get("score") if isinstance(sq_eval, dict) and "score" in sq_eval else sq.get("score", 0)
                            if score > q.get("best_score", 0):
                                q["best_score"] = score
                                q["answer"] = sq.get("answer", q.get("answer", ""))
                                q["evaluation"] = sq_eval
                                q["response_time_seconds"] = sq.get("response_time_seconds", q.get("response_time_seconds", 0))
                                q["answered_in_attempt"] = sq.get("answered_in_attempt") or s.get("attempt_number")
            if q.get("best_score", 0) >= 70 or q.get("answered", False) or q.get("passed", False):
                q["answered"] = True
                q["passed"] = True
            else:
                q["answered"] = False
                q["passed"] = False
                # Modernize any legacy or robotic cached standards questions
                raw_q = q.get("question", "")
                if "coding audit flagged" in raw_q.lower() or "(changed)" in raw_q:
                    q_gen = QuestionGenerator()
                    name_m = re.search(r"flagged '([^']+)'", raw_q)
                    rule_name = name_m.group(1) if name_m else "Nesting Depth"
                    matching_fs = None
                    for fs in failed_standards:
                        if rule_name in fs.get("name", ""):
                            matching_fs = fs
                            break
                    if not matching_fs:
                        matching_fs = {"name": rule_name, "details": raw_q}
                    modern_q = q_gen._build_proper_standard_question(matching_fs, context)
                    q["question"] = modern_q["question"]
                    q["type"] = modern_q["type"]
                    q["time_limit"] = modern_q["time_limit"]
                    q["expected_concepts"] = modern_q["expected_concepts"]
                    q["evaluation_criteria"] = modern_q["evaluation_criteria"]
                    q["is_standards_violation"] = True
    else:
        # Generate fresh questions for new diff
        question_generator = QuestionGenerator()
        questions = question_generator.generate(context, summary, hints, env=env)
        valid_questions = question_generator.validate(questions)
        for q in valid_questions:
            q["passed"] = False
            q["answered"] = False
            q["best_score"] = 0

    # An attempt is LLM-verified only when questions were LLM-generated AND
    # every evaluation came from the LLM (not the offline fallback).
    questions_llm_generated = not all(q.get("is_fallback", False) for q in valid_questions) if valid_questions else True

    interaction = Interaction()
    evaluator = AnswerEvaluator()
    followup_generator = FollowUpGenerator()
    
    # Colors
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    while True:
        # Persist state so attempt number and questions are tracked even if interrupted
        _write_state(state_file, attempt_key, head_commit, commit_msg,
                     diff_hash, attempts, valid_questions)

        print(f"\n\n{BOLD}{CYAN}Questions [Attempt {attempts}]{RESET}\n")
        final_results = []
        total_score = 0
        any_eval_unverified = False
        
        for i, q in enumerate(valid_questions, 1):
            q_id = q.get("question_id", f"q{i}")
            q_text = q.get("question", "")
            q_type = q.get("type", "Reasoning")
            time_limit = q.get("time_limit", 60)

            print(f"{BOLD}{CYAN}Question {i}/{len(valid_questions)}{RESET}")
      
            # If answer is already given with passing score, show Already Answered and skip
            if q.get("best_score", 0) >= 70 or q.get("answered", False) or q.get("passed", False):
                origin_att = q.get("answered_in_attempt")
                if origin_att:
                    print(f"{GREEN}✓ Already Answered (Attempt #{origin_att}){RESET}\n")
                else:
                    print(f"{GREEN}✓ Already Answered{RESET}\n")
                total_score += q.get('best_score', 0)
                final_results.append({
                    "question_id": q_id,
                    "question": q_text,
                    "type": q_type,
                    "time_limit_seconds": time_limit,
                    "answer": q.get("answer", ""),
                    "response_time_seconds": q.get("response_time_seconds", 0),
                    "status": "answered",
                    "answered_in_attempt": origin_att,
                    "evaluation": q.get("evaluation") or {
                        "score": q.get("best_score", 0),
                        "understanding": "good",
                        "rationale": f"Previously answered in Attempt #{origin_att}" if origin_att else "Previously answered and verified",
                        "key_points_covered": [],
                        "missing_concepts": [],
                        "follow_up_required": False
                    }
                })
                continue
                
            print(f"{YELLOW}Time Limit: {time_limit} seconds{RESET}\n")

            # On retry attempts, lightly rephrase questions that were previously
            # failed so the developer gets a fresh angle on the same concept.
            if attempts > 1 and not q.get("passed", False) and q.get("best_score", 0) < 70:
                rephrased = followup_generator.rephrase_failed_question(q)
                if rephrased and rephrased.strip() != q_text.strip():
                    q_text = rephrased

            print(f"{BOLD}{q_text}{RESET}\n")

            # Follow-up state for this question (set only if a partial answer triggers one)
            fu_ans = None
            fu_eval = None
            fu_ans_obj = {}
            followup_q = ""

            ans_text = interaction.timed_input(f"{CYAN}❯ {RESET}", time_limit, speak_text=q_text)
            
            if ans_text is None:
                ans_text = ""
                status = "timeout"
                response_time = time_limit
                print(f"\n{RED}✗ Timeout reached{RESET}")
            else:
                status = "answered"
                resp_val = getattr(interaction, "last_response_time", None)
                if resp_val is not None and resp_val > 0:
                    response_time = min(time_limit, resp_val)
                else:
                    response_time = min(time_limit, 1)
                print(f"\n{GREEN}✓ Answer received in {response_time} seconds{RESET}")
            ans_obj = {
                "answer": ans_text,
                "response_time_seconds": response_time,
                "status": status
            }
            
            eval_res = evaluator.evaluate(q, ans_obj, context, summary, env=env)
            final_score = eval_res.score
            if not eval_res.llm_verified:
                any_eval_unverified = True

            # Targeted follow-up for partial understanding: one focused question
            # on the missing concept, replacing the original score for this item.
            if 25 <= final_score < 70 and eval_res.follow_up_required and (ans_text or "").strip():
                missing = ", ".join(eval_res.missing_concepts) or "the missing concept"
                followup_q = followup_generator.generate(q, ans_obj, eval_res.to_dict())
                print(f"\n{YELLOW}Partial understanding. Follow-up on: {missing}{RESET}\n")
                print(f"{BOLD}{followup_q}{RESET}\n")

                fu_limit = min(time_limit, 45)
                fu_ans = interaction.timed_input(
                    f"{CYAN}❯ {RESET}", fu_limit, speak_text=followup_q
                )
                if fu_ans is None:
                    fu_ans = ""
                    fu_secs = fu_limit
                    print(f"{RED}✗ Timeout reached on follow-up{RESET}")
                else:
                    fu_val = getattr(interaction, "last_response_time", None)
                    if fu_val is not None and fu_val > 0:
                        fu_secs = min(fu_limit, fu_val)
                    else:
                        fu_secs = min(fu_limit, 1)
                    print(f"{GREEN}✓ Follow-up answer received in {fu_secs} seconds{RESET}")

                fu_ans_obj = {
                    "answer": fu_ans,
                    "response_time_seconds": fu_secs,
                    "status": "answered" if (fu_ans or "").strip() else "timeout"
                }
                fu_eval = evaluator.evaluate(
                    {"question": followup_q, "type": q.get("type", "Reasoning"),
                     "expected_concepts": eval_res.missing_concepts,
                     "evaluation_criteria": []},
                    fu_ans_obj, context, summary, env=env
                )
                final_score = max(final_score, fu_eval.score)
                eval_res = fu_eval
                ans_obj = fu_ans_obj
                if not fu_eval.llm_verified:
                    any_eval_unverified = True
                if final_score >= 70:
                    print(f"{GREEN}✓ Good understanding demonstrated on follow-up{RESET}")
                elif final_score >= 25:
                    print(f"{YELLOW}Partial understanding demonstrated on follow-up{RESET}")
                else:
                    print(f"{RED}✗ Understanding not demonstrated on follow-up{RESET}")
            elif final_score >= 70:
                print(f"{GREEN}✓ Good understanding demonstrated{RESET}")
            elif final_score >= 25:
                print(f"{YELLOW}Partial understanding demonstrated{RESET}")
            else:
                print(f"{RED}✗ Understanding not demonstrated{RESET}")
            
            # Only scores in GREEN (>= 70%) are considered answered and passed
            q["best_score"] = max(q.get("best_score", 0), final_score)
            if q["best_score"] >= 70:
                q["answered"] = True
                q["passed"] = True
                if not q.get("answered_in_attempt"):
                    q["answered_in_attempt"] = attempts
            else:
                q["answered"] = False
                q["passed"] = False
                
            total_score += q["best_score"]
            
            result_entry = {
                "question_id": q_id,
                "question": q_text,
                "type": q_type,
                "time_limit_seconds": time_limit,
                "answer": ans_text,
                "response_time_seconds": response_time,
                "status": status,
                "answered_in_attempt": q.get("answered_in_attempt") or (attempts if q["best_score"] >= 70 else None),
                "evaluation": eval_res.to_dict()
            }
            # Record the follow-up exchange when one occurred
            if fu_ans is not None or fu_eval is not None:
                result_entry["follow_up"] = {
                    "question": followup_q,
                    "answer": fu_ans_obj.get("answer", ""),
                    "status": fu_ans_obj.get("status", "timeout"),
                    "evaluation": fu_eval.to_dict()
                }
            final_results.append(result_entry)

            q["answer"] = ans_text
            q["evaluation"] = eval_res.to_dict()
            q["response_time_seconds"] = response_time

            # Persist state immediately so answered questions are preserved across attempts
            _write_state(state_file, attempt_key, head_commit, commit_msg,
                         diff_hash, attempts, valid_questions)

            # If user gives wrong answer (< 25%), ask no next questions and abort
            if final_score < 25:
                print(f"\n{BOLD}{RED}Commit aborted. Please review the code and then try again.{RESET}\n")
                
                try:
                    session_data = {
                        "session_id": f"sess_{int(time.time()*1000)}_{attempts}",
                        "attempt_id": f"sess_{int(time.time()*1000)}_{attempts}",
                        "attempt_number": attempts,
                        "commit_id": "staged",
                        "head_commit": head_commit,
                        "commit_message": commit_msg,
                        "attempt_key": attempt_key,
                        "diff_hash": diff_hash,
                        "repository": env.get("repository", "unknown"),
                        "branch": env.get("branch", "unknown"),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "status": "FAILED",
                        "verification": "llm-verified" if (questions_llm_generated and not any_eval_unverified) else "llm-unverified",
                        "score": round(final_score, 1),
                        "changed_files": changes.get("files", []),
                        "change_summary": summary,
                        "standards_report": standards_report,
                        "violations_count": len(failed_standards),
                        "questions": final_results
                    }
                    save_session(project_root, session_data)
                    ServerClient().send(session_data)
                except Exception:
                    pass
                sys.exit(1)

        avg_score = total_score / len(valid_questions) if valid_questions else 100
        # LLM verification: honest even when failing open. A session is only
        # "llm-verified" when questions were LLM-generated and no evaluation
        # fell back to the offline scorer.
        verification = "llm-verified" if (questions_llm_generated and not any_eval_unverified) else "llm-unverified"

        # When API is not working and fallback questions are being asked,
        # if all answers have been given, do not check score and allow commit.
        fallback_questions_asked = (not questions_llm_generated) or any(q.get("is_fallback", False) for q in valid_questions)
        api_not_working = (not load_nous_api_key()) or (verification == "llm-unverified") or any_eval_unverified or (not questions_llm_generated)
        all_answers_given = (
            bool(valid_questions)
            and all(bool((q.get("answer") or "").strip()) for q in valid_questions)
            and all(bool((r.get("answer") or "").strip()) and r.get("status") != "timeout" for r in final_results)
        )

        if fallback_questions_asked and api_not_working and all_answers_given:
            is_passed = True
        else:
            is_passed = avg_score > 75

        attempt_status = "PASSED" if is_passed else "FAILED"
        if is_passed:
            print(f"\n{BOLD}{GREEN}Overall Understanding: Verified{RESET}")
        else:
            print(f"\n{BOLD}{RED}Overall Understanding: Insufficient{RESET}")

        # 9. Server Client & Session Storage
        session_data = {
            "session_id": f"sess_{int(time.time()*1000)}_{attempts}",
            "attempt_id": f"sess_{int(time.time()*1000)}_{attempts}",
            "attempt_number": attempts,
            "commit_id": "staged",
            "head_commit": head_commit,
            "commit_message": commit_msg,
            "attempt_key": attempt_key,
            "diff_hash": diff_hash,
            "repository": env.get("repository", "unknown"),
            "branch": env.get("branch", "unknown"),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": attempt_status,
            "verification": verification,
            "score": round(avg_score, 1),
            "changed_files": changes.get("files", []),
            "change_summary": summary,
            "standards_report": standards_report,
            "violations_count": len(failed_standards),
            "questions": final_results
        }
        try:
            save_session(project_root, session_data)
            ServerClient().send(session_data)
        except Exception:
            pass
        
        # Save state: keep attempts tracked and preserve questions across attempts
        _write_state(state_file, attempt_key, head_commit, commit_msg,
                     diff_hash, attempts, valid_questions)
        
        if not is_passed:
            print(f"\n{RED}Attempt {attempts} failed. Understanding criteria not met.{RESET}")
            retry = interaction.timed_input(f"{YELLOW}Would you like to try Attempt {attempts + 1}? (y/n) {RESET}", 60)
            if retry and retry.strip().lower() == 'y':
                attempts += 1
                continue
            else:
                print(f"\n\033[91mCommit aborted.\033[0m")
                sys.exit(1)
        else:
            print(f"\n\033[92mCommit allowed: Understanding demonstrated.\033[0m")
            # Cleanup state on success
            if os.path.exists(state_file):
                os.remove(state_file)
            sys.exit(0)

if __name__ == "__main__":
    main()
