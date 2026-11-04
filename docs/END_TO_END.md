# End-to-end working

Branch `server-database` (tip `a8dde17`). This is the commit-time oral defense: a git hook that blocks live credentials, audits changed lines, asks the author about the diff, scores the answers, and stores the attempt locally and on the central ledger.

Three processes:

| Process | Entry | What it talks to |
|---|---|---|
| Hook | `understanding-agent` → `understanding_agent.cli:main` | Git index, the configured LLM, local SQLite, ledger HTTP |
| Local dashboard | `understanding-agent-ui` → `understanding_agent.server:run` | `.git/understanding_sessions.db` |
| Central ledger | `service.app:create_app` (Docker, port 8088) | Postgres, and the same `understanding_agent/ui` files |

Python 3.10+ is required. If `python -m understanding_agent.cli` starts on an older interpreter, `cli.py` re-execs under a newer one (`_modern_python`) before any other import.

## Execution flow

### 0. Git starts the process

`install.sh` sets `git config --global core.hooksPath` and writes two hooks.

`pre-commit` chains a repo-local `pre-commit` when one exists and, if that hook succeeds, returns without running the agent. Otherwise it execs `understanding-agent`.

`prepare-commit-msg` chains a repo-local `prepare-commit-msg`, then always execs `understanding-agent`. Git still runs this stage after `--no-verify` skips `pre-commit`.

The pre-commit framework (`.pre-commit-hooks.yaml`) registers the same command on both stages, plus `understanding-agent-push-guard` on `pre-push`.

A passed defense writes `.git/understanding_agent_verified.json`. The second stage calls `is_already_verified_for_commit` and exits 0 when that badge is under 5 minutes old, or when SQLite has a `PASSED` row for the same HEAD and diff hash within the last hour.

### 1. `cli.main` before any question

