# Architecture — Technical Reference

Written against v0.4.0 (commit 82cee04). Line counts and behavior verified
against the tree; anything marked DEFERRED is not implemented today.

## 1. System shape

Three deployable pieces, one pipeline:

```
┌──────────────────────────────┐         ┌──────────────────────────┐
│ TARGET REPO (developer mac)  │         │ CENTRAL LEDGER (team)    │
│                              │         │                          │
│  git commit                  │  sync   │  service/ (FastAPI +     │
│   └─ pre-commit hook         │ ──────► │   SQLAlchemy, Postgres)  │
│       understanding-agent    │  queue  │   JIT users/repos        │
│        (understanding_agent/)│         │   proof-of-defense API   │
│                              │         └───────────▲──────────────┘
│  .git/understanding_sessions.db (local SQLite)    │ CI check
│  .git/understanding_agent_state.json              │
│  .git/understanding_sync_queue.json (offline)     │
└──────────────────────────────┘         scripts/verify_defense.py
```

- **Hook package** (`understanding_agent/`, ~12k lines incl. UI): runs at
  commit time; zero hard dependencies (voice/UI/service are extras).
- **Central ledger** (`service/`, ~600 lines): optional; ingests sessions,
  serves the defense-check API.
- **Verification tool** (`scripts/verify_defense.py`): runs in CI or as a
  server-side pre-receive hook; pure stdlib.

## 2. The commit-time pipeline (cli.py, exact order)

```
git commit
 └─► 1  EnvironmentDetector.detect()        repo path, language, framework
     2  ChangeDetector.detect()             staged files, changed functions,
     │                                     is_large_change (>100 LOC | >3 fns | >2 files)
     3  CodeGraph.build()                   regex per-file symbol graph
     │                                     (language_support.py: py/js/ts/…)
     4  ContextBuilder.build()              BUDGETED per-function diffs:
     │                                     ranked selection (changed lines kept
     │                                     first, structural next, filler dropped
     │                                     first; 35-line/fn default, 240-line
     │                                     total), function details, dependency
     │                                     summaries; nested JS/TS handlers pull
     │                                     the enclosing component preamble
     5  ChangeKind.classify_change()         deterministic classification
     │                                     {ui, data_model, control_flow,
     │                                     infra_config, tests, docs} weights
     │                                     + auditable signals → attached to
     │                                     context; printed after the audit;
     │                                     both question paths consume it
     5b SecurityLens.profile_change()       security profile → question hints
     │  EvidenceCollector.collect_evidence() semgrep/gitleaks/test-gaps (if tools present)
     │  PracticePacks.hint()                 stack-specific question topics
     │  IdentityDetector.resolve()          zero-touch git identity
     ▼
     6  CREDENTIAL GATE (deterministic, FIRST)
     │     vendor key patterns · entropy gate (≥20 chars, ≥3.5 bits/char,
     │     ≥3 char classes on secret-named vars) · connection strings ·
     │     staged key files by name (example/sample stems exempt) ·
     │     escapes: `# pragma: allow-secret`, `# example`, gate=off
     │     BLOCK → gate output only, exit 1, ZERO LLM calls
     ▼
     7  Coding Standards Audit (deterministic, 15 changed-line checks)
     │  Tool Evidence block (semgrep/gitleaks findings, advisory, printed)
     ▼
     8  RECORD-ONLY CHECK (Judas J2)
     │     UNDERSTANDING_AGENT_MODE=agent OR no interactive terminal reachable
     │     → record session as agent-unverified / unattended-unverified,
     │       exit 0 (gate + audit above still ran; zero LLM calls)
     ▼
     9  Standards fix/proceed prompt (only if violations)
     │     "fix" → session FAILED recorded, exit 1 · "proceed" → continue
     ▼
     10 ChangeSummary.generate()            LLM summary (deterministic fallback offline)
     ▼
     11 QUESTION GENERATION
     │     a) Master Analysis (opt-in, UNDERSTANDING_AGENT_MASTER_ANALYSIS=1):
     │        ONE LLM call → classification (domain weights over
     │        {ui, data_model, control_flow, infra_config, tests, docs},
     │        change_type, rationale) + advisory findings (security/standards,
     │        severity 1–10, MUST cite a diff file or dropped deterministically,
     │        top 3 kept) + questions born with weighted checklists + difficulty
     │     b) Legacy path: micro/macro prompts built from context + hints
     │        (stack, security, evidence, practices, standards), 80/20
     │        diff/standards question ratio, 2–8 questions by diff scale
     │        (small 2–3 / medium 3–5 / large 6–8), types from TIME_LIMITS
     │     Both paths validate every question (type, checklist, concepts).
     ▼
     12 DEFENSE LOOP (per question)
     │     print + speak (`say`) + [Tab] dictation/mic option
     │     timed input (30–60s per type, see TIME_LIMITS)
     │     Answer hygiene: suspect capture (empty/single-token/ASR stutter)
     │       → ONE optional re-ask, Enter submits as-is; mixed-language NEVER flagged
     │     Evaluation (see §4) → score
     │     25 ≤ score < 70 with follow-up required → one focused follow-up
     │       question on the missing concept; final = max(original, follow-up)
     │     score < 25 → commit aborted (session FAILED recorded, exit 1)
     ▼
     13 SCORING & PERSISTENCE
           per-question pass ≥ 70 · overall pass > 75 · abort < 25
           build_session_payload (schema v3, verification REQUIRED)
           save_session → .git/understanding_sessions.db (SQLite, 0600, prune 500)
           ServerClient.send → central ledger (queue if offline) or legacy telemetry
