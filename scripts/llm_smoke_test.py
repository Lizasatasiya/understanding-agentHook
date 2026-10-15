#!/usr/bin/env python3
"""Real-LLM smoke test — verifies the whole LLM pipeline end to end.

Run after filling .env (see .env.example):

    python3.14 scripts/llm_smoke_test.py

Checks, in order:
  1. Config resolution — provider/model/base_url/key presence (never prints
     the key itself).
  2. Transport — one raw chat-completions round trip.
  3. Question generation — checklist-bearing questions from a fixture diff.
  4. Checklist evaluation — a substantive answer scores high, a dismissive
     answer scores low (RepoProbe protocol through the real judge).
  5. Master Analysis Pass — classification + citation-verified findings on a
     fixture diff (opt-in path; runs regardless of the env flag).

Exits non-zero on the first hard failure. Every step prints PASS/FAIL with
the evidence. Total: 3-4 real API calls.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from understanding_agent.api_utils import (  # noqa: E402
    load_api_key, call_llm_api, get_model, get_base_url, extract_json,
)
from understanding_agent.llm_manager import get_provider, resolve_judge_model  # noqa: E402
from understanding_agent.question_generator import QuestionGenerator  # noqa: E402
from understanding_agent.answer_evaluator import AnswerEvaluator  # noqa: E402
from understanding_agent.change_intelligence import run_master_analysis  # noqa: E402

FIXTURE_CONTEXT = {
    "file_summary": [{"file": "retry.py", "functions": ["send_with_retry"]}],
    "structured_changes": [{
        "file": "retry.py",
        "function": "send_with_retry",
        "diff": ("+ def send_with_retry(url, payload, retries=3):\n"
                 "+     for attempt in range(retries):\n"
                 "+         resp = post(url, payload)\n"
                 "+         if resp.status_code == 200:\n"
                 "+             return resp\n"
                 "+     return None"),
    }],
}


def step(name, ok, detail=""):
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        sys.exit(1)


def main():
    # 1. Config resolution
    key = load_api_key()
    step("1. API key resolved", bool(key),
         f"provider={get_provider()} model={get_model()} judge={resolve_judge_model()} "
         f"endpoint={get_base_url()}" if key else "set NOUS_API_KEY (or UNDERSTANDING_AGENT_API_KEY) in .env")

    # 2. Transport
    status, body = call_llm_api(key, {
        "model": get_model(),
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 10,
    }, timeout=45)
    step("2. Transport round trip", status == 200 and "OK" in body, f"HTTP {status}")

    # 3. Question generation with checklists
    gen = QuestionGenerator()
    questions = gen.generate(FIXTURE_CONTEXT, {"what_changed": "retry loop"}, hints=None)
    has_checklists = bool(questions) and all(q.get("checklist") for q in questions)
    step("3. Question generation", bool(questions),
         f"{len(questions)} questions, checklists={'yes' if has_checklists else 'MISSING'}")
    if not has_checklists:
        sys.exit(1)

    # 4. Checklist evaluation: substantive vs dismissive
    evaluator = AnswerEvaluator()
    q = questions[0]
    good = evaluator.evaluate(q, {"answer":
         "The POST is attempted up to three times in a loop. Each attempt "
         "returns immediately when the response status is 200, so an early "
         "success skips the remaining attempts. If every attempt fails, the "
         "function returns None, so callers must handle a None return "
         "instead of assuming success."}, FIXTURE_CONTEXT, {})
    bad = evaluator.evaluate(q, {"answer": "idk"}, FIXTURE_CONTEXT, {})
    step("4a. Substantive answer scores high", good.score >= 70,
         f"score={good.score} llm_verified={good.llm_verified} follow_up={good.follow_up_required}")
    step("4b. Dismissive answer scores low", bad.score <= 25,
         f"score={bad.score} follow_up={bad.follow_up_required}")

    # 5. Master Analysis Pass
    analysis = run_master_analysis(FIXTURE_CONTEXT, {"what_changed": "retry loop"})
    step("5. Master analysis returns", analysis is not None,
         f"difficulty={analysis.get('difficulty')} findings={len(analysis.get('findings', []))} "
         f"classification={analysis.get('classification', {}).get('change_type', '?')}"
         if analysis else "degraded to None (check key/endpoint) — not fatal, but expected to work")
    if analysis:
        n_q = len(analysis.get("questions", []))
        step("5b. Master questions carry checklists",
             n_q > 0 and all(q.get("checklist") for q in analysis["questions"]),
             f"{n_q} questions")

    print("\nSMOKE TEST PASSED — the full LLM pipeline works against the real provider.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
