#!/usr/bin/env python3
"""Demo probe — the presenter's dry-run instrument for the oral defense.

Run from INSIDE a target repo with changes staged (the demo repos mirror real
upstream fixes). Replays the REAL pipeline and prints everything the hook
will show at commit time, without needing an interactive terminal:

  1. deterministic ChangeKind classification (weights + signals)
  2. context stats (functions, diff lines after the budget)
  3. offline fallback questions (what a no-key run would ask)
  4. LIVE LLM questions with wall-clock timing (the .env key is used)

Usage (from the target repo):
  python3.14 /path/to/understanding-agentHook/scripts/demo_probe.py [--offline-only]

Exit 0 on success; non-zero if the live path fails (check the key/network).
"""
import argparse
import os
import sys
import time
from pathlib import Path

HOOK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOK_ROOT))

from understanding_agent.change_kind import classify_change
from understanding_agent.change_detector import ChangeDetector
from understanding_agent.change_summary import ChangeSummary
from understanding_agent.code_graph import CodeGraph
from understanding_agent.context_builder import ContextBuilder
from understanding_agent.question_generator import QuestionGenerator
from understanding_agent.api_utils import load_nous_api_key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline-only", action="store_true",
                        help="skip the live LLM call")
    args = parser.parse_args()

    repo = os.getcwd()
    print(f"=== demo probe: {Path(repo).name} ===")

    changes = ChangeDetector().detect()
    graph = CodeGraph()
    graph.build(changes.get("files") or [])
    context = ContextBuilder().build(changes, graph)

    ck = classify_change(changes, context)
    if ck["dominant"]:
        strong = ", ".join(f"{d} {ck['weights'][d]:.0%}"
                           for d in ck["weights"] if ck["weights"][d] >= 0.25)
        print(f"\nChange classification: {ck['dominant']} ({strong})")
        print(f"  signals: {'; '.join(ck['signals'][:6])}")
    else:
        print("\nChange classification: unknown (no signals)")

    fn_diffs = context.get("structured_changes", [])
    diff_lines = sum(len(c.get("diff", "").splitlines()) for c in fn_diffs)
    print(f"Context: {len(fn_diffs)} function diffs, {diff_lines} lines after budget")

    summary = ChangeSummary().generate(context)
    print(f"Summary: {(summary or {}).get('what_changed', '')[:120]}")

    gen = QuestionGenerator()

    print("\n--- offline fallback questions (no key needed) ---")
    offline_q = gen._fallback(context, None)
    for q in offline_q[:5]:
        print(f"  [{q.get('type')}] {q.get('question')}")

    if args.offline_only:
        return 0

    key = load_nous_api_key()
    if not key:
        print("\nNo API key found (checked env + .env) — live path skipped.")
        return 1

    print("\n--- LIVE LLM questions ---")
    t0 = time.monotonic()
    questions = gen.generate(context, summary, None)
    elapsed = time.monotonic() - t0
    for q in questions:
        print(f"  [{q.get('type')}] {q.get('question')}")
        print(f"     concepts: {q.get('expected_concepts')}")
    print(f"\nLive generation: {len(questions)} questions in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
