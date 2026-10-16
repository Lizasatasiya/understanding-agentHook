# Management Demo Runbook — Two Real Bugs, Two Real Defenses

Everything in this runbook was executed and verified end-to-end on this
machine (2026-10-16). Timings are real measurements, not estimates.

## The stories (pick per audience, or run both)

### Python — Textualize/rich
"The library that makes every Python CLI beautiful. Its `console.print()`
ignored the `end` parameter when you printed nothing: `console.print(end="!")`
emitted a newline instead of `!`. The maintainers fixed it (PR #4075, release
note: 'Fixed empty print ignoring the end parameter'). We implement that fix
ourselves — and our system makes the author defend it out loud before it
lands."

- Fix mirrored: `rich/console.py` (+3 net, the empty-objects branch) +
  `tests/test_console.py` (the upstream regression test).
- One-line bug repro: `Console.print(end="!")` → `"\n"` before, `"!"` after.

### JavaScript — axios
"axios, 100M+ weekly downloads. Reported as issue #6676: 'getUri ignores
paramsSerializer if it is a callback' — you configure how query strings are
built, axios silently ignores you and ships the default serialization.
Fixed by PR #6680 (released in 1.7.8): a 6-line change in
`lib/helpers/buildURL.js`. We implement it and defend it."

- Fix mirrored: `lib/helpers/buildURL.js` (function options wrapped as
  `{serialize: fn}`) + the upstream spec addition in
  `test/specs/helpers/buildURL.spec.js`.

## Prepared state (already on this machine)

| Item | Path | State |
|---|---|---|
| rich demo repo | ~/demo-repos/rich-demo | at pre-fix base 1fc7cb21; fix STAGED (console.py + test); .env copied; local identity set; ledger session from the dry run recorded |
| axios demo repo | ~/demo-repos/axios-demo | at pre-fix base df956d18 (v1.7.7 era); fix STAGED (buildURL.js + spec); .env copied; local identity set |
| demo probe | scripts/demo_probe.py | replays the real pipeline + live LLM; verified: rich 2 questions/13.8s, axios 2 questions/10.1s |
| central ledger | scratch db (demo) | start command below; verified agent-commit + defense-check matrix |

Both demo repos are real clones — never push them; everything is local.

## Interpreters (fixed once, 2026-10-16)

`python3` now resolves to Python 3.12 (uv-managed, ~/.local/bin/python3 —
verify `python3 --version` shows 3.12; if not, run
`uv python install 3.12 --default`). All commands below use `python3`.
The package needs >= 3.10; the old `python3` shell alias is broken
(permission denied) — do not use it.
The dashboard runs from the prepared venv ~/.understanding-demo-venv
(Python 3.12 + the [ui] extras, editable hook repo installed).

## Pre-flight (T-15 minutes, in your own terminal)

```bash
# 1. Warm + verify the live path per repo (expect ~10-15s, 2 questions)
cd ~/demo-repos/rich-demo  && python3 ~/understanding-agentHook/scripts/demo_probe.py
cd ~/demo-repos/axios-demo && python3 ~/understanding-agentHook/scripts/demo_probe.py

# 2. Start the central ledger (leave running in a side terminal)
cd ~/understanding-agentHook && mkdir -p /tmp/demo-ledger
SERVICE_DATABASE_URL=sqlite:////tmp/demo-ledger/ledger.db SERVICE_TEAM_TOKEN=demo-team \
  python3 -m uvicorn service.app:app --port 8766

# 3. (Optional but authentic) wire the hook via pre-commit in the demo repo
cd ~/demo-repos/rich-demo
cat > .pre-commit-config.yaml <<'YML'
repos:
-   repo: /Users/yash.c/understanding-agentHook
    rev: main
    hooks:
    -   id: understanding-agent
YML
pre-commit install
# If pre-commit is not available, use the manual path in Scene 2 (same behavior).
```

## Scene 0 — the bug (30 seconds, rich)

```bash
cd ~/demo-repos/rich-demo
git stash push -q -m fix   # hide the staged fix
python3 -c "import sys, io; sys.path.insert(0,'.'); from rich.console import Console; \
c=Console(file=io.StringIO()); c.print(end='!'); print('output:', repr(c.file.getvalue()))"
# → output: '\n'   ← the bug, live
git stash apply -q --index && git stash drop -q   # restore the staged fix
python3 -c "import sys, io; sys.path.insert(0,'.'); from rich.console import Console; \
c=Console(file=io.StringIO()); c.print(end='!'); print('output:', repr(c.file.getvalue()))"
# → output: '!'
```

(For axios, tell the story from issue #6676 + the codesandbox link — the
codesandbox repro needs a browser; the rich repro is fully in-terminal.)

## Scene 1 — the credential gate (60 seconds, either repo)

```bash
python3 - <<'EOF'
aws = "AKIA" + "IOSFODNN7" + "EXAMPLE"
with open("scratch_creds.py", "w") as f:
    f.write(f'import os\nAWS_ACCESS_KEY_ID = "{aws}"\n')
EOF
git add scratch_creds.py
git commit -m "oops"        # hook fires
# → "Commit blocked: credential detected in staged code." + unstage hints
# → exit 1, no commit, ZERO AI calls, sub-second
git restore --staged scratch_creds.py && rm scratch_creds.py
```
Speaker note: "deterministic, instant, un-spoofable by prompt content — no AI
anywhere in the block decision."

