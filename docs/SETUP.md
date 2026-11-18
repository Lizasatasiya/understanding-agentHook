# Setup

This is the path to run the defense service and take one commit through the hook. Do it from a checkout of this repo. Python 3.10 or newer.

## 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[service]"
```

`openai` is installed with the package. The service extra adds FastAPI, uvicorn, SQLAlchemy, and pydantic. The service also needs `psycopg[binary]`:

```bash
pip install -r service/requirements.txt
```

## 2. Configure the service

Put this in `.env` at the repo root. The service reads the file from the directory you start it in, so start uvicorn from the repo root.

```bash
UNDERSTANDING_AGENT_MODEL=nous
UNDERSTANDING_AGENT_API_KEY=your-key
SERVICE_DATABASE_URL=postgresql+psycopg://postgres.PROJECT_REF:PASSWORD@aws-1-REGION.pooler.supabase.com:5432/postgres?sslmode=require
SERVICE_TEAM_TOKEN=dev-token
```

`UNDERSTANDING_AGENT_MODEL` is `nous`, `groq`, `sonnet`, `haiku`, or `org`. The key and model are also read from `~/.config/understanding-agent/.env` if they are not in the process environment. The process environment wins.

Use the Supabase **session pooler** string from the project Connect dialog (port 5432). The direct host `db.<project>.supabase.co` is IPv6-only and will fail on a network that cannot route IPv6. The pooler username is `postgres.<project-ref>`, not `postgres`. Percent-encode reserved characters in the password (`?` is `%3F`, `/` is `%2F`, `#` is `%23`, `@` is `%40`) and keep `sslmode=require`.

An exported `SERVICE_DATABASE_URL` overrides `.env`.

Optional service settings:

| Variable | Default | Effect |
|---|---|---|
| `SERVICE_TEAM_SECRET` | empty | Require HMAC `X-Signature` on defense and session posts |
| `SERVICE_ADMIN_TOKEN` | empty | Require `X-Admin-Token` on `/api/v1/admin/*` |
| `SERVICE_DEFENSE_STEPS` | `summary,trivial_gate,questions` | Interview order |
| `SERVICE_BYPASS_TRIVIAL` | on | Skip questions for a trivial diff when standards passed |
| `SERVICE_INTERVIEW_TTL_SECONDS` | 1800 | How long an interview can still be answered |
| `UNDERSTANDING_AGENT_PASSING_SCORE` | 75 | Score below this stops the interview |
| `SERVICE_RETENTION_DAYS` | 90 | How long raw answer text on session rows is kept |

## 3. Start the service

```bash
.venv/bin/uvicorn service.app:app --host 127.0.0.1 --port 8000
```

Check it:

```bash
curl -s http://127.0.0.1:8000/api/v1/health
```

A healthy response is `{"status":"ok","schema_version":1}`. Startup fails if Postgres cannot be reached. The schema is created on startup.

Docker Compose in `service/` starts a local Postgres on port 5433 and can build the service image. Use it only when you are not using the hosted database. The image still needs `SERVICE_DATABASE_URL`.

## 4. Point a test repo at the service

In a separate git repo that has `user.name` and `user.email` set:

```bash
mkdir -p .git/hooks
cat > .git/hooks/pre-commit << 'EOF'
#!/bin/sh
export UNDERSTANDING_AGENT_SERVER_URL=http://127.0.0.1:8000
export UNDERSTANDING_AGENT_TEAM_TOKEN=dev-token
exec /ABSOLUTE/PATH/TO/understanding-agentHook/.venv/bin/understanding-agent
EOF
cp .git/hooks/pre-commit .git/hooks/prepare-commit-msg
chmod +x .git/hooks/pre-commit .git/hooks/prepare-commit-msg
```

Replace the `exec` path with this checkout's venv. Stage a real code change and run `git commit`. You should see the local standards report, then questions. A passing last answer allows the commit. A low score aborts it. A formatting-only change can skip the questions.

If `/etc/understanding-agent/config.env` already contains `UNDERSTANDING_AGENT_SERVER_URL`, the hook ignores the variables in the script and uses that file.

## 5. Install for every repo on a machine

`install.sh` is the admin install. It needs root, a server URL, and a team token. The model key stays on the server.

```bash
sudo bash install.sh --server "http://127.0.0.1:8000" --team-token "dev-token"
```

That writes `/etc/understanding-agent/config.env` and sets the system `core.hooksPath`. `sudo bash install.sh --status` and `--uninstall` report and remove it.

## Voice and the local dashboard

Voice input is optional: `pip install -e ".[voice]"`. The hook still accepts typed answers without it.

`./start_ui.sh` serves a local commit dashboard. It is separate from the defense service and defaults to port 8000, so stop one before starting the other, or pass `--port`.
