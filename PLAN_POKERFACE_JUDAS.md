# PLAN_POKERFACE_JUDAS.md — Pokerface (Ops) & Judas (Packaging/Delivery)

Status: **implemented**. Live end-to-end runs covered hook → service → ledger,
offline queue + drain, and the verify_defense audited/bypassed/break-glass
matrix. See the bottom of this file for the implementation report.

## Decisions (resolved at approval)

- **A (LLM security review trigger)**: keep the master-analysis shape — the
  security/standards LLM review is FOLDED into the single master call that also
  produces classification + questions (verified against 60f67ff: findings are
  citation-verified advisory, EvidenceCollector hints already flow into the
  prompt via `_hint_lines`, injection constraints enforced in code). No
  separate call, no extra gating — the one-call cost cap is already satisfied.
  P2 scope shrinks to: policy doc + deterministic-evidence visibility.
- **B (local tier)**: SQLite locally (`.git/`, 0600) + Postgres centrally.
  Plaintext JSON readable-by-anyone was overruled; JSON stays as an export
  format only. Retention/prune cap applies to the SQLite store.
- **C (agent mode)**: both — explicit `UNDERSTANDING_AGENT_MODE=agent` env var
  preferred, non-TTY auto-detect as safety net.
- P3b (central service) stays in scope — the managerial side and the Judas
  ledger both depend on it. Built on Liza's
  CENTRALIZED_DATABASE_ARCHITECTURE.md, integrated/expanded per P3.

Prerequisite state from the Bad Romance round (already landed, 60f67ff):
single versioned session-payload builder (`session_payload.py`, schema_version=2,
verification REQUIRED), master analysis pass (`change_intelligence.py` —
classification + citation-verified advisory findings + checklist questions),
calibration harness, judge/model split. The 4 red evaluator tests are fixed;
main is green.

This plan executes WORKSTREAMS_ASSESSMENT.md (Pokerface + Judas sections) and
adopts Liza's CENTRALIZED_DATABASE_ARCHITECTURE.md with the amendments in P3.

---

## Governing design rules

1. Deterministic decides blocks; LLM only generates judgment-call questions and
   advisory findings. The credential gate NEVER consults the LLM (attacker-
   controlled diff content reaching a prompt is an injection surface).
2. The client hook is the question-delivery mechanism. Centralization covers
   the ledger, enforcement, and config — never the oral defense itself.
3. The central service must never block or measurably slow a commit:
   offline-first, ~2.5s sync budget, fail-silent, queue-and-drain.
4. Frozen behavior unchanged: 75/70/25 thresholds, 25–70% follow-up band,
   2–8 question scaling, 80/20 diff/standards ratio, per-type time limits.
5. Answers are developer-performance data (HR-adjacent). The privacy/retention
   stance ships WITH the storage upgrade, not after — per-developer score
   views default OFF, team opt-in.

---

## P1 — Voice dictation robustness (multilingual: HANDLE, never punish)

Bug class being fixed: a transcription artifact that scores as a wrong answer
aborts the commit — dictation failure currently punishes the developer.

- New `understanding_agent/answer_hygiene.py`: pure-Python checks on the
  transcribed/typed answer before scoring — empty, single-token, and
  mixed-script heuristic (Devanagari vs Latin vs CJK detectable without deps).
  Suspicious → offer "say it again / type it" (one retry), never score garbage.
- Evaluator prompt multilingual clause (Layer 2): the answer may mix languages
  or use transliterated terms; judge concept coverage only, never language,
  grammar, or spelling. Survives even imperfect transcription.
- Discard policy: reserved ONLY for the un-transcribable case (the P1 retry);
  mixed-language answers remain scorable via Layer 2 — discarding angers users
  for no quality gain.
- Cloud code-switching ASR backend: REJECTED for V1 (research: MUSCAT
  arXiv 2604.15929 — code-switching ASR is an open problem; best commercial
  pairs still ~8% WER). Robust evaluation beats chasing transcription quality.

Files: `interaction.py`, `answer_evaluator.py`, new `answer_hygiene.py`, tests.

## P2 — LLM access policy doc + evidence-gated security/best-practice review

