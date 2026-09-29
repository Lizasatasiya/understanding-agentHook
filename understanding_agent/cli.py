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
from .followup_generator import FollowUpGenerator

def main():
    print("\n[HOOK] Code Understanding Check")
    
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
        commit_cmd = subprocess.check_output(['ps', '-p', str(ppid), '-o', 'command=']).decode().strip()
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

    if state.get("diff_hash") == diff_hash:
        valid_questions = state.get("questions", [])
        attempts = state.get("attempts", 1) + 1
        print(f"\n[QUESTIONS] Loading {len(valid_questions)} previous questions... (Attempt {attempts})")
    else:
        print("\n[QUESTIONS] Generating questions...")
        question_generator = QuestionGenerator()
        questions = question_generator.generate(context, summary)
        valid_questions = question_generator.validate(questions)
        for q in valid_questions:
            q["passed"] = False
            q["best_score"] = 0
        attempts = 1
    
    # 8. Developer Interaction & Evaluation
    print("\n[INTERACTION] Asking developer...")
    interaction = Interaction()
    evaluator = AnswerEvaluator()
    followup_gen = FollowUpGenerator()
    
    interaction.print_context(context)
    
    while True:
        if attempts > 1:
            print(f"\n[QUESTIONS] Loading Questions... (Attempt {attempts})")
        else:
            print(f"\n[QUESTIONS] Asking Questions... (Attempt 1)")
            
        final_results = []
        total_score = 0
        
        for i, q in enumerate(valid_questions, 1):
            q_id = q.get("question_id", f"q{i}")
            q_text = q.get("question", "")
            q_type = q.get("type", "Reasoning")
            time_limit = q.get("time_limit", 60)
            
            # Colors
            CYAN = "\033[96m"
            GREEN = "\033[92m"
            YELLOW = "\033[93m"
            RED = "\033[91m"
            BOLD = "\033[1m"
            RESET = "\033[0m"

            print(f"\n{BOLD}{CYAN}╭──────────────────────────────────────────────────╮{RESET}")
            print(f"{BOLD}{CYAN}│ Question {i}/{len(valid_questions):<40}│{RESET}")
            print(f"{BOLD}{CYAN}╰──────────────────────────────────────────────────╯{RESET}")
            
            if q.get("passed", False):
                print(f"{GREEN}✓ Already passed with score {q.get('best_score', 0)}%{RESET}\n")
                total_score += q.get('best_score', 0)
                continue
                
            print(f"{YELLOW}⏳ Time Limit: {time_limit} seconds{RESET}\n")
            print(f"{BOLD}{q_text}{RESET}\n")
            
            start_time = time.time()
            ans_text = interaction.timed_input(f"{CYAN} ❯ {RESET}", time_limit)
            
            if ans_text is None:
                ans_text = ""
                status = "timeout"
                response_time = time_limit
                print(f"\n{RED}✗ Timeout reached{RESET}")
            else:
                status = "answered"
                response_time = int(time.time() - start_time)
                print(f"\n{GREEN}✓ Answer received in {response_time} seconds{RESET}")
                
            print(f"\n{CYAN}⚡ Evaluating answer...{RESET}")
            
            ans_obj = {
                "answer": ans_text,
                "response_time_seconds": response_time,
                "status": status
            }
            
            eval_res = evaluator.evaluate(q, ans_obj, context, summary)
            color = GREEN if eval_res.score >= 70 else (YELLOW if eval_res.score >= 40 else RED)
            print(f"📊 {BOLD}Score: {color}{eval_res.score}%{RESET}")
            
            follow_up_data = None
            final_score = eval_res.score
            
            if eval_res.follow_up_required:
                print(f"⚠️  {YELLOW}Answer requires clarification{RESET}")
                print(f"\n{BOLD}{CYAN}↪ Asking targeted follow-up...{RESET}\n")
                follow_up_q = followup_gen.generate(q, ans_obj, eval_res.to_dict())
                print(f"{BOLD}{follow_up_q}{RESET}\n")
                
                f_start = time.time()
                f_ans = interaction.timed_input(f"{CYAN} ❯ {RESET}", time_limit)
                
                f_status = "answered" if f_ans is not None else "timeout"
                f_resp_time = int(time.time() - f_start) if f_ans is not None else time_limit
                f_ans_text = f_ans or ""
                
                print(f"\n{CYAN}⚡ Evaluating follow-up...{RESET}")
                f_ans_obj = {
                    "answer": f_ans_text,
                    "response_time_seconds": f_resp_time,
                    "status": f_status
                }
                
                # evaluate follow up by combining context or treating it as new answer
                f_eval_res = evaluator.evaluate(
                    {"question": follow_up_q, "expected_concepts": eval_res.missing_concepts, "evaluation_criteria": q.get("evaluation_criteria", [])},
                    f_ans_obj,
                    context,
                    summary
                )
                
                # combine scores
                final_score = min(100, int(eval_res.score * 0.5 + f_eval_res.score * 0.5))
                if f_eval_res.score > 70:
                    print(f"{GREEN}✓ Understanding demonstrated{RESET}")
                else:
                    print(f"{RED}✗ Understanding still missing{RESET}")
                    
                follow_up_data = {
                    "question": follow_up_q,
                    "answer": f_ans_text,
                    "score": f_eval_res.score
                }
                
                
            final_color = GREEN if final_score >= 70 else (YELLOW if final_score >= 40 else RED)
            print(f"\n{BOLD}★ Final Question Score: {final_color}{final_score}%{RESET}")
            
            if final_score > 75:
                q["passed"] = True
                q["best_score"] = final_score
            else:
                q["best_score"] = max(q.get("best_score", 0), final_score)
                
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
            if follow_up_data:
                result_entry["follow_up"] = follow_up_data
                
            final_results.append(result_entry)

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
        avg_color = "\033[92m" if avg_score > 75 else "\033[91m"
        print(f"\n\033[1mAverage Understanding Score: {avg_color}{avg_score:.1f}%\033[0m")
        
        # Save state
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
        
        if avg_score <= 75:
            print(f"\n\033[91m⛔ Attempt {attempts} failed. Score must be > 75%.\033[0m")
            retry = interaction.timed_input(f"\033[93mWould you like to try Attempt {attempts + 1}? (y/n) \033[0m", 60)
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