| Step | Call | Exit |
|---|---|---|
| 1 | `_detect_no_verify()`, `_detect_agent_execution()` | — |
| 2 | Rebind stdin/stdout to `/dev/tty` (`CON` on Windows), unless an agent or `--no-verify` was already seen | — |
| 3 | `EnvironmentDetector.detect()` | repo root, name, branch |
| 4 | `ChangeDetector.detect()` | staged files and changed functions. Python via `ast`. Other languages via `language_support.extract_functions` / `functions_touching_lines`. Paths in `path_exclusions.EXCLUSION_GLOBS` (tests, generated code, lockfiles, vendor, build output) are dropped here |
| 5 | `security_gate_enabled()` then `SecurityLens.find_critical_findings` on an empty change set | exit 1 via `_print_gate_block` if a key *filename* is staged. File contents are not read for that check |
| 6 | No remaining files | exit 0 |
| 7 | `get_head_commit`, `get_current_commit_message`, SHA-256 of the change payload, `compute_attempt_key` | — |
| 8 | `is_already_verified_for_commit` | exit 0 |
| 9 | `_is_prepare_stage` and not verified | `_print_no_verify_block`, exit 1 |
| 10 | `StackDetector.detect` | languages, frameworks, domains from manifests |
| 11 | `CodeGraph.build` | symbols and caller/callee edges (`git grep`, Python `ast` for calls) |
| 12 | `ContextBuilder.build` | per-function diffs, 35 lines each, 240 lines total. Changed lines kept first |
| 13 | `classify_change` | weights over `ui`, `data_model`, `control_flow`, `infra_config`, `tests`, `docs` |
| 14 | `SecurityLens.profile_change` + `security_question_hint` | question hints only. Injection, XSS, auth, crypto, deserialization, command/path/SSRF, LLM-output trust, cleartext HTTP, disabled TLS, CORS, cookies |
| 15 | `collect_evidence` + `evidence_question_hint` | semgrep and gitleaks if installed, plus test-gap notes. Printed, never a block |
| 16 | `practice_pack_hint` | framework and domain topics (React, Nest, Express, FastAPI/Django/Flask, .NET, SQL, ML, pipelines, plus universal error and impact topics) |
| 17 | `IdentityDetector.resolve` | git email, name, origin, platform, repo slug, 12-hex hostname hash |
| 18 | `SecurityLens.find_critical_findings` on the real context | exit 1, no LLM. Blocks vendor token shapes, private keys, `user:pass@` URLs, and secret-named assignments of at least 20 chars with Shannon entropy ≥ 3.5 and 3 character classes. `# pragma: allow-secret`, `# example`, `# allow-secret` skip a line. Generic tiers skip tests, docs, and example/sample/template files. `UNDERSTANDING_AGENT_SECURITY_GATE=off` skips this step |
| 19 | `CodingStandardsChecker.check` + `print_report` | changed lines only: React mutation, hooks, effect cleanup, virtual DOM, JS `var`/`==`, swallowed errors, Python mutable defaults, bare `except`, missing context managers, Go discarded errors, functions over 80 lines, nesting over 4, missing boundary checks |
| 20 | `_detect_no_verify` again (env `UNDERSTANDING_AGENT_NO_VERIFY`, ancestor `git commit --no-verify` or `-n`, process tree, or `prepare-commit-msg` without a badge) | session `FAILED` / `no-verify-blocked`, `save_session`, `ServerClient.send`, exit 1 |
| 21 | `is_commit_formatting_only` and no failed standards | session `PASSED` / `formatting-exempt` / score 100, `mark_verified_for_commit`, exit 0. Covers formatting, import reorder, comments, docs, type annotations, non-code metadata, local cosmetic edits, debug-print cleanup, lockfiles, patch version bumps |
| 22 | `_detect_agent_execution` | session `FAILED` / `agent-blocked`, exit 1. Triggers: `UNDERSTANDING_AGENT_MODE=agent`, known agent env vars (Cursor, Claude Code, Copilot, Cline, Aider, Antigravity, and similar), or a matching ancestor process. `UNDERSTANDING_AGENT_DELEGATED_TERMINAL=1` is a human |
| 23 | `_interactive_input_available` is false | On macOS, `_launch_gui_terminal_defense` opens Terminal.app and waits up to 10 minutes. Exit 0 writes the badge. Elsewhere exit 1 |
| 24 | Failed standards | `1` records `FAILED` / `llm-unverified` and exits 1. `2` continues |

### 2. Questions

`ChangeSummary.generate` writes one summary. One changed function uses `_build_prompt_for_single`; several use `_build_unified_prompt`. No key or a bad response uses `_fallback` / `_unified_fallback`.

Attempt number is the max of prior sessions for this HEAD and `.git/understanding_agent_state.json`, plus one. The same diff reuses cached questions. A question whose best score is already ≥ 70 is skipped later as already answered.

Fresh questions:

1. If `UNDERSTANDING_AGENT_MASTER_ANALYSIS` is `1` / `on` / `true`, `run_master_analysis` makes one LLM call. Malformed output is dropped. Advisory findings are printed by `_print_advisory_findings` and cannot block.
2. Otherwise `QuestionGenerator.generate` → `_build_prompt` → `_call_groq` (which calls `_call_nous`).
3. `QuestionGenerator.validate` keeps typed questions with a checklist. Items marked `is_fallback` are removed. Only model-written questions are asked.

Scale (`QuestionGenerator.get_diff_scale`), from added plus deleted lines:

| Lines | Questions |
|---|---|
| ≤ 30 | 2 |
| 31–99 | 3 |
| 100–150 | 3 |
| 151–200 | 4 |
| 201–250 | 5 |
| 251–350 | 6 |
| 351–500 | 7 |
| > 500 | 8 |

`get_standards_and_diff_counts` keeps about 80% of questions on the diff and at most 20% on failed standards.

Types and seconds (`TIME_LIMITS`): Code Logic 30; Data Flow 45; Dependencies 45; Reasoning, Edge Cases, Change Impact, What-if, System Architecture, Invariants 60.

