# Enforcement Policy — Bypasses, Agents, and Delivery (Judas)

The design assumption, confirmed against OWASP DevSecOps guidance and gitleaks
docs: client-side hooks are advisory by nature. `--no-verify` skips
pre-commit/commit-msg/pre-push; a fresh clone runs nothing until hooks are
installed. Nothing installed on a developer's workstation can be relied on as
an enforcement boundary. The answer is to make bypass an audit event, not a
hole: the central ledger (service/, see CENTRALIZED_DATABASE_ARCHITECTURE.md
§9) doubles as a proof-of-defense record, and CI checks it.

## Actor taxonomy and how each is handled

| Actor | What happens | Where it's enforced |
|---|---|---|
| Human, `--no-verify` | The hook never runs, so nothing is recorded — by definition the pushed commit has no session row. CI proof-of-defense check fails it. | CI required check / pre-receive |
| Human, GUI with skip-toggle (VS Code "smart commit", Tower, Fork) | Same class as --no-verify. GUIs shell out to git, so when the hook does run it works normally; the skip path is identical to --no-verify. | Same |
| Agent (Codex/Claude CLI/etc.) | The hook FIRES (agents use git), but no human is present. `UNDERSTANDING_AGENT_MODE=agent` — or an unreachable TTY — records the session as `agent-unverified` / `unattended-unverified` and exits 0. Zero LLM calls. The credential gate still hard-blocks (agents are unverified, not exempt). | Ledger row + CI policy on top |
| Fresh clone / hooks never installed | No session rows at all. CI proof-of-defense flags every commit. | CI required check |
| Credential in the diff | Gate blocks regardless of actor class — including agent mode. | In the hook, first |

## Proof-of-defense check

At push/PR time, for each commit C in the range: the session that defends C
was recorded BEFORE C existed (pre-commit), so it is matched by its PARENT:
session.head_commit == parent(C) AND status == PASSED.

`scripts/verify_defense.py` implements this for both contexts:

CI (GitHub Actions) — fail the check for unaudited commits:

    - name: Proof of defense
      run: |
        python scripts/verify_defense.py \
          --ledger https://understanding.internal:8000 \
          --repo "$GITHUB_REPOSITORY" \
          --range "origin/${{ github.base_ref || 'main' }}..HEAD" \
          --token "$LEDGER_TOKEN"

Pre-receive (self-hosted git only — shipped as a snippet, not a product):

    # .git/hooks/pre-receive  (server side; cannot be skipped without server access)
    while read old new ref; do
      python /opt/understanding/verify_defense.py \
        --ledger http://ledger.internal:8000 \
        --repo "$(basename "$PWD" .git)" \
        --range "$old..$new" --token "$LEDGER_TOKEN" || exit 1
    done

Escape valve (or the tool dies at the first production incident): a hotfix
label / break-glass flag — `--allow-missing` prints a loud UNAUDITED banner
and exits 0, so the bypass stays visible in the CI log; the retro belongs in
the review of that PR. On managed platforms (github.com et al.) pre-receive
is not available — the required status check IS the enforcement point, plus
platform-native push protection for secret-shaped content as the backstop.

## Delivery verdict: hybrid (the weighing, answered)

The oral defense is irreducibly local and interactive — a server cannot ask a
developer questions at push time with acceptable latency. A centralized-only
design degrades to either deterministic checks at push (gitleaks + CI with
extra steps — loses the entire product thesis) or async question
emails/Slack (kills the commit-time moment). Therefore:

- The pre-commit hook STAYS as the question-delivery mechanism (UX + first
  line of defense).
- The central service owns what can centralize: the ledger, enforcement
  (above), and later config distribution (team standards served centrally).

## Rejected alternative: system-wide git-command interception

"Detect that a `git commit` runs anywhere and trigger a centralized flow" is
technically possible — a wrapper script on PATH (shell profile prepend) can
intercept `git` invocations and open a web page — and it is rejected:

- Unenforceable: absolute-path git (`/usr/bin/git commit`), alternate shells,
  libgit2-based tools, and agents all bypass a PATH shim. You cannot mandate
  every developer's shell configuration.
- Fragile: breaks under tooling assumptions (git wrappers, hub/cli shims,
  version managers), and gives no attestation that the defense actually ran —
  the same weakness as trusting any client-side signal.
- The ledger check delivers the same visibility (which commits were never
  defended) with none of the fragility, and it is the only version that
  catches --no-verify humans, GUI skippers, and fresh clones in one place.

## Agent-mode contract (first-class unverified actors)

- Explicit: `UNDERSTANDING_AGENT_MODE=agent` (preferred — predictable,
  documented, testable).
- Automatic: no interactive stdin reachable (no TTY and no /dev/tty) → same
  record-and-pass path with `verification=unattended-unverified`.
- Output is loud (RECORD-ONLY MODE banner); the session row carries the
  verification value; CI policy decides what agent rows require on top (e.g.
  a human review on the PR — the defense check accepts them with
  `--allow-agent-unverified` but prints them as unaudited-by-a-human).
- Policy principle: agents don't cheat the system — they are a new actor
  class. Model them, don't fail them.

## Packaging (pinnable revs)

An enforcement story needs a pinnable revision: consumers pin the released
tag in their .pre-commit-config.yaml (`rev: v0.4.0`), never a mutable branch.
`rev: main` is dev-only (used by the demo target repo). Releases are git
tags on main; the version in setup.py/pyproject.toml moves with them.
