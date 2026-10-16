#!/usr/bin/env python3
"""Context evaluation harness (Bad Romance item 2 — measurement FIRST).

Replays the REAL deterministic pipeline (ChangeDetector → CodeGraph →
ContextBuilder) on fixture repos and measures the only number that matters:
what fraction of each case's expected concepts actually appear in the context
that would be sent to the question generator. Context work without this
measurement is vibes.

Per case: scratch git repo → commit base → stage the modified files → build
context → check each expected concept (case-insensitive substring against the
serialized context). No LLM, fully deterministic, safe in CI.

Usage:
  python3.14 scripts/context_eval_harness.py            # report
  python3.14 scripts/context_eval_harness.py --strict  # exit 1 under 0.80
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CORPUS = Path(__file__).resolve().parent / "context_eval_corpus"
DEFAULT_MIN = 0.80


def _run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"git failed ({cmd}): {r.stderr}")


def run_case(case_dir: Path) -> dict:
    manifest = json.loads((case_dir / "manifest.json").read_text())
    scratch = Path(tempfile.mkdtemp(prefix=f"ctxeval-{manifest['name']}-"))
    try:
        _run(["git", "init", "-q"], scratch)
        _run(["git", "config", "user.email", "eval@test.local"], scratch)
        _run(["git", "config", "user.name", "eval"], scratch)

        # Base commit
        base_dir = case_dir / "base"
        for f in base_dir.rglob("*"):
            if f.is_file():
                dest = scratch / f.relative_to(base_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(f, dest)
        _run(["git", "add", "-A"], scratch)
        _run(["git", "commit", "-q", "-m", "base"], scratch)

        # Staged modification
        staged_dir = case_dir / "staged"
        for f in staged_dir.rglob("*"):
            if f.is_file():
                dest = scratch / f.relative_to(staged_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(f, dest)
        _run(["git", "add", "-A"], scratch)

        # Real pipeline (git subprocesses inside the scratch repo)
        old_cwd = os.getcwd()
        os.chdir(scratch)
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from understanding_agent.change_detector import ChangeDetector
            from understanding_agent.code_graph import CodeGraph
            from understanding_agent.context_builder import ContextBuilder

            changes = ChangeDetector().detect()
            graph = CodeGraph()
            graph.build(changes.get("files") or [])
            context = ContextBuilder().build(changes, graph)
        finally:
            os.chdir(old_cwd)

        serialized = json.dumps(context, default=str).lower()
        missing = [c for c in manifest["expected_concepts"]
                   if c.lower() not in serialized]
        total = len(manifest["expected_concepts"])
        coverage = (total - len(missing)) / total if total else 1.0
        # context stats for the report
        n_changes = len(context.get("structured_changes", []))
        diff_lines = sum(len((c.get("diff") or "").splitlines())
                         for c in context.get("structured_changes", []))
        return {
            "name": manifest["name"],
            "coverage": coverage,
            "missing": missing,
            "expected": total,
            "structured_changes": n_changes,
            "diff_lines": diff_lines,
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help=f"exit 1 if aggregate coverage < {DEFAULT_MIN}")
    parser.add_argument("--min", type=float, default=DEFAULT_MIN)
    args = parser.parse_args()

    cases = sorted(d for d in CORPUS.iterdir() if d.is_dir() and (d / "manifest.json").exists())
    if not cases:
        raise SystemExit(f"no cases found in {CORPUS}")

    results = []
    for case in cases:
        r = run_case(case)
        results.append(r)
        status = "PASS" if r["coverage"] >= args.min else "FAIL"
        print(f"[{status}] {r['name']}: coverage {r['coverage']:.2f} "
              f"({r['expected'] - len(r['missing'])}/{r['expected']} concepts) "
              f"| {r['structured_changes']} fn diffs, {r['diff_lines']} lines")
        for m in r["missing"]:
            print(f"         missing: {m}")

    aggregate = sum(r["coverage"] for r in results) / len(results)
    print(f"\nAggregate context coverage: {aggregate:.2f} over {len(results)} cases "
          f"(threshold {args.min})")
    if args.strict and aggregate < args.min:
        print("CONTEXT EVAL FAILED: below threshold — context selection regressed.")
        return 1
    print("CONTEXT EVAL PASSED" if aggregate >= args.min else "CONTEXT EVAL FAILED")
    return 0 if aggregate >= args.min else 1


if __name__ == "__main__":
    sys.exit(main())