No questions left: `fail_open_enabled()` is false only when `UNDERSTANDING_AGENT_FAIL_MODE=closed`. Closed records `FAILED` / `llm-unverified` and exits 1. Otherwise records `PASSED` / `llm-unverified`, writes the badge, and exits 0.

### 3. One question

Inside the loop in `cli.main`, state is rewritten by `_write_state` before the set and after each answer.

1. Attempt > 1 and score < 70: `FollowUpGenerator.rephrase_failed_question`.
2. Print the question. `Interaction.timed_input` speaks it with macOS `say` unless `UNDERSTANDING_AGENT_VOICE=off`. Tab starts dictation (macOS system dictation, else Whisper via `sounddevice` if installed). Timeout stores an empty answer.
3. `hygiene_enabled` and `classify_answer`: one re-ask for an empty, single-token, or stuttered capture. Enter keeps the original. Mixed languages are not flagged. `UNDERSTANDING_AGENT_ANSWER_HYGIENE=off` skips this.
4. `AnswerEvaluator.evaluate`. Empty or timeout returns score 0 and does not call the model.
5. If the question has a checklist, `_evaluate_checklist` asks the model for a rationale and a tier per key point. `checklists.aggregate_score` turns those tiers into 0–100. The model’s own number is not the score.
6. Score in `[25, 70)` with `follow_up_required` and a non-empty answer: `FollowUpGenerator.generate`, one more `timed_input` (cap 45s), evaluate again, keep the max.
7. Score < 25: `build_session_payload`, `save_session`, `ServerClient.send`, exit 1. Later questions are not asked.
8. Score ≥ 70 marks the question passed.

After the set, the commit passes only when every question’s best score is ≥ 70. The stored `score` is the average. Failure offers another attempt (`y`) or abort. Success deletes the state file, calls `mark_verified_for_commit`, and exits 0.

`llm-verified` means the model wrote the questions and judged every answer. Any offline score makes the attempt `llm-unverified`.

Offline checklist scoring is `verify_offline` (content-word overlap). `clarity_offline` is imported by the evaluator and never called.

### 4. Save and sync

`session_payload.build_session_payload` is the only session builder (schema version 3). Fields include ids, HEAD, message, diff hash, status, verification, score, files, summary, standards, questions, and optional analysis, identity, and tool evidence. Raw diffs are not stored.

`session_store.save_session` writes `.git/understanding_sessions.db` mode `0600` and keeps the newest 500 (`UNDERSTANDING_AGENT_MAX_SESSIONS`; a value under 2 disables pruning). SQLite failure falls back to JSON. An old `understanding_sessions.json` is imported once.

`ServerClient.send` then:

1. Resolves URL, team token, and HMAC secret from the environment, else `.understanding-agent.json` or `.yaml` in the repo, else the built-in defaults `http://localhost:8088`, `team-token-secret-123`, and `team-hmac-secret-456`.
2. Drains `.git/understanding_sync_queue.json` with POST `/api/v1/sessions/batch`.
3. POST `/api/v1/sessions`. Failure appends to the queue (cap 500). Budget is 2.5 seconds. A sync failure does not change the commit’s exit code.
4. If no base URL were set, `UNDERSTANDING_AGENT_TELEMETRY_URL` would be a single POST with no queue. The built-in default means that legacy path does not run unless the resolved URL is empty.

Verification values this hook writes: `llm-verified`, `llm-unverified`, `formatting-exempt`, `no-verify-blocked`, `agent-blocked`.

### 5. Ledger

`service/app.py` `create_app`. Postgres via `service/docker-compose.yml` (host port 5434 for the database, 8088 for the API). Defaults match the hook: `SERVICE_TEAM_TOKEN`, `SERVICE_TEAM_SECRET`, `SERVICE_ADMIN_TOKEN`.

