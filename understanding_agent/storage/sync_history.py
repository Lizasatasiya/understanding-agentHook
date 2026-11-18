"""Backfill CLI: push existing local
sessions to the central ledger.

    understanding-agent-sync-history [--repo PATH] [--server URL] [--dry-run]

- There is no local session database. This command reports an empty batch.
- Sends in batches of 25 to {server}/api/v1/sessions/batch using the same
  team-token/HMAC transport as the hook (server_client).
- --dry-run reports what would be sent without any network calls.

A failure to reach the server exits 1 with a clear message (this is an
explicit operator command, not a commit path).
"""

import argparse
import os
import sys

BATCH_SIZE = 25


def collect_backfill_sessions(repo_root: str) -> list:
    return []


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