- New `docs/LLM_POLICY.md`: the governing rule extended and written down —
  which LLM calls see diff content; injection hardening (diff delimited and
  declared DATA, never instructions; LLM output reaches only validated
  Pydantic finding models, structurally never `sys.exit` or the gate's
  finding list); findings advisory-only; diff_hash caching; offline degrades
  to deterministic-only; cost cap (≤1 extra LLM call on the normal path);
  zero calls on the gate path (invariant, keep).
- Implementation: EvidenceCollector output (semgrep/gitleaks/test gaps) and
  the standards report are wired into the master-analysis prompt as
  "deterministic flags to elaborate into judgment-call questions" — gated on
  flags existing (OPEN DECISION A: evidence-gated vs every commit;
  recommend gated — cheaper, findings anchored in a deterministic first
  opinion).

Files: `docs/LLM_POLICY.md`, `change_intelligence.py`, `cli.py`, tests.

## P3 — Session storage upgrade: identity, org contracts, sync, central service

Answers "how does per developer / per team / per repo work":

**Identity (client)** — new `understanding_agent/identity.py`
(IdentityDetector, from Liza's doc): `user_email` (primary key, git config),
`user_name`, `system_user`, `machine_id` (hashed hostname — correlation only,
no raw hostnames), `repo_slug` + `git_platform` (parsed from remote), `ci_actor`.
Attached to every payload; session schema_version bumps to 3.

**Org hierarchy (server)** — Liza's schema (users/repositories/sessions/
session_questions) gains one layer: `organizations` → `teams` →
`team_members (user_id, team_id)`, and `repositories.team_id`. Resolution
chain per session: email → user (JIT), repo_slug → repo (JIT) → team → org.
Per-developer = user_id; per-team = team_id; per-repo = repo_id. All queries
funnel through these FKs — no free-text attribution anywhere.

**Server contract** — `service/` (FastAPI + SQLAlchemy + docker-compose for
local Postgres):
- `POST /api/v1/sessions` — JIT user/repo provisioning, idempotent on
  session_id, atomic transaction; every response carries schema_version.
- `POST /api/v1/sessions/batch` — offline-queue drain.
- `GET /api/v1/defense?repo=&from=&to=` — proof-of-defense lookup for
  CI/pre-receive (J3): commit/hash/status/verification rows only.

**Client sync** — `server_client.py` upgrade: attach identity, 2.5s timeout,
on failure append to `.git/understanding_sync_queue.json`, drain the queue
FIRST on every subsequent run, batch POST. Commit never blocked by sync
(invariant; failures are one log line).

**Anti-spoofing** — V1: shared team token (`UNDERSTANDING_AGENT_TEAM_KEY`)
plus HMAC-SHA256 over the canonical payload with
`UNDERSTANDING_AGENT_TEAM_SECRET`; server verifies both. Ceiling stated in
the doc: this proves "someone holding the team secret", not "this developer
answered honestly" — full per-dev attestation is not worth it pre-adoption.
Optional server-side email-domain allowlist.

**Privacy/retention (ships with this, not later)** — written into the amended
doc: raw answer text retained N days (default 90, configurable), aggregates
kept indefinitely; per-developer score trends default OFF; individual drill-down
views are team opt-in. Without this stance the adoption dies in an HR review.

**Local tier** — OPEN DECISION B: JSON-only locally (YAGNI; Postgres central)
vs SQLite locally. Recommend JSON-only.

Files: `identity.py`, `server_client.py`, `session_payload.py` (v3), `cli.py`,
`service/*`, `docker-compose.yml`, tests; commit
CENTRALIZED_DATABASE_ARCHITECTURE.md with an AMENDMENTS section carrying the
org layer, privacy stance, and HMAC scheme.

## J1 — CI

The 20fb32a regression sat red for 8 days because nothing watches main. The
ledger is the bypass-detection seed.

## J2 — Agent / non-interactive commits (first-class actor class)

Today an agent literally cannot commit through a repo with this hook:
`/dev/tty` EOF → empty answer → abort <25%. That is an adoption blocker.

- `UNDERSTANDING_AGENT_MODE=agent` → skip questions, record
  `verification: "agent-unverified"`, exit 0 with a loud unverified marker.
