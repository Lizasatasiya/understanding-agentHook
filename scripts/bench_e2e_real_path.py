"""End-to-end latency check through the REAL QuestionGenerator + AnswerEvaluator
path (not the bench harness) with the thinking-disable fix live, on both models."""

import sys
import time
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from understanding_agent.api_utils import load_nous_api_key  # noqa: E402
from understanding_agent.question_generator import QuestionGenerator  # noqa: E402
from understanding_agent.change_summary import ChangeSummary  # noqa: E402
from bench_llm_latency import realistic_question_prompt  # noqa: E402


def build_context():
    """Reuse the bench prompt's change shape as a realistic context."""
    diff_lines = "\n".join(
        f"+    total += item['qty'] * item['unit_price_{i}']" for i in range(12))
    diff = (
        "--- a/src/billing/Calculator.tsx\n+++ b/src/billing/Calculator.tsx\n"
        "@@ -10,6 +10,20 @@\n" + diff_lines +
        "\n+    if (coupon) total = applyCoupon(total, coupon)\n+    return round2(total)\n")
    return {
        "stats": {"total_loc": 18, "total_added": 14, "total_deleted": 0},
        "structured_changes": [{
            "file": "src/billing/Calculator.tsx",
            "function": "calculateInvoice",
            "diff": diff,
            "dependency_summary": "calls applyCoupon(), round2()",
        }],
        "file_summary": [{"file": "src/billing/Calculator.tsx", "functions": ["calculateInvoice"]}],
        "is_large_change": False,
        "summary": {},
    }


def run_once(model):
    os.environ["UNDERSTANDING_AGENT_MODEL"] = model
    import understanding_agent.api_utils as au
    ctx = build_context()
    gen = QuestionGenerator()

    t0 = time.perf_counter()
    summary = ChangeSummary().generate(ctx)
    t_summary = time.perf_counter() - t0

    t0 = time.perf_counter()
    questions = gen.generate(ctx, summary, hints={"security": None, "standards": []}, env=None)
    t_questions = time.perf_counter() - t0

    n = len(questions)
    fallback = all(q.get("is_fallback") for q in questions)
    print(f"  model={model}")
    print(f"    summary:    {t_summary:6.2f}s")
    print(f"    questions:  {t_questions:6.2f}s  count={n}  all_fallback={fallback}")
    if questions and not fallback:
        print(f"    first q:   {questions[0]['question'][:90]}")
    return t_summary, t_questions


def main():
    key = load_nous_api_key()
    if not key:
        print("no key; abort")
        sys.exit(1)
    for model in ("qwen/qwen3-coder-30b-a3b-instruct", "deepseek/deepseek-v4-flash"):
        for run in (1, 2):
            print(f"--- run {run}")
            try:
                run_once(model)
            except Exception as e:
                print(f"    ERROR: {e}")
            print()


if __name__ == "__main__":
    main()
