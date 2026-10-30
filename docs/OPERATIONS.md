# Operations Guide

Install, configuration, deployment, enforcement wiring, testing, and
troubleshooting — for platform engineers and team leads. Grounded in v0.4.0.

## 1. Installation (solo developer)

```bash
# Target repo
pip install -e /path/to/understanding-agentHook   # or from git at a pinned tag
cp .env.example .env    # fill UNDERSTANDING_AGENT_API_KEY (or NOUS_API_KEY)
```

`.pre-commit-config.yaml` in the target repo:

```yaml
repos:
-   repo: https://github.com/Lizasatasiya/understanding-agentHook.git
    rev: v0.4.0          # pin a tag; rev: main is dev-only
    hooks:
    -   id: understanding-agent
    # optional: loud second chance when the hook is not installed (pre-push)
    -   id: understanding-agent-push-guard
```

```bash
pre-commit install
```

Notes:
- Python ≥ 3.10 required (system python3 on macOS is 3.9 — use a newer one).
- pre-commit snapshots the hook repo at the pinned rev: after editing the
  hook repo, commit + `pre-commit clean && pre-commit install-hooks` in the
  target, or you are testing stale code.
- Zero dependencies for the core path; `[voice]` and `[ui]` extras exist for
  dictation and the local dashboard.

## 2. Configuration reference

All variables are documented in `.env.example`. The hook reads `.env` from
the TARGET repo root at commit time.

LLM core:

| Variable | Default | Meaning |
|---|---|---|
| UNDERSTANDING_AGENT_PROVIDER | nous | nous/groq/openai/openrouter/together/ollama/custom |
| UNDERSTANDING_AGENT_MODEL | preset default | any model id |
| UNDERSTANDING_AGENT_BASE_URL | preset default | any OpenAI-compatible endpoint |
| UNDERSTANDING_AGENT_API_KEY | — | provider key (else preset default var, e.g. NOUS_API_KEY) |
| UNDERSTANDING_AGENT_JUDGE_MODEL | = model | separate judge model |
| UNDERSTANDING_AGENT_FAIL_MODE | open | closed = block commits when LLM unreachable |
| UNDERSTANDING_AGENT_DISABLE_THINKING | on | suppress reasoning tokens (nous) |
| UNDERSTANDING_AGENT_MASTER_ANALYSIS | off | 1/on/true enables the single-pass analysis |

Behavior:

| Variable | Default | Meaning |
|---|---|---|
| UNDERSTANDING_AGENT_SECURITY_GATE | on | off disables the credential gate (not recommended) |
| UNDERSTANDING_AGENT_MODE | — | agent = record-only mode (skip defense, record unverified) |
| UNDERSTANDING_AGENT_ANSWER_HYGIENE | on | off disables the suspect-capture re-ask |
| UNDERSTANDING_AGENT_MAX_SESSIONS | 500 | local store prune cap (<2 disables) |

Sync (teams):

| Variable | Default | Meaning |
|---|---|---|
| UNDERSTANDING_AGENT_SYNC_URL | — | central ledger BASE url; enables queue-backed sync |
| UNDERSTANDING_AGENT_TEAM_KEY | — | X-Team-Token header |
| UNDERSTANDING_AGENT_TEAM_SECRET | — | HMAC-SHA256 signing secret (X-Signature) |
| UNDERSTANDING_AGENT_TELEMETRY_URL | — | LEGACY single-endpoint telemetry (no queue) |

## 3. Central ledger deployment (teams)

Dev (SQLite, zero Docker):

```bash
pip install -r service/requirements.txt
SERVICE_TEAM_TOKEN=dev-token uvicorn service.app:app --port 8000
```

Production (Postgres via compose):

```bash
cd service
SERVICE_TEAM_TOKEN=... SERVICE_TEAM_SECRET=... SERVICE_ADMIN_TOKEN=... \
  docker compose up -d     # Postgres 16 + the service, schema auto-created
```

