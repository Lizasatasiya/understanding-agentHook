# Defense service

Runs the model for each interview. The hook finishes its local checks, then posts the staged diff to `POST /api/v1/defense/start` and each answer to `POST /api/v1/defense/{defense_id}/answer`. This process writes the summary, the questions, and the scores, then stores that developer's persona. The next diff does not read those rows.

Model code lives in `service/llm` (client and keys) and `service/interview` (summary, questions, scoring). The hook package does not import them.

The full first-time test, including the scratch repo and the persona check, is [docs/SETUP.md](../docs/SETUP.md). Run the service from the repo root so it finds `.env`.

```bash
pip install -e ".[service]"
pip install -r service/requirements.txt
.venv/bin/uvicorn service.app:app --host 127.0.0.1 --port 8000
```

| Variable | Purpose |
|---|---|
| `SERVICE_DATABASE_URL` | Postgres URL. Required to start. Persona tables are created on startup |
| `UNDERSTANDING_AGENT_MODEL` | `nous`, `groq`, `sonnet`, `haiku`, or `org` |
| `UNDERSTANDING_AGENT_API_KEY` | Key for that model. Read from the process environment, then `~/.config/understanding-agent/.env`, then `.env` in the directory where the service was started |
| `SERVICE_TEAM_TOKEN` | `X-Team-Token`. Read only from the process environment, not from `.env`. Empty allows local use without a token |
| `SERVICE_TEAM_SECRET` | HMAC secret for `X-Signature`. Read only from the process environment |
| `SERVICE_DEFENSE_STEPS` | Default `summary,trivial_gate,questions` |
| `SERVICE_BYPASS_TRIVIAL` | Skip questions on a trivial diff. Default on |
| `SERVICE_INTERVIEW_TTL_SECONDS` | Default 1800 |
| `UNDERSTANDING_AGENT_PASSING_SCORE` | Default 75 |

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Liveness |
| POST | `/api/v1/defense/start` | Summary, trivial skip, or questions for this request |
| POST | `/api/v1/defense/{defense_id}/answer` | Score one answer: `continue`, `stop`, or `pass` |

`docker compose up` in this directory starts a local Postgres on port 5433 and can build the service. Hosted Postgres is configured only through `SERVICE_DATABASE_URL`.