- Non-TTY stdin (no controlling terminal) as a fallback auto-trigger to the
  same record-and-pass path (OPEN DECISION C: env-var only vs env var +
  auto-detect; recommend both — an agent that doesn't set the var gets
  record-and-pass instead of a hard abort).
- The ledger then shows the commit as agent-made; the J3 defense check can
  require a human review record on top of any agent row.

## J3 — Enforcement & delivery: policy doc + proof-of-defense + packaging

`docs/ENFORCEMENT.md` + `scripts/verify_defense.py`:

- **Bypass taxonomy**: `--no-verify` humans → cannot prevent client-side;
  make it visible. A pushed commit with no PASSED session row in the ledger
  is by definition unaudited. Agents → J2. GUI clients → hooks fire normally
  (human present); risks are cosmetic (support matrix, not architecture).
  Fresh clones / never-installed hooks → no session rows → CI flags it.
- **CI proof-of-defense**: on PR/push, for each commit in the range call
  `GET /api/v1/defense`; missing PASSED row → required check fails. Escape
  valve (hotfix label + post-hoc retro) or the tool dies at the first
  production incident. Self-hosted git: pre-receive variant shipped as a
  documented snippet, not a product.
- **Delivery verdict (the weighing)**: hybrid. The oral defense is
  irreducibly local and interactive — a server cannot ask questions at push
  time with acceptable latency; a centralized-only design degrades to
  deterministic checks (gitleaks/CI with extra steps, losing the product
  thesis) or async question emails (kills the commit-time moment). The hook
  stays as question delivery; the central service takes the ledger,
  enforcement, and (later) config distribution.
- **System-wide `git commit` detection**: REJECTED. A PATH shim/wrapper CAN
  intercept `git` invocations system-wide (shell profile prepend), but it is
  unenforceable (absolute-path git, alternate shells, libgit2-based tools,
  agents all bypass it), fragile under tooling assumptions, and provides no
  attestation that the defense actually ran. The ledger check gives the same
  visibility with none of the fragility. Documented in ENFORCEMENT.md as the
  rejected alternative.
- **Packaging**: tag `v0.4.0`; README documents pinned-rev consumption
  (`rev: <tag>`), `rev: main` marked dev-only. An enforcement story needs a
  pinnable rev; consumers cannot adopt from a mutable main.

---

## Out-of-scope table (with reasons)

| Item | Reason |
|---|---|
| Managerial dashboards / per-dev showcase (P4) | depends on central DB + privacy stance; deferred, low priority (agreed) |
| docs/ARCHITECTURE.md flow docs | Bad Romance remainder; write after this lands to avoid documenting twice |
| Cloud code-switching ASR ([voice] extra) | open research problem; evaluation robustness instead |
| Custom ASR fine-tuning | off-mission |
| Windows/Linux voice paths | darwin-first today; support matrix documented, not built |
| SQLite local store | YAGNI unless user overrules (OPEN DECISION B) |
| Embedding/semantic context retrieval | rejected for V1 (assessment) |
| Pre-receive product support | documented snippet only |
| Thresholds / scaling / ratio changes | frozen by explicit user instruction |

## Phase order (risk-ordered; each phase lands green)

1. J1 CI (watches every subsequent phase)
2. P1 voice hygiene + evaluator multilingual clause
3. J2 agent mode
4. P2 policy doc + evidence-gated elaboration
5. P3a identity + payload v3 + HMAC + queue + ServerClient (client side)
6. P3b central service (FastAPI/Postgres, docker-compose local, JIT)
7. J3 enforcement doc + verify_defense.py + tag v0.4.0

## Estimated line delta

~+2,300 / −120 across ~20 files
(service/ ~600 incl. tests; identity + client sync ~300; docs ~800;
P1 ~150; J2 ~150; remaining wiring/tests ~300).

---

## Implementation report

CHANGES (before → after):

1. CI (J1): nothing watched main (a regression once sat red 8 days).
2. Voice answer hygiene (P1): a transcription artifact scored as a wrong
   answer and could abort the commit → suspect captures (empty,
   single-token, ASR stutter) get ONE optional re-ask before scoring
   (Enter submits as-is); mixed-language answers are NEVER flagged.
   New answer_hygiene.py; wiring in cli.py; kill switch
   UNDERSTANDING_ANSWER_HYGIENE=off. The evaluator multilingual clause was
   already present (last session) — verified, not rebuilt.