```

Retries: attempt state lives in `.git/understanding_agent_state.json`
(attempt number, cached questions keyed by diff_hash). A retry of the same
diff reuses cached questions; questions already answered with ≥70 show
"Already Answered"; failed questions get an LLM-rephrased variant. Attempt
numbers are scoped to the current HEAD commit cycle.

## 3. Module map (contracts)

| Module | LOC | Input → Output | Failure mode |
|---|---|---|---|
| cli.py | 783 | orchestrates everything | sys.exit codes: 1 block/abort, 0 pass/record-only |
| hook_stages.py | 95 | gate-block/advisory printers, interactive probe (phase-9 extraction) | pure presentation |
| change_kind.py | 190 | changes+context → typed classification {weights, dominant, signals} | no signals → dominant "" (unknown), never raises |
| change_detector.py | 270 | git index → {files, structured_changes, is_large_change} | unknown extensions still tracked (gate invariant) |
| code_graph.py / language_support.py | 103/191 | files → symbol graph, function extraction | regex-based; AST-quality extraction DEFERRED |
| context_builder.py | 320 | changes+graph → context with BUDGETED diffs (ranked selection: changed lines kept, structural next, filler dropped; 35-line/function default cap, 240-line total budget; nested JS/TS handlers extend to the enclosing component's preamble) | truncate-with-priorities; measured by scripts/context_eval_harness.py |
| security_lens.py | 511 | added diff lines → critical findings (gate) + profile (hints) | gate has documented exemptions; generic tiers skip test/doc paths |
| evidence_collector.py | 168 | staged files → semgrep/gitleaks/test-gap findings | tools absent → empty evidence, hook continues |
| coding_standards.py | 766 | changes → 15 deterministic checks; standards DOC (repo `.understanding/standards.md` or default prose) | changed-line scoped by design |
| practice_packs.py | 252 | stack → question topic packs | unknown stack → universal topics |
| stack_detector.py | 246 | repo → language/frameworks/domains | — |
| environment_detector.py | 153 | cwd → env info | — |
| identity.py | 82 | repo → zero-touch identity dict | no email → `user@host.local` fallback |
| change_summary.py | 139 | context → {what_changed, impact,…} | offline → deterministic summary |
| question_generator.py | 935 | context+summary+hints → questions | no key → offline fallback pool (still asks the security question when the lens flagged) |
| change_intelligence.py | 290 | context+summary → master analysis dict | ANY malformed LLM output → None → legacy path |
| checklists.py | 195 | question.checklist → validated Checklist; offline verify; banding | invalid checklist → derived or dropped |
| answer_evaluator.py | 449 | question+answer → EvaluationResult | no key/LLM fail → deterministic fallback (fail-open) |
| followup_generator.py | 102 | question+answer+eval → focused follow-up | LLM fail → deterministic rephrase |
| answer_hygiene.py | 93 | answer text → (suspect, reason) | kill switch env var |
| interaction.py | 839 | prompts → typed/dictated answers | mac_dictation or whisper (sounddevice/pyaudio); TTS via `say` |
| session_payload.py | 92 | fields → schema-v3 session dict (single source of schema) | verification is a REQUIRED arg |
| session_store.py | 547 | session dicts ↔ `.git/understanding_sessions.db` | sqlite unusable → legacy JSON fallback; JSON import once (`.imported` backup) |
| server_client.py | 149 | session → central ledger / queue | any failure → queue, commit continues |
| sync_history.py | 120 | local store → backfill batches to the ledger (understanding-agent-sync-history) | legacy rows get git-author resolution; fail-loud (operator command, not a commit path) |
| push_guard.py | 95 | repo → install health ({healthy, reason}) | pre-push aid: exit 1 + remediation steps when the pre-commit hook is missing |
| api_utils.py | 371 | LLM transport: retries, key loading (.env + env), JSON extraction | 200-with-null-content treated as retryable |
| llm_manager.py | 244 | provider presets (nous/groq/openai/openrouter/together/ollama/custom) | legacy NOUS_*/GROQ_* vars honored via compat layer |
| commit_context.py | 237 | git plumbing: head, commit msg from parent process args, diff_hash, attempt_key, attempt numbering | — |
| evaluation_models.py | 43 | EvaluationResult wrapper | — |
| server.py + ui/ | 692+ | LOCAL dashboard (FastAPI on 127.0.0.1:8000): /ui, /api/commits, /attempts, telemetry ingest | reads local store; central dashboard DEFERRED |
| service/ (app, models) | ~700 | central ledger (see §6) | — |

## 4. Evaluation protocol (Checklist-Based Verification)

Default path for newly generated questions. Each question carries a
**checklist**: 2–4 weighted key points (weight 1–3), each with tiered rubric
scores (descending, including 0). Validated by `checklists.parse_checklist`
(schema-enforced).

- The judge (model set by `UNDERSTANDING_AGENT_JUDGE_MODEL`, deliberately
  separable from the generator to avoid self-preference bias) answers with a
  **rationale first**, then per-item tier picks, plus a 0–100 clarity score.
- The 0–100 score is a **deterministic aggregation** (`aggregate_score`) of
  awarded tiers × weights + clarity — never the LLM's own number.
- Out-of-rubric or malformed judge output degrades to the offline fallback.
- Offline fallback for checklist questions: `verify_offline` — stopword-
  filtered content-word overlap per key point; banded 85+ substantive /
  70-band / 76 baseline / dismissive low (frozen contract).
- Frozen thresholds (user-mandated, do not change): overall pass > 75,
  per-question pass ≥ 70, abort < 25, follow-up band 25–70, question count
  2–8 by scale, 80/20 diff/standards ratio, per-type time limits.
- Judge prompt is explicitly multilingual: concept coverage only, never
  language/grammar/spelling.

## 5. Local data (all under .git/)

| File | Format | Purpose |
|---|---|---|
| understanding_sessions.db | SQLite, 0600 | session history (prune: most recent 500 by timestamp, attempt tiebreak) |
| understanding_agent_state.json | JSON | attempt/answer state across retries, cached questions per diff_hash |
| understanding_sync_queue.json | JSON, 0600, ≤500 | sessions queued while the ledger was unreachable |
| understanding_sessions.json(.imported) | legacy | pre-v0.4 JSON store, imported once, kept as backup |

Session schema v3 (built ONLY by `session_payload.build_session_payload`):
`schema_version:3, session_id, attempt_id, attempt_number, commit_id
("staged" pre-commit), head_commit, commit_message, attempt_key, diff_hash,
repository, branch, timestamp, status (PASSED/FAILED), verification
(llm-verified | llm-unverified | agent-unverified | unattended-unverified),
score, changed_files, change_summary, standards_report, violations_count,
questions[], analysis? (master classification+findings), identity?
(zero-touch), evidence? (deterministic tool findings — never raw diffs)`.

## 6. Central ledger (service/)

Schema (SQLAlchemy; SQLite dev, Postgres prod):

```
organizations 1..* teams 1..* team_members *..* users
repositories.team_id → teams.id
sessions(session_id UNIQUE, user_id FK, repo_id FK, …, JSON fidelity columns)
session_questions(session_row_id FK, question/answer/score/eval, answer_retained)
```

API (all responses carry `schema_version`):

| Endpoint | Auth | Behavior |
|---|---|---|
| GET /api/v1/health | — | liveness |
| POST /api/v1/sessions | X-Team-Token (+ X-Signature HMAC-SHA256 over canonical JSON if secret set) | JIT user/repo provisioning from payload identity; idempotent upsert by session_id |
| POST /api/v1/sessions/batch | same | drains the offline queue shape `{"sessions":[…]}` |
| GET /api/v1/defense?repo_slug&from&to | — | proof-of-defense rows (commit/diff hash, status, verification, email) |
| POST /api/v1/admin/repositories | X-Admin-Token | assign repo → org/team |
| POST /api/v1/admin/members | X-Admin-Token | add user → team |

Retention: `purge_expired_answers` nulls raw answer text older than
SERVICE_RETENTION_DAYS (default 90); scores/aggregates kept forever.

Matching semantics for enforcement: a session defends a commit iff
`session.head_commit == parent(commit)` and `status == PASSED` — pre-commit
sessions are recorded before the commit hash exists.

## 7. Failure-mode invariants (all test-enforced)

- Gate block → exit 1, gate output only, zero LLM calls (e2e scene 1).
- No LLM key / API down → offline fallback questions, session marked
  llm-unverified, commit proceeds (fail-open; `UNDERSTANDING_AGENT_FAIL_MODE=closed`
  reverses this).
- Malformed master-analysis output → deterministic legacy path (silent).
- Ledger unreachable → session queued locally, commit proceeds; queue
  drained next run.
- SQLite unusable → legacy JSON store; commit proceeds.
- Non-interactive context → record-only pass with loud unverified banner.
- Prune is timestamp-first (attempt_number alone was a bug — new attempt-1
  sessions were pruned over old high-attempt rows).

## 8. Measurement

- `scripts/calibration_harness.py` + 4-case corpus: precision/recall gate
  (≥0.6 strict) for master-analysis changes; first live measurement
  1.00/1.00 (qwen3-coder-30b, Nous gateway).
- `scripts/context_eval_harness.py` + 5-case corpus: context quality gate —
  % of expected concepts present in the built context, replaying the REAL
  detector/graph/builder on scratch repos (strict gate at
  ≥0.80; first measurement 1.00 after it caught a real React-extraction
  gap — nested handlers excluded their component's useState preamble).
- `scripts/bench_*.py`: LLM latency/capture-anomaly benches (Nous gateway
  varies 3–15s per call; repeat ≥3 before concluding anything).

## 9. Known deferred items

Tree-sitter/AST extraction and repo map (build only if the context eval
harness shows the regex tier failing on real repos; a known measured gap:
unchanged SIBLING functions are not in context — "compare with the sibling"
questions need repo-map support), OIDC per-developer auth (D9), central
dashboard UI (colleague), voice beyond darwin, Windows/Linux voice paths.
Done in the completion pass (see PLAN_V1_COMPLETION.md): context eval
harness, token-budgeted selection, deterministic ChangeKind tier + frozen
matrix, tiered redaction, violation wire-format single-source, backfill CLI,
retention enforcement (startup + admin endpoint), push guard, cli helper
extraction (hook_stages.py).
