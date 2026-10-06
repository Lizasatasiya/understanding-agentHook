import sys
import time
import json
import os
import hashlib
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
    
    if not changes.get("files"):
        sys.exit(0)
        
    # 3. Stack Detection (languages, frameworks, domains — keys all downstream hints)
    stack = StackDetector().detect(env.get("project_root") or os.getcwd())

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
    evidence = collect_evidence(changes.get("files", []), env.get("project_root") or os.getcwd())
    evidence_hint = evidence_question_hint(evidence)
    practice_hint = practice_pack_hint(stack, context, security_hint)

    # 5c. Coding Standards Audit (deterministic, printed first before questions)
    standards_checker = CodingStandardsChecker(env.get("project_root") or os.getcwd())
    standards_report = standards_checker.check(changes, context)
    standards_checker.print_report(standards_report)

    hints = {
        "stack": stack,
        "security": security_hint,
        "evidence": evidence_hint,
        "practices": practice_hint,
        "standards": standards_report,
    }

    # 5c. Security gate: deterministic critical findings (live credentials) block
    # before any questions are generated. Unlike the oral defense, no answer can
    # make committing a real credential safe, and git history is permanent.
    if security_gate_enabled():
        critical = lens.find_critical_findings(context)
        if critical:
            print(f"\n\033[91m⛔ Commit blocked: credential detected in staged code.\033[0m\n")
            for f in critical:
                print(f"  \033[91m✗ {f['description']} ({f['redacted']}) in {f['file']}"
                      + (f" :: {f['function']}()" if f['function'] else "") + "\033[0m")
            print("""
\033[93m  Remove the credential before committing:\033[0m
  1. Move it to an environment variable or your .env (already gitignored)
  2. If it was ever committed before, rotate it — history keeps it forever
  3. If this is a false positive, set UNDERSTANDING_AGENT_SECURITY_GATE=off
     for this commit: git commit  # after unsetting, or use --no-verify
""")
            sys.exit(1)

    # 6. Change Summary
    summary_generator = ChangeSummary()
    summary = summary_generator.generate(context)
    
    # 6. Question Generator & State Management
    # Hash only the detected changes: a retry with an edited commit message
    # must reuse cached questions rather than regenerate everything.
    diff_hash = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()
    state_file = ".git/understanding_agent_state.json"
    
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                state = json.load(f)
        except Exception:
            pass

    cached_questions = state.get("questions", [])
    has_fallback = any(q.get("is_fallback") for q in cached_questions)

    if state.get("diff_hash") == diff_hash and cached_questions and not has_fallback:
        valid_questions = cached_questions
        for q in valid_questions:
            # Only scores in GREEN (>= 70%) are considered passed and skipped
            if q.get("best_score", 0) >= 70:
                q["passed"] = True
            else:
                q["passed"] = False
        attempts = state.get("attempts", 1) + 1
    else:
        question_generator = QuestionGenerator()
        questions = question_generator.generate(context, summary, hints, env=env)
        valid_questions = question_generator.validate(questions)
        for q in valid_questions:
            q["passed"] = False
            q["best_score"] = 0
        attempts = 1
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
        print(f"\n\n{BOLD}{CYAN}Questions [Attempt {attempts}]{RESET}\n")
        final_results = []
        total_score = 0
        
        for i, q in enumerate(valid_questions, 1):
            q_id = q.get("question_id", f"q{i}")
            q_text = q.get("question", "")
            q_type = q.get("type", "Reasoning")
            time_limit = q.get("time_limit", 60)

            print(f"{BOLD}{CYAN}Question {i}/{len(valid_questions)}{RESET}")
      
            # Only consider answered and skip if the answer score is in GREEN (>= 70%)
            if q.get("best_score", 0) >= 70:
                print(f"{GREEN}✓ Already answered and verified{RESET}\n")
                total_score += q.get('best_score', 0)
                continue
                
            print(f"{YELLOW}⏳ Time Limit: {time_limit} seconds{RESET}\n")
            print(f"{BOLD}{q_text}{RESET}\n")

            # Follow-up state for this question (set only if a partial answer triggers one)
            fu_ans = None
            fu_eval = None
            fu_ans_obj = {}
            followup_q = ""

            start_time = time.time()
            ans_text = interaction.timed_input(f"{CYAN}❯ {RESET}", time_limit, speak_text=q_text)
            
            if ans_text is None:
                ans_text = ""
                status = "timeout"
                response_time = time_limit
                print(f"\n{RED}✗ Timeout reached{RESET}")
            else:
                status = "answered"
                response_time = int(time.time() - start_time)
                print(f"\n{GREEN}✓ Answer received in {response_time} seconds{RESET}")
            ans_obj = {
                "answer": ans_text,
                "response_time_seconds": response_time,
                "status": status
            }
            
            eval_res = evaluator.evaluate(q, ans_obj, context, summary, env=env)
            final_score = eval_res.score

            # Targeted follow-up for partial understanding: one focused question
            # on the missing concept, replacing the original score for this item.
            if 25 <= final_score < 70 and eval_res.follow_up_required and (ans_text or "").strip():
                missing = ", ".join(eval_res.missing_concepts) or "the missing concept"
                followup_q = followup_generator.generate(q, ans_obj, eval_res.to_dict())
                print(f"\n{YELLOW}⚠️ Partial understanding. Follow-up on: {missing}{RESET}\n")
                print(f"{BOLD}{followup_q}{RESET}\n")

                fu_start = time.time()
                fu_ans = interaction.timed_input(
                    f"{CYAN}❯ {RESET}", min(time_limit, 45), speak_text=followup_q
                )
                if fu_ans is None:
                    fu_ans = ""
                    print(f"{RED}✗ Timeout reached on follow-up{RESET}")
                else:
                    fu_secs = int(time.time() - fu_start)
                    print(f"{GREEN}✓ Follow-up answer received in {fu_secs} seconds{RESET}")

                fu_ans_obj = {
                    "answer": fu_ans,
                    "response_time_seconds": max(0, int(time.time() - fu_start)),
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
                if final_score >= 70:
                    print(f"{GREEN}✓ Good understanding demonstrated on follow-up{RESET}")
                elif final_score >= 25:
                    print(f"{YELLOW}⚠️ Partial understanding demonstrated on follow-up{RESET}")
                else:
                    print(f"{RED}✗ Understanding not demonstrated on follow-up{RESET}")
            elif final_score >= 70:
                print(f"{GREEN}✓ Good understanding demonstrated{RESET}")
            elif final_score >= 25:
                print(f"{YELLOW}⚠️ Partial understanding demonstrated{RESET}")
            else:
                print(f"{RED}✗ Understanding not demonstrated{RESET}")
            
            # Only scores in GREEN (>= 70%) are considered answered and passed
            q["best_score"] = max(q.get("best_score", 0), final_score)
            if q["best_score"] >= 70:
                q["answered"] = True
                q["passed"] = True
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

            # Persist state immediately
            if not any(quest.get("is_fallback") for quest in valid_questions):
                try:
                    with open(state_file, "w") as f:
                        json.dump({
                            "diff_hash": diff_hash,
                            "attempts": attempts,
                            "questions": valid_questions
                        }, f)
                except Exception:
                    pass

            # If user gives wrong answer (< 25%), ask no next questions and abort
            if final_score < 25:
                print(f"\n{BOLD}{RED}⛔ Commit aborted. Please review the code and then try again.{RESET}\n")
                
                try:
                    client = ServerClient()
                    session_data = {
                        "repository": env.get("repository", "unknown"),
                        "branch": env.get("branch", "unknown"),
                        "changed_files": changes.get("files", []),
                        "change_summary": summary,
                        "questions": final_results
                    }
                    client.send(session_data)
                except Exception:
                    pass
                sys.exit(1)

        # 9. Server Client
        client = ServerClient()
        session_data = {
            "repository": env.get("repository", "unknown"),
            "branch": env.get("branch", "unknown"),
            "changed_files": changes.get("files", []),
            "change_summary": summary,
            "questions": final_results
        }
        client.send(session_data)
        
        avg_score = total_score / len(valid_questions) if valid_questions else 100
        if avg_score > 75:
            print(f"\n{BOLD}{GREEN}✅ Overall Understanding: Verified{RESET}")
        else:
            print(f"\n{BOLD}{RED}⛔ Overall Understanding: Insufficient{RESET}")
        
        # Save state (only persist if not fallback, so subsequent runs can retry AI generation)
        is_fallback_run = any(q.get("is_fallback") for q in valid_questions)
        if not is_fallback_run:
            new_state = {
                "diff_hash": diff_hash,
                "attempts": attempts,
                "questions": valid_questions
            }
            try:
                with open(state_file, "w") as f:
                    json.dump(new_state, f)
            except Exception:
                pass
        else:
            if os.path.exists(state_file):
                try:
                    os.remove(state_file)
                except Exception:
                    pass
        
        if avg_score <= 75:
            print(f"\n{RED}⛔ Attempt {attempts} failed. Understanding criteria not met.{RESET}")
            retry = interaction.timed_input(f"{YELLOW}Would you like to try Attempt {attempts + 1}? (y/n) {RESET}", 60)
            if retry and retry.strip().lower() == 'y':
                attempts += 1
                continue
            else:
                print(f"\n\033[91m⛔ Commit aborted.\033[0m")
                sys.exit(1)
        else:
            print(f"\n\033[92m✅ Commit allowed: Understanding demonstrated.\033[0m")
            # Cleanup state on success
            if os.path.exists(state_file):
                os.remove(state_file)
            sys.exit(0)

if __name__ == "__main__":
    main()