| Route | Behavior |
|---|---|
| `GET /api/v1/health` | liveness |
| `POST /api/v1/sessions` | HMAC when a secret is set, then `_upsert_session`. JIT user and repo from `identity` |
| `POST /api/v1/sessions/batch` | queue drain (`BatchPayload`) |
| `GET /api/v1/defense` | rows for CI. A commit is defended when a `PASSED` session has `head_commit` equal to that commit’s parent |
| `POST /api/v1/admin/repositories` and `/members` | `X-Admin-Token` |
| `POST /api/v1/admin/purge` | null answers older than `SERVICE_RETENTION_DAYS` (90). Also runs at startup |
| `GET /`, `/ui` | team dashboard, static files from `understanding_agent/ui` |
| `GET /api/users`, `/api/projects`, `/api/branches`, `/api/commits`, `/api/commits/{id}`, `/api/attempts/{id}` | Postgres-backed dashboard. Same paths without the `/api` prefix |

`scripts/verify_defense.py` checks a commit range against `GET /api/v1/defense`. Exit 0 audited, 1 unaudited, 2 usage or unreachable ledger. Flags: `--strict`, `--allow-agent-unverified`, `--allow-unattended-unverified`, `--allow-missing`. The current hook does not write passing `agent-unverified` rows; it writes `agent-blocked`.

`understanding-agent-push-guard` only checks that a pre-commit hook file exists and mentions this agent or the pre-commit framework.

`understanding-agent-sync-history` backfills the local SQLite store to the ledger. It is not on the commit path.

`scripts/sync_git_to_ledger.py` is a separate one-shot. It deletes staged rows, then walks two hardcoded directories (`/Users/liza/Documents/PRE-COMMIT-DEMO/...`) and fixed repo ids, inserting `UNAUDITED` / `no-defense-recorded` rows for git commits that have no session. It is not imported by the hook.

### 6. Models

Only `~/.config/understanding-agent/.env` (`install.sh --key` and `--model`).

| Choice | Model id | Endpoint |
|---|---|---|
| `haiku` | `claude-haiku-4-5` | Anthropic |
| `sonnet` | `claude-sonnet-4-5` | Anthropic |
| `nous` | `qwen/qwen3-coder-30b-a3b-instruct` | Nous |
| `org` | `org` | `http://llm.yorkdevs.link:2222/v1` |

Questions and judging use that same pair (`resolve_model` / `resolve_judge_model`). HTTP is `api_utils.call_llm_api` (aliases `call_nous_api`, `call_groq_api`): retries, empty-200 retry, reasoning-tag strip, loose JSON parse. `UNDERSTANDING_AGENT_DISABLE_THINKING` defaults on and sends `reasoning_effort=none` for Nous.

The model writes the summary, the questions, and the rubric picks. Blocks, standards, evidence, change kind, the trivial-change exemption, agent detection, and the numeric score are code.

### 7. Other commands

| Command | Role |
|---|---|
| `understanding-agent` | hook |
| `understanding-agent-ui` | local SQLite dashboard on `127.0.0.1:8000` |
| `understanding-agent-push-guard` | pre-push install check |
| `understanding-agent-sync-history` | backfill local sessions |
| `install.sh` | global hooks, venv, model file |
| `install_ui.sh` / `start_ui.sh` | local dashboard |
| `scripts/wire_project.sh` | per-repo pre-commit config |
| `scripts/calibration_harness.py`, `context_eval_harness.py`, `bench_*.py`, `demo_probe.py` | measurement and demos, not on the commit path |

Languages with function extraction: Python, JavaScript/TypeScript, C#, F#, Go, Rust, Java, Kotlin, Ruby, PHP, C/C++, Swift. SQL and notebooks are recognized. Other extensions stay as module-level changes so the filename gate still sees them.

The `tests/` tree was removed on this branch (`77098ba`). Nothing in-repo executes those old tests.

## Redundant files

Not imported, not an entry point, and not required to commit.