Service env: `SERVICE_DATABASE_URL` (sqlite:/// or postgresql+psycopg://),
`SERVICE_TEAM_TOKEN` (required on ingestion when set), `SERVICE_TEAM_SECRET`
(enables HMAC verification), `SERVICE_ADMIN_TOKEN` (org-structure endpoints),
`SERVICE_RETENTION_DAYS` (default 90 — enforced automatically on every
service startup; POST /api/v1/admin/purge with X-Admin-Token enforces it on
demand for cron).

Org wiring: `POST /api/v1/admin/repositories {repo_slug, org, team}` and
`POST /api/v1/admin/members {user_email, org, team}` with X-Admin-Token.
Everything else JIT-provisions on first sight.

Smoke: `curl http://<host>:8000/api/v1/health` → `{"status":"ok","schema_version":1}`.

## 4. Enforcement wiring (proof of defense)

CI (GitHub Actions):

```yaml
- name: Proof of defense
  env:
    LEDGER_TOKEN: ${{ secrets.LEDGER_TOKEN }}
  run: |
    python scripts/verify_defense.py \
      --ledger https://understanding.internal:8000 \
      --repo "$GITHUB_REPOSITORY" \
      --range "origin/${{ github.base_ref || 'main' }}..HEAD"
```

Self-hosted git (pre-receive — cannot be skipped without server access):

```bash
# .git/hooks/pre-receive
while read old new ref; do
  python /opt/understanding/verify_defense.py \
    --ledger http://ledger.internal:8000 \
    --repo "$(basename "$PWD" .git)" --range "$old..new" || exit 1
done
```

Policy flags: `--strict` (llm-verified only), `--allow-agent-unverified`
(accept AI-agent rows, require human review in the PR),
`--allow-unattended-unverified`, `--allow-missing` (break-glass: loud
UNAUDITED banner + exit 0 — use for hotfixes; the bypass stays visible in
the CI log). Matching semantics: a commit is audited iff a PASSED session
exists with `head_commit == parent(commit)`.

## 5b. Support matrix (manual checklist)

Client coverage as of v0.4.x — terminal git is first-class; the rest are
expected to work via the same mechanisms but are NOT yet exercised by an
automated matrix. Manual checklist for GUI clients (VS Code integrated git,
Tower, Fork): shell out to git → hook fires; the TTY prompt renders in the
integrated terminal; skip toggles behave like --no-verify (ledger gap, CI
flags). Voice: macOS only (mac_dictation or whisper); Windows dictation and
Linux speech paths exist in code but are untested — prefer typing there.

## 6. Voice

macOS is the first-class platform. Backends, in detection order
(interaction.py): macOS system dictation (keystroke simulation, no extra
install) → whisper+sounddevice → whisper+pyaudio (`pip install -e ".[voice]"`,
plus ffmpeg). Questions are spoken via `say`; `UNDERSTANDING_AGENT_VOICE=off`
silences TTS. [Tab] starts dictation; the transcript lands in the typing
buffer so you can edit before Enter. Suspect captures (clipped mic, stutter)
get one "say it again" prompt; mixed-language answers are never flagged.

## 7. Measurement

| Command | What it proves |
|---|---|
| `python scripts/calibration_harness.py [--strict]` | master-analysis precision/recall vs the 4-case corpus (bench-first before any prompt/model change) |
| `python scripts/context_eval_harness.py --strict` | context quality gate: % of expected concepts present in the built context (CI-enforced ≥0.80; current baseline 1.00) |
| `understanding-agent-sync-history --repo . --server URL [--dry-run]` | backfill existing local sessions into the ledger (legacy rows get git-author resolution; batches of 25; fail-loud) |
| `understanding-agent-push-guard` | install health check used by the pre-push hook (exit 1 + remediation when the pre-commit hook is missing) |
| `python scripts/bench_llm_latency.py` etc. | latency benches — the gateway varies 3–15s; never conclude from a single call |

Use python ≥ 3.10 (macOS system python3 is 3.9 and crashes at import).

## 8. Local dashboard (solo)

`understanding-agent-ui` (needs `[ui]` extra) serves the local dashboard on
127.0.0.1:8000 — commit history, attempts, per-question breakdowns, reading
the local SQLite store. The team/central dashboard is deferred (P4).

## 9. Troubleshooting

| Symptom | Cause → fix |
|---|---|
| Hook output looks stale after editing the hook repo | pre-commit runs the COMMITTED rev → commit, then `pre-commit clean && pre-commit install-hooks` in the target |
| "NOUS_API_KEY is not set" warning, generic questions | key not found in env or target-repo `.env` → set it; offline fallback ran by design (session recorded llm-unverified) |
| Commit exits 0 with "RECORD-ONLY MODE" | intended: non-interactive context (agent/CI) or UNDERSTANDING_AGENT_MODE=agent; the session is recorded unverified |
| Sessions missing after v0.4 upgrade | legacy JSON was imported into `.git/understanding_sessions.db` on first run; the old file is kept as `understanding_sessions.json.imported` |
| Ledger rows missing for offline periods | by design: queued in `.git/understanding_sync_queue.json`, drained on the next commit; check the queue file if it never drains (wrong SYNC_URL / token) |
| verify_defense says UNAUDITED for a commit you defended | the defense session must have status PASSED and head_commit == the commit's PARENT; agent rows additionally need `--allow-agent-unverified` |
| LLM slow (multi-second) | bench first (scripts/bench_*), repeat ≥3; the gateway legitimately varies 3–15s — do not tune prompts from a single sample |
| A real-looking key in a fixture does not block | check tier: short/low-entropy keys are placeholder-shaped by design; real vendor formats block everywhere, generic/entropy tiers skip test/doc paths |

## 10. Data & privacy operations

- Local store: `.git/understanding_sessions.db` (0600). Export: 
  `from understanding_agent.session_store import export_sessions`.
- Retention enforcement on the ledger: schedule
  `purge_expired_answers(db, retention_days)` — raw answers older than the
  window are nulled, scores kept.
- What leaves the machine: diff content → your configured LLM provider only;
  session records (questions/answers/scores/identity) → only if sync or
  telemetry is configured. Raw diffs are never in session payloads.
