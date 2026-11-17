"""Versioned session payload builder — the single source of the session schema.

Replaces the three near-identical session_data dict constructions that used
to live inline in cli.py (fix-violations, abort, and normal-completion paths).
Those three had already drifted apart — the fix-violations path silently
omitted the `verification` field — which is exactly the bug class a single
builder exists to prevent.

Every session record produced here carries:
  - schema_version: lets the server/dashboard migrate old payloads safely
  - verification: ALWAYS present — a fallback/offline pass is marked
    "llm-unverified", never silently omitted (the honesty invariant)

Schema v3 (Pokerface P3, PLAN_POKERFACE_JUDAS.md) adds:
  - identity: zero-touch developer/repo identity (understanding_agent/identity.py)
    — the central service JIT-provisions users/repos from it
  - evidence: deterministic tool findings (semgrep/gitleaks) recorded at
    commit time so the ledger can show WHY questions were asked
"""

import time

SCHEMA_VERSION = 3


def build_session_payload(
    *,
    attempt_number: int,
    head_commit: str,
    commit_message: str,
    attempt_key: str,
    diff_hash: str,
    repository: str,
    branch: str,
    status: str,
    score: float,
    changed_files: list,
    change_summary: dict,
    verification: str,
    standards_report: list | None = None,
    violations_count: int = 0,
    questions: list | None = None,
    analysis: dict | None = None,
    identity: dict | None = None,
    evidence: dict | None = None,
) -> dict:
    """Build one session record. All call sites go through here so the
    schema can never drift between paths again.

    verification is REQUIRED (no default): callers must state it —
    "llm-verified" only when questions were LLM-generated and every
    evaluation came from the LLM; "llm-unverified" otherwise (including the
    standards-blocked path, where no defense happened at all).

    identity/evidence are optional (older call paths and offline tests may
    omit them) but the interactive CLI always attaches both.
    """
    session_id = f"sess_{int(time.time() * 1000)}_{attempt_number}"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "attempt_id": session_id,
        "attempt_number": attempt_number,
        "commit_id": "staged",
        "head_commit": head_commit,
        "commit_message": commit_message,
        "attempt_key": attempt_key,
        "diff_hash": diff_hash,
        "repository": repository,
        "branch": branch,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": status,
        "verification": verification,
        "score": score,
        "changed_files": changed_files,
        "change_summary": change_summary,
        "standards_report": standards_report or [],
        "violations_count": violations_count,
        "questions": questions or [],
    }
    if analysis:
        payload["analysis"] = analysis  # master-analysis classification/findings, when present
    if identity:
        payload["identity"] = identity  # zero-touch identity (IdentityDetector)
    if evidence:
        # record only the deterministic tool findings, never raw diff content
        payload["evidence"] = {
            "semgrep": evidence.get("semgrep") or [],
            "gitleaks": evidence.get("gitleaks") or [],
            "test_coverage_gaps": evidence.get("test_coverage_gaps") or [],
        }
    return payload
