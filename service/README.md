# Defense service

Stores each developer's persona and the questions and answers from each finished interview. The hook posts that record to `POST /api/v1/persona` when `UNDERSTANDING_AGENT_SERVER_URL` is set. The next diff does not read it. An interview started on this service also writes the same rows when it passes or stops.

The full first-time test, including the scratch repo and the persona check, is [docs/SETUP.md](../docs/SETUP.md). Run the service from the repo root so it finds `.env`.

```bash
pip install -e ".[service]"
pip install -r service/requirements.txt
.venv/bin/uvicorn service.app:app --host 127.0.0.1 --port 8000
```

| Variable | Purpose |
|---|---|
| `SERVICE_DATABASE_URL` | Postgres URL. Required to start, because admin tables are created on startup |
| `UNDERSTANDING_AGENT_MODEL` | `nous`, `groq`, `sonnet`, `haiku`, or `org` |
| `UNDERSTANDING_AGENT_API_KEY` | Key for that model |
| `SERVICE_TEAM_TOKEN` | `X-Team-Token`. Empty allows unauthenticated local use |
| `SERVICE_TEAM_SECRET` | HMAC secret for `X-Signature` |
| `SERVICE_ADMIN_TOKEN` | `X-Admin-Token` on admin routes |
| `SERVICE_DEFENSE_STEPS` | Default `summary,trivial_gate,questions` |
| `SERVICE_BYPASS_TRIVIAL` | Skip questions on a trivial diff. Default on |
| `SERVICE_INTERVIEW_TTL_SECONDS` | Default 1800 |
| `UNDERSTANDING_AGENT_PASSING_SCORE` | Default 75 |

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Liveness |
| POST | `/api/v1/persona` | Append this interview to one developer's persona |
| POST | `/api/v1/defense/start` | Summary, trivial skip, or questions for this request |
| POST | `/api/v1/defense/{defense_id}/answer` | Score one answer: `continue`, `stop`, or `pass` |
| POST | `/api/v1/admin/repositories` | Attach a repo to an org and team |
| POST | `/api/v1/admin/members` | Add a user to a team |

`docker compose up` in this directory starts a local Postgres on port 5433 and can build the service. Hosted Postgres is configured only through `SERVICE_DATABASE_URL`.
