# Workflow

Two processes. The hook is local and has no model key. The defense service holds the key, calls the model, and writes the persona row.

## On `git commit`

The hook entry is `understanding-agent` (`understanding_agent/core/cli.py`). It runs at `pre-commit`. The same command also runs at `prepare-commit-msg`, but that second run only checks that the interview just passed. It does not ask again.

1. **Read the staged change.** Detect the repo, the files, the stack, and a change classification (UI, data model, control flow, and so on). Build a bounded context from the diff. Empty staging area: exit 0, no interview.

2. **Block stolen credentials.** A deterministic scan rejects live keys, private key files, and credential-shaped values before any question. `UNDERSTANDING_AGENT_SECURITY_GATE=off` disables this scan.

3. **Advisory checks, still local.** Coding-standards findings are printed. If any failed, the author chooses fix (commit stops) or proceed (the failure is sent to the service as `standards_failed`). Security profile, optional semgrep/gitleaks output, and practice-pack hints are attached to that same payload. None of these call the model.

4. **Refuse a bypass.** `--no-verify`, a non-interactive agent commit, or a GUI commit with no terminal stops the commit. On macOS a GUI commit can open Terminal and run the interview there.

5. **Require a git author.** `user.name` and `user.email` must be set. The email is how the service keys the persona.

6. **Ask the service to start.** `POST /api/v1/defense/start` with the context, hints, author, and a `trivial` flag (formatting-only change). The hook signs the body when `UNDERSTANDING_AGENT_TEAM_SECRET` is set.

7. **The service decides the next step**, in this order (`SERVICE_DEFENSE_STEPS`, default `summary,trivial_gate,questions`):
   - Write a change summary.
   - If the hook marked the diff trivial, standards did not fail, and `SERVICE_BYPASS_TRIVIAL` is on: return `action=skip`. The hook allows the commit.
   - Otherwise generate questions. Optional master analysis runs only when `UNDERSTANDING_AGENT_MASTER_ANALYSIS` is `on`. The interview is kept in memory for `SERVICE_INTERVIEW_TTL_SECONDS` (default 30 minutes).

8. **Ask the author, one question at a time.** The hook prints the question, reads a typed or spoken answer, and `POST`s it to `/api/v1/defense/{defense_id}/answer`. The service scores that answer. Below `UNDERSTANDING_AGENT_PASSING_SCORE` (default 75) the service returns `stop` and the commit is aborted. A passing answer returns `continue`, or `pass` on the last question.

9. **Record the result.** On `pass` or `stop`, the service folds scores into one persona row for that git email. Question text and answer text are not stored on that row. The hook then writes a local pass marker so `prepare-commit-msg` can see that this diff was defended. The git index stays locked while the questions are on screen.

If the service cannot be reached, the commit stops. There is no local scoring fallback.

## What the service stores

Postgres is required. On startup the service creates tables and nulls raw answer text older than `SERVICE_RETENTION_DAYS` (default 90) on ingested session rows.

The commit path above does not send a session payload. `POST /api/v1/sessions` and `POST /api/v1/sessions/batch` exist for a separate ingest. `GET /api/v1/defense` lists those ingested rows for a repo. A finished interview writes the persona aggregate only.

`X-Team-Token` matches `SERVICE_TEAM_TOKEN`. An empty token means the endpoint is open, which is for local development. The token shows that the caller knows the team secret. It does not prove the author answered honestly.
