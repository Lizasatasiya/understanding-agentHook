import sys
import time
import json
import os
import hashlib
from .environment_detector import EnvironmentDetector
from .change_detector import ChangeDetector
from .code_graph import CodeGraph
from .context_builder import ContextBuilder
from .change_summary import ChangeSummary
from .question_generator import QuestionGenerator
from .interaction import Interaction
from .server_client import ServerClient
from .answer_evaluator import AnswerEvaluator

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
        
    # 3. Code Graph
    graph = CodeGraph()
    graph.build(changes.get("files"))
    
    # 4. Context Builder
    context_builder = ContextBuilder()
    context = context_builder.build(changes, graph)
    
    # 5. Change Summary
    summary_generator = ChangeSummary()
    summary = summary_generator.generate(context)
    
    # 6. Question Generator & State Management
    # Try to grab the parent git command line (which contains the -m message) to include in the hash
    try:
        import subprocess
        ppid = os.getppid()
        commit_cmd = subprocess.check_output(
            ['ps', '-p', str(ppid), '-o', 'command='],
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        commit_cmd = ""
        
    diff_hash = hashlib.md5((json.dumps(changes, sort_keys=True) + commit_cmd).encode()).hexdigest()
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
        questions = question_generator.generate(context, summary)
        valid_questions = question_generator.validate(questions)
        for q in valid_questions:
            q["passed"] = False
            q["best_score"] = 0
        attempts = 1
    interaction = Interaction()
    evaluator = AnswerEvaluator()
    
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
            
            eval_res = evaluator.evaluate(q, ans_obj, context, summary)
            final_score = eval_res.score
            if final_score >= 70:
                print(f"\n{GREEN}✓ Good understanding demonstrated{RESET}")
            elif final_score >= 25:
                print(f"\n{YELLOW}⚠️ Partial understanding demonstrated{RESET}")
            else:
                print(f"\n{RED}✗ Understanding not demonstrated{RESET}")
            
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
