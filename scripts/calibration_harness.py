#!/usr/bin/env python3
"""Calibration harness for the Master Analysis Pass.

Bench-first discipline: every prompt/model change to change_intelligence
must be measured against this corpus before it ships. "Calibrated" is a
number here, not an adjective.

Each corpus case is a directory under the corpus root containing:
    diff.txt    — the added lines of a commit (the harness builds a fake
                  structured_changes context from it)
    case.json   — {"file": "...", "expected": ["security", ...], "notes": "..."}
                  `expected` lists the finding CATEGORIES the analysis should
                  surface (precision/recall are computed per category; an
                  expected list of [] means the diff should produce NO
                  findings — the false-positive control cases).

Run with a real API key:
    python3.14 scripts/calibration_harness.py
Flags:
    --corpus DIR   corpus root (default scripts/calibration_corpus)
    --dry-run      validate fixtures + context building without any API call
    --strict       exit non-zero if precision < 0.6 or recall < 0.6
"""
import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from understanding_agent.change_intelligence import run_master_analysis  # noqa: E402
from understanding_agent.api_utils import load_nous_api_key  # noqa: E402


def load_cases(corpus_root: str):
    cases = []
    for name in sorted(os.listdir(corpus_root)):
        case_dir = os.path.join(corpus_root, name)
        if not os.path.isdir(case_dir):
            continue
        diff_path = os.path.join(case_dir, "diff.txt")
        meta_path = os.path.join(case_dir, "case.json")
        if not (os.path.exists(diff_path) and os.path.exists(meta_path)):
            print(f"[skip] {name}: missing diff.txt or case.json")
            continue
        with open(diff_path, encoding="utf-8") as fh:
            diff_text = fh.read()
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        cases.append((name, meta, diff_text))
    return cases


def build_context(meta: dict, diff_text: str) -> dict:
    return {
        "file_summary": [{"file": meta.get("file", "case.py"), "functions": ["changed"]}],
        "structured_changes": [{
            "file": meta.get("file", "case.py"),
            "function": "changed",
            "diff": diff_text,
        }],
    }


def run_case(name: str, meta: dict, diff_text: str, has_key: bool):
    context = build_context(meta, diff_text)
    if not has_key:
        return None, context
    result = run_master_analysis(context, {"what_changed": meta.get("notes", "")})
    return result, context


def evaluate(meta: dict, result) -> dict:
    """Per-category precision/recall against expected labels."""
    expected = set(meta.get("expected", []))
    if result is None:
        predicted = set()
        degraded = True
    else:
        predicted = {f["category"] for f in result.get("findings", [])}
        degraded = False
    tp = len(expected & predicted)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    return {
        "expected": sorted(expected),
        "predicted": sorted(predicted),
        "tp": tp, "fp": fp, "fn": fn,
        "degraded": degraded,
        "verdict": ("PASS" if not fn and not fp else
                    "MISS" if fn else
                    "FP" if fp else "PASS"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=os.path.join(REPO_ROOT, "scripts", "calibration_corpus"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    cases = load_cases(args.corpus)
    if not cases:
        print(f"No cases found in {args.corpus}")
        return 1
    api_key = load_nous_api_key()
    has_key = bool(api_key) and not args.dry_run
    print(f"Calibration corpus: {len(cases)} cases | "
          f"{'DRY RUN (fixtures + context only)' if args.dry_run else ('live LLM' if has_key else 'NO API KEY — degradation check only')}")

    tp = fp = fn = 0
    results = []
    for name, meta, diff_text in cases:
        result, _context = run_case(name, meta, diff_text, has_key)
        ev = evaluate(meta, result)
        results.append((name, ev))
        tp += ev["tp"]; fp += ev["fp"]; fn += ev["fn"]
        flag = "OK " if ev["verdict"] == "PASS" else "!! "
        print(f"  {flag}{name:28s} expected={ev['expected']} predicted={ev['predicted']} -> {ev['verdict']}")

    precision = (tp / (tp + fp)) if (tp + fp) else 1.0
    recall = (tp / (tp + fn)) if (tp + fn) else 1.0
    print(f"\nAggregate over {len(cases)} cases: precision={precision:.2f} recall={recall:.2f} (tp={tp} fp={fp} fn={fn})")
    if args.dry_run:
        print("Dry run: fixtures and context construction validated.")
        return 0
    if not has_key:
        print("No API key: all cases correctly degraded (analysis is a no-op without the LLM).")
        return 0
    if args.strict and (precision < 0.6 or recall < 0.6):
        print("STRICT: below calibration threshold.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