## Scene 2 — the live oral defense (the centerpiece, 3-5 minutes)

Pre-commit path (if wired): `git commit -m "fix: ..."` — the hook fires.
Manual path (identical behavior, no pre-commit needed):

```bash
export PYTHONPATH=~/understanding-agentHook
python3 -m understanding_agent.cli     # interactive: it reads your terminal
git commit --no-verify -m "fix: ..."     # complete the commit after passing
```

What the room sees (verified this session):
- `Change classification: tests (control_flow …) — signals: …` — the
  deterministic lens, printed and auditable.
- Coding Standards Audit — 15 deterministic checks over the changed lines.
- 2 LLM questions aimed at THIS fix (real examples captured live):
  - rich: "What happens to objects when print is called with no arguments
    and end='!'?" / "How does the new test verify the behavior of print
    with empty objects and custom end?"
  - axios: "What happens to the options parameter when it's a function in
    buildURL?" / "How does the custom serializer function get called in the
    test case?"
- Answer in your own words (type, or [Tab] for dictation — the hygiene
  re-ask shows if a capture looks clipped). Judge scores in seconds.
- Score >75% → commit lands. Say the line: "the author just proved they
  understand their own change — in front of the team, at the moment it
  matters."

Optional beat — the follow-up: answer one question vaguely; the system
asks a targeted follow-up on the missing concept and takes the max score.
Optional beat — the retry: miss badly once (<25%) → commit aborts → retry
shows "Already Answered" carry-over for what you did get right.

Latency expectations (measured): question generation 10-14s once; judge
~3-8s per answer. If the provider is down entirely, the hook degrades to
offline questions and still completes — say "fail-open, honestly marked
unverified".

## Scene 3 — agents commit too (90 seconds)

```bash
export UNDERSTANDING_AGENT_SYNC_URL=http://127.0.0.1:8766
export UNDERSTANDING_AGENT_TEAM_KEY=demo-team
export UNDERSTANDING_AGENT_MODE=agent            # = what an AI agent hits
python3 -m understanding_agent.cli           # via PYTHONPATH as above
# → "⚠ RECORD-ONLY MODE (agent-unverified): no oral defense performed."
git commit --no-verify -m "fix: ... (agent)"
```

## Scene 4 — the ledger closes the loop (90 seconds)

```bash
# what the team knows now (repo identity comes from the git remote — Textualize/rich)
curl "http://127.0.0.1:8766/api/v1/defense?repo_slug=Textualize/rich" | python3 -m json.tool

# CI's proof-of-defense check — default policy:
python3 ~/understanding-agentHook/scripts/verify_defense.py \
  --ledger http://127.0.0.1:8766 --repo "Textualize/rich" \
  --range "HEAD~1..HEAD" --token demo-team
# → "UNAUDITED ...: agent-made commit (agent-unverified) — require human review"  rc=1

# with the agent policy flag:
... --allow-agent-unverified
# → "✓ All 1 commit(s) ... have a recorded defense in the ledger."  rc=0
```

The closer: "--no-verify, GUI skippers, fresh clones, agents — none of them
can be prevented client-side, and none of them can hide: a pushed commit
with no defense record is unaudited by definition, and CI fails it. Bypass
becomes an audit event, not a hole."

## Scene 5 — the dashboard updates itself (90 seconds, great closer)

The local dashboard (colleague's real-time UI: per-user metrics, user
filtering, 8-second auto-refresh) watches the demo repo:

    # terminal 2 (leave running):
    UNDERSTANDING_AGENT_REPO_ROOT=~/demo-repos/rich-demo \
      ~/.understanding-demo-venv/bin/python -m understanding_agent.server
    # open http://127.0.0.1:8000/ui — leave it on the projector

With the dashboard open, run any scene above (agent commit, or the live
defense): the new commit and its attempts appear WITHIN 8 SECONDS without
touching the browser (verified live: a fresh commit appeared with author
attribution and attempt count; the per-user panel updated on its own).
Speaker beat: click a user in the left panel to filter — "the team sees
who defended what, per user, as it happens."


## Failure playbook (say this, then switch repo)

| What breaks | What you say | Recovery |
|---|---|---|
| Live LLM slow (>25s) | "gateway variance is documented; we bench it" | Answer the offline questions (fail-open path) — still a full demo |
| LLM unreachable | "the system is honest about degradation — no key, still works, marked unverified" | Continue with offline questions |
| Voice dictation garbles | "exactly the failure mode the hygiene layer exists for" | It re-asks; or just type |
| A question is off-topic | "judge model is separate from generator; scores concept coverage only" | Answer adjacent truthfully; follow-up targets the gap |

## After the demo — cleanup

```bash
cd ~/demo-repos/rich-demo  && git reset -q --soft HEAD~1 2>/dev/null; git stash list
# keep the repos staged for the next run; the ledger db is throwaway (/tmp)
```

## Why these two repos (if asked)

Recognizable names, real merged upstream fixes (not synthetic bugs), small
self-contained diffs (2-3 questions each — tight for a live room), and both
are the exact target persona: a developer changing someone else's mature
code and needing to prove comprehension, not correctness.
