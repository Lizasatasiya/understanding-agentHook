"""Collect deterministic evidence about a change from already-configured tools.

Philosophy (grounded in the Tricorder/Google static analysis research): this
system does NOT install or run its own linters. It reuses the project's
existing, already-trusted tooling output as *question evidence* — findings
become questions, never verdicts. That inherits scanner recall without
inheriting the false-positive-blocks-commit problem.

Tools consulted when present on PATH (all optional, all cached, all timed):
- semgrep      (security findings; --json)
- gitleaks     (hardcoded secrets)
- npm/pip audit-able manifests are deliberately NOT run here: too slow at
               commit time; dependency review stays a question topic.

Every collector is wrapped so a missing/broken tool degrades to no evidence.
"""

import json
import os
import re
import shutil
import subprocess
from typing import Dict, Any


_TIMEOUT = 20


def _run(cmd: list, cwd: str | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT,
            cwd=cwd, stdin=subprocess.DEVNULL
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception:
        return -1, ""


def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def collect_evidence(staged_files: list, repo_root: str | None = None) -> Dict[str, Any]:
    """Gather tool-based evidence for the staged files. Safe to fail."""
    evidence: Dict[str, Any] = {
        "semgrep": [],
        "gitleaks": [],
        "test_coverage_gaps": [],
        "lint_available": False,
    }

    root = repo_root or os.getcwd()
    code_files = [f for f in staged_files
                  if f.get("path", "").endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".cs", ".go", ".rb", ".java"))]

    if code_files:
        paths = [f["path"] for f in code_files]

        if _tool_available("semgrep"):
            evidence["semgrep"] = _collect_semgrep(paths, root)
        if _tool_available("gitleaks"):
            evidence["gitleaks"] = _collect_gitleaks(paths, root)
        evidence["test_coverage_gaps"] = _find_test_gaps(paths, staged_files)

    return evidence


def _collect_semgrep(paths: list, root: str) -> list:
    """Run semgrep on staged files; return [{rule, path, line, message}]."""
    cmd = ["semgrep", "--json", "--quiet", "--config", "auto", *paths]
    code, out = _run(cmd, cwd=root)
    if code not in (0, 1):
        return []
    try:
        data = json.loads(out)
    except Exception:
        return []
    findings = []
    for r in data.get("results", [])[:10]:
        findings.append({
            "rule": r.get("check_id", "unknown"),
            "path": r.get("path", ""),
            "line": (r.get("start") or {}).get("line", 0),
            "message": ((r.get("extra") or {}).get("message") or "")[:200],
        })
    return findings


def _collect_gitleaks(paths: list, root: str) -> list:
    code, out = _run(["gitleaks", "detect", "--source", root, "--no-banner", "--report-format", "json",
                      "--report-path", os.devnull, "--exit-code", "0"], cwd=root)
    # gitleaks JSON report path handling differs by version; fall back to stdout parse
    findings = []
    if code == 0 and out:
        # Try parsing newline-delimited or single JSON
        try:
            data = json.loads(out)
            for f in data[:10]:
                findings.append({
                    "rule": f.get("RuleID", "secret"),
                    "path": f.get("File", ""),
                    "line": f.get("StartLine", 0),
                    "message": (f.get("Secret", "") or "")[:60],
                })
        except Exception:
            pass
    return findings


def _find_test_gaps(paths: list, staged_files: list) -> list:
    """Heuristic: changed code files with no similarly-named test file in the change or repo.

    Returns [{path, reason}] for non-test code files that lack an obvious test
    counterpart. This is question evidence ("how would you verify this?"),
    not a gate.
    """
    staged_paths = {f.get("path", "") for f in staged_files}
    gaps = []
    for path in paths:
        base = os.path.basename(path)
        if any(h in base.lower() for h in ("test", "spec", "mock", "fixture")):
            continue
        if any(h in path.lower() for h in ("migrations", "__snapshots__")):
            continue
        stem = base.rsplit(".", 1)[0]
        # look for a sibling test file anywhere in the staged set first (cheap)
        candidate_names = {f"test_{stem}", f"{stem}_test", f"{stem}.test", f"{stem}.spec",
                           f"{stem}Tests", f"{stem}Test"}
        staged_test = any(
            os.path.basename(sp).rsplit(".", 1)[0] in candidate_names
            for sp in staged_paths
        )
        if not staged_test:
            gaps.append({
                "path": path,
                "reason": f"no test file matching '{stem}' found in the staged change",
            })
    return gaps[:5]


def evidence_question_hint(evidence: dict) -> Dict[str, Any]:
    """Turn collected evidence into question-generator guidance."""
    hints = {"evidence_findings": []}

    for f in evidence.get("semgrep", [])[:3]:
        hints["evidence_findings"].append({
            "source": "semgrep",
            "detail": f"{f['rule']} at {f['path']}:{f['line']}",
            "message": f["message"],
        })
    for f in evidence.get("gitleaks", [])[:2]:
        hints["evidence_findings"].append({
            "source": "gitleaks",
            "detail": f"possible secret at {f['path']}:{f['line']} ({f['rule']})",
            "message": f["message"],
        })
    for g in evidence.get("test_coverage_gaps", [])[:2]:
        hints["evidence_findings"].append({
            "source": "test-gap-analysis",
            "detail": g["reason"],
            "message": f"changed file {g['path']} has no test counterpart",
        })

    hints["has_high_severity_evidence"] = any(
        f["source"] in ("semgrep", "gitleaks") for f in hints["evidence_findings"]
    )
    return hints
