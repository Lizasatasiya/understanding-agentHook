"""Backfill CLI (Pokerface P3 — colleague's doc Phase 4): push existing local
sessions to the central ledger.

    understanding-agent-sync-history [--repo PATH] [--server URL] [--dry-run]

- Reads the local SQLite store (session_store.load_sessions).
- Legacy rows without an identity block get their author resolved from git
  (`git show -s --format=%ae|%an` against the recorded commit) when possible.
- Sends in batches of 25 to {server}/api/v1/sessions/batch using the same
  team-token/HMAC transport as the hook (server_client).
- --dry-run reports what would be sent without any network calls.

Never mutates the local store. A failure to reach the server exits 1 with a
clear message (this is an explicit operator command, not a commit path —
fail-loud is correct here, unlike the hook).
"""

import argparse
import os
import subprocess
import sys

BATCH_SIZE = 25


def _resolve_author_git(repo_root: str, session: dict) -> dict:
    """Best-effort identity for legacy rows: git author of the recorded
    commit (head_commit, or the reconciled commit_id when present)."""
    identity = dict(session.get("identity") or {})
    if identity.get("user_email"):
        return identity
    commit = ""
    if session.get("commit_id") and session["commit_id"] != "staged":
        commit = session["commit_id"]
    elif session.get("head_commit"):
        commit = session["head_commit"]
    if commit:
        try:
            out = subprocess.check_output(
                ["git", "show", "-s", "--format=%ae|%an", commit],
                text=True, cwd=repo_root, stderr=subprocess.DEVNULL,
            ).strip()
            email, _, name = out.partition("|")
            if email:
                identity["user_email"] = email.lower().strip()
                identity.setdefault("user_name", name.strip())
        except Exception:
            pass
    if not identity.get("user_email"):
        # unverifiable attribution is recorded as such — never fabricated
        identity.setdefault("user_email", "unknown@local.dev")
        identity.setdefault("user_name", "Unknown (backfill)")
    return identity


def collect_backfill_sessions(repo_root: str) -> list:
    from .session_store import load_sessions
    sessions = load_sessions(repo_root)
    out = []
    for s in sessions:
        row = dict(s)
        row["identity"] = _resolve_author_git(repo_root, s)
        out.append(row)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Push existing local sessions to the central ledger.")
    parser.add_argument("--repo", default=".", help="target repo root (default cwd)")
    parser.add_argument("--server", default="",
                        help="central service base URL (default $UNDERSTANDING_AGENT_SYNC_URL)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be sent; no network calls")
    args = parser.parse_args()

    if args.server:
        os.environ["UNDERSTANDING_AGENT_SYNC_URL"] = args.server

    sessions = collect_backfill_sessions(args.repo)
    print(f"Local sessions found: {len(sessions)}")

    if args.dry_run:
        for s in sessions[:5]:
            print(f"  would send: {s.get('session_id')} "
                  f"({(s.get('identity') or {}).get('user_email')})")
        if len(sessions) > 5:
            print(f"  ... and {len(sessions) - 5} more")
        return 0

    base = os.environ.get("UNDERSTANDING_AGENT_SYNC_URL", "").strip().rstrip("/")
    if not base:
        print("No server configured: pass --server or set "
              "UNDERSTANDING_AGENT_SYNC_URL.", file=sys.stderr)
        return 1

    from .server_client import _post
    sent = 0
    for i in range(0, len(sessions), BATCH_SIZE):
        batch = sessions[i:i + BATCH_SIZE]
        if _post(f"{base}/api/v1/sessions/batch", {"sessions": batch}):
            sent += len(batch)
            print(f"  batch {i // BATCH_SIZE + 1}: sent {len(batch)}")
        else:
            print(f"  batch {i // BATCH_SIZE + 1}: FAILED (server unreachable "
                  f"or rejected) — stopping; {sent} already sent.",
                  file=sys.stderr)
            return 1
    print(f"Backfill complete: {sent}/{len(sessions)} sessions in the ledger.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
