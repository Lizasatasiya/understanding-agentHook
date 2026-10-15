#!/usr/bin/env python3
"""Proof-of-defense verifier (Judas J3) — CI / pre-receive against the ledger.

A commit is AUDITED when the central ledger holds a defense session recorded
against its PARENT commit (pre-commit sessions are recorded before the commit
hash exists: session.head_commit == parent(C)) with status PASSED.

Exit codes:
  0  every commit in the range is audited (or explicitly allowed)
  1  at least one commit is unaudited
  2  usage / ledger unreachable (fail-safe visible: treat like unaudited in CI)

Usage:
  verify_defense.py --ledger URL --repo ORG/REPO --range OLD..NEW [--token T]
                    [--allow-missing] [--allow-agent-unverified]
                    [--allow-unattended-unverified] [--strict]

Policy flags:
  --allow-missing                break-glass: print loud UNAUDITED banners, exit 0
  --allow-agent-unverified       accept agent-mode rows (commit made by an AI
                                 agent; require human review in the PR instead)
  --allow-unattended-unverified  accept non-interactive rows (CI/bots)
  --strict                       require llm-verified rows only
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request

OK_VERIFICATIONS = {"llm-verified", "llm-unverified"}  # a human defended


def _commits_in_range(rev_range: str) -> list:
    """Return [{hash, parents}] for each commit in the range, oldest first."""
    out = subprocess.run(
        ["git", "log", "--reverse", "--pretty=%H %P", rev_range],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise SystemExit(f"verify_defense: git log failed for {rev_range!r}: "
                         f"{out.stderr.strip()}")
    commits = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if parts:
            commits.append({"hash": parts[0], "parents": parts[1:]})
    return commits


def _fetch_defense(ledger: str, repo: str, token: str) -> dict:
    url = f"{ledger.rstrip('/')}/api/v1/defense?repo_slug={repo}"
    req = urllib.request.Request(url)
    if token:
        req.add_header("X-Team-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise SystemExit(f"verify_defense: ledger unreachable ({exc}) — "
                         "treating as unaudited; fix connectivity or use "
                         "--allow-missing for a declared hotfix.")


def _accepted(record: dict, args) -> tuple:
    """Return (accepted, note). A row defends its parent commit."""
    verification = record.get("verification", "")
    if record.get("status") != "PASSED":
        return False, f"status={record.get('status')}"
    if args.strict:
        return verification == "llm-verified", f"verification={verification}"
    if verification == "agent-unverified":
        return bool(args.allow_agent_unverified), \
            f"agent-made commit ({verification}) — require human review"
    if verification == "unattended-unverified":
        return bool(args.allow_unattended_unverified), \
            f"non-interactive commit ({verification})"
    return verification in OK_VERIFICATIONS, f"verification={verification}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ledger", required=True, help="central service base URL")
    parser.add_argument("--repo", required=True, help="repo slug, e.g. acme/billing")
    parser.add_argument("--range", dest="rev_range", required=True,
                        help="git rev range, e.g. origin/main..HEAD or old..new")
    parser.add_argument("--token", default=os.environ.get("LEDGER_TOKEN", ""),
                        help="team token for the ledger (default $LEDGER_TOKEN)")
    parser.add_argument("--allow-missing", action="store_true",
                        help="break-glass: loud banner + exit 0 for unaudited commits")
    parser.add_argument("--allow-agent-unverified", action="store_true")
    parser.add_argument("--allow-unattended-unverified", action="store_true")
    parser.add_argument("--strict", action="store_true",
                        help="require llm-verified defense rows only")
    args = parser.parse_args()

    commits = _commits_in_range(args.rev_range)
    if not commits:
        print("verify_defense: empty range — nothing to verify")
        return 0

    data = _fetch_defense(args.ledger, args.repo, args.token)
    rows = data.get("sessions", [])
    by_parent = {}
    for r in rows:
        by_parent.setdefault(r.get("head_commit", ""), []).append(r)

    unaudited = []
    for commit in commits:
        parents = commit["parents"]
        defending = []
        for parent in parents:
            defending.extend(by_parent.get(parent, []))
        accepted_rows = []
        notes = []
        for r in defending:
            ok, note = _accepted(r, args)
            if ok:
                accepted_rows.append(r)
            else:
                notes.append(note)
        if not accepted_rows:
            unaudited.append((commit["hash"], notes
                              or ["no defense session for parent commit"]))

    for h, notes in unaudited:
        print(f"UNAUDITED {h[:12]}: " + "; ".join(sorted(set(notes))))
    if unaudited:
        if args.allow_missing:
            print(f"\n⚠ BREAK-GLASS: {len(unaudited)} commit(s) unaudited but "
                  "--allow-missing is set. This bypass is now a visible audit "
                  "event — record the retro on the PR.")
            return 0
        print(f"\n✗ {len(unaudited)}/{len(commits)} commit(s) lack a recorded "
              "defense. Commit with the hook installed (never --no-verify), or "
              "use the documented break-glass flag for a hotfix.")
        return 1

    print(f"✓ All {len(commits)} commit(s) in {args.rev_range} have a recorded "
          "defense in the ledger.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
