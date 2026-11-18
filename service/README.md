# Defense service

FastAPI process that summarizes a staged change, generates questions, scores answers, and stores a persona aggregate. The hook talks to it over HTTP. Run it from the repo root so it finds `.env`.

Full setup, including the Supabase pooler URL: [docs/SETUP.md](../docs/SETUP.md). The commit sequence is [docs/WORKFLOW.md](../docs/WORKFLOW.md).

```bash
pip install -e ".[service]"
pip install -r service/requirements.txt
.venv/bin/uvicorn service.app:app --host 127.0.0.1 --port 8000
```

| Variable | Purpose |
|---|---|
| `SERVICE_DATABASE_URL` | Postgres URL. Required. Environment, then `.env` |
| `UNDERSTANDING_AGENT_MODEL` | `nous`, `groq`, `sonnet`, `haiku`, or `org` |
| `UNDERSTANDING_AGENT_API_KEY` | Key for that model |
| `SERVICE_TEAM_TOKEN` | `X-Team-Token`. Empty allows unauthenticated local use |
| `SERVICE_TEAM_SECRET` | HMAC secret for `X-Signature` |
| `SERVICE_ADMIN_TOKEN` | `X-Admin-Token` on admin routes |
| `SERVICE_DEFENSE_STEPS` | Default `summary,trivial_gate,questions` |
| `SERVICE_BYPASS_TRIVIAL` | Skip questions on a trivial diff. Default on |
| `SERVICE_INTERVIEW_TTL_SECONDS` | Default 1800 |
| `UNDERSTANDING_AGENT_PASSING_SCORE` | Default 75 |
| `SERVICE_RETENTION_DAYS` | Default 90 |

The hook uses `UNDERSTANDING_AGENT_SERVER_URL`, `UNDERSTANDING_AGENT_TEAM_TOKEN`, and `UNDERSTANDING_AGENT_TEAM_SECRET`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Liveness |
| POST | `/api/v1/defense/start` | Summary, trivial skip, or questions |
| POST | `/api/v1/defense/{defense_id}/answer` | Score one answer: `continue`, `stop`, or `pass` |
| GET | `/api/v1/defense` | Ingested session rows for a `repo_slug` |
| POST | `/api/v1/sessions` | Ingest one session payload |
| POST | `/api/v1/sessions/batch` | Ingest a batch |
| POST | `/api/v1/admin/repositories` | Attach a repo to an org and team |
| POST | `/api/v1/admin/members` | Add a user to a team |
| POST | `/api/v1/admin/purge` | Null expired raw answers now |

`docker compose up` in this directory starts a local Postgres on port 5433 and can build the service. Hosted Postgres is configured only through `SERVICE_DATABASE_URL`.