| Path | Why it is unused by the running hook |
|---|---|
| `node_modules/` | Untracked JavaScript `openai` tree. The hook’s HTTP client is Python. Safe to delete locally |
| `scripts/sync_git_to_ledger.py` | One machine, two absolute paths, hardcoded repo ids `3` and `4`. Overlaps the ledger idea of `sync_history.py` but is not called by install, the hook, or the service |
| `synthesis.md`, `synthesis-verified.md` | Point-in-time reviews of an older `main`. They describe a test suite this branch deleted and a fallback-question policy this branch removed |
| `PLAN.md`, `PLAN_V1_COMPLETION.md`, `PLAN_POKERFACE_JUDAS.md`, `WORKSTREAMS_ASSESSMENT.md` | Planning history. `docs/README.md` still points at them as history, not as runtime |
| `CENTRALIZED_DATABASE_ARCHITECTURE.md` | Design note. The implemented schema and routes are `service/models.py` and `service/app.py` |
| `docs/CLI_PIPELINE_REFERENCE.md` | Long restatement of `cli.py`. It still describes agent commits as recorded-and-allowed (`agent-unverified`) and offline fallback questions. The code blocks agents and drops fallback questions. `docs/ARCHITECTURE.md` overlaps it and has the same drift |

Still live, even though they overlap:

- `understanding_agent/server.py` and the dashboard half of `service/app.py` both serve `understanding_agent/ui`. The local server reads SQLite. The ledger server reads Postgres and adds `/api/projects` and `/api/branches`. Keep both while solo and team dashboards are both in use.
- `understanding-agent` and `understanding-agentHook` in `.pre-commit-hooks.yaml` are two ids for one entry point.

## Orphan functions

Confirmed by walking every `understanding_agent/`, `service/`, and `scripts/*.py` call. FastAPI route handlers are live even though nothing calls them by name. `ast.NodeVisitor.visit_*` methods in `formatting_detector.py` are live. Nested callbacks `_rank_sess` and `strip_params` are live.

| Symbol | File | What is wrong |
|---|---|---|
| `LLMManager`, `.provider`, `.base_url`, `.model`, `.api_key`, `.reset` | `llm_manager.py` | Class is never constructed. Callers use `get_provider`, `resolve_base_url`, `resolve_model`, `resolve_api_key`. `.reset` existed for the deleted tests |
| `compute_diff_hash` | `commit_context.py` | `cli.main` hashes the change payload inline |
| `clear_verified_for_commit` | `hook_stages.py` | Imported by `cli.py`, never called. `mark_verified_for_commit` is the one that runs |
| `_sync_base_url` | `server_client.py` | `ServerClient.send` calls `_resolve_config` instead |
| `export_sessions` | `session_store.py` | No caller. Docs mention it as a manual import |
| `_seed_from_state` | `session_store.py` | No caller. Attempt restore in `cli.main` reads the state file itself |
| `format_violation` | `coding_standards.py` | No caller. Checks build violation strings inline. `parse_violation` is used by `server.py` |
| `CodeGraph.get_related_functions` | `code_graph.py` | No caller. `ContextBuilder.build` reads `graph.nodes` and `graph.edges` directly |
| `Interaction.print_context` | `interaction.py` | Body is `pass` |
| `script_counts` | `answer_hygiene.py` | No caller. `classify_answer` is the function the hook uses |
| `Checklist.key_points` | `checklists.py` | No caller. Scoring reads `item.key_point` |
| `clarity_offline` | `checklists.py` | Imported in `answer_evaluator.py`, never called. Offline path uses `verify_offline` |
| `SessionPayload` | `understanding_agent/server.py` | No route uses it. `receive_session` takes a plain dict |
| `SessionPayload` | `service/app.py` | No route uses it. `record_session` parses the raw body. `BatchPayload` is used |

Not orphan: `functions_touching_lines` (imported by `change_detector.py` as `generic_functions_touching_lines`), `call_llm_api` (used through `call_nous_api` / `call_groq_api`), and the `_call_groq` methods on the summary, question, follow-up, and evaluator classes (each one delegates to that class’s `_call_nous`).