3. Agent / non-interactive mode (J2): an agent could not commit at all
   (TTY EOF → abort <25%) → UNDERSTANDING_AGENT_MODE=agent (explicit) or
   unreachable TTY (auto) records verification=agent-unverified /
   unattended-unverified, exits 0 with a loud banner; the credential gate
   still hard-blocks; zero LLM calls. cli.py 5e + _interactive_input_available.
4. LLM policy (P2): the policy existed only as code comments →
   docs/LLM_POLICY.md (tier table, injection hardening, evidence rules,
   cost discipline, guard-map) + deterministic tool evidence (semgrep/
   gitleaks) now displayed and recorded, not just fed to prompts. Decision A:
   security review stays folded into the single master-analysis call —
   no separate/gated call (verified against 60f67ff first).
5. Session prune bug (found by J2 e2e): prune ranked by attempt_number
   first, so a new attempt-1 session could be pruned in favor of old
   high-attempt rows → timestamp-first ranking (session_store.py), with a
   regression test.
6. Local store (P3, decision B): plaintext JSON readable by anyone →
   SQLite .git/understanding_sessions.db (0600), one-time legacy-JSON
   import with .imported backup, export_sessions for JSON portability,
   identical public API. Plain JSON is no longer written on any path.
7. Zero-touch identity (P3): sessions carried no developer identity →
   IdentityDetector (email primary key, hashed machine_id, repo_slug,
   platform, ci_actor) attached to every payload; schema_version 3 adds
   identity + evidence.
8. Central sync (P3): telemetry was a fire-and-forget single POST →
   queue-backed sync (UNDERSTANDING_AGENT_SYNC_URL, X-Team-Token,
   HMAC-SHA256 X-Signature, 2.5s budget, .git/understanding_sync_queue.json
   drained as batch on next run). Commit never blocked by sync. Legacy
   UNDERSTANDING_AGENT_TELEMETRY_URL unchanged.
9. Central service (P3b): nothing existed → service/ (FastAPI +
   SQLAlchemy; SQLite dev / Postgres prod via docker-compose): JIT user/
   repo provisioning, idempotent ingestion, batch drain, /api/v1/defense
   proof-of-defense rows, admin org/team wiring, retention purge
   (raw answers nulled after 90 days, aggregates kept). Liza's doc committed
   with an AMENDMENTS section (§9) documenting every delta + privacy stance.
10. Enforcement (J3/J1): bypasses were invisible → docs/ENFORCEMENT.md
    (taxonomy, hybrid verdict, system-wide-detection rejection + reasons)
    + scripts/verify_defense.py (CI/pre-receive proof-of-defense;
    --strict / --allow-agent-unverified / --allow-missing break-glass).
11. Packaging: rev: main → consumers pin v0.4.0; setup.py/pyproject gain
    [service] extra; README ledger/enforcement section.

UNCHANGED (frozen): 75/70/25 thresholds, 25–70% follow-up band, 2–8 question
scaling, 80/20 ratio, per-type time limits; gate ordering and zero-LLM-on-
block invariants; master-analysis prompt/checklist contracts.

Verification evidence:
- Agent mode records and passes.
- The credential gate still blocks in agent mode, with gate output only.
- Live end-to-end (uvicorn service + scratch git repo): hook in agent mode
  → 200 on /api/v1/sessions → /api/v1/defense shows the row with git
  identity; service stopped → session queued (0600); service restarted →
  next hook run drained the batch; 3/3 rows in the ledger, queue empty.
- verify_defense.py live matrix: audited commit with agent rows and no
  flag → rc=1 (human review required); with --allow-agent-unverified →
  rc=0; true --no-verify bypass → rc=1 UNAUDITED; --allow-missing →
  rc=0 with loud break-glass banner.

Deferred (known, deliberate): managerial dashboard views (P4 — needs the
privacy stance live first), docs/ARCHITECTURE.md flow doc, Windows/Linux
voice support matrix, per-developer attestation above the team-secret
ceiling, pre-receive as a shipped product (snippet only).
