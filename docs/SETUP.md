# Setup and test

Do this from a checkout of this repo. You need Python 3.10 or newer, a Postgres database, and a model API key. The hook runs on your machine. It asks you to explain the staged change. The service only stores the result against your git email.

Use a throwaway git repo for the commit tests. Do not try the first run inside a repo you care about.

## 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[service]"
pip install -r service/requirements.txt
```

`openai` comes with the package. The service extra adds FastAPI, uvicorn, SQLAlchemy, and pydantic. `service/requirements.txt` adds the Postgres driver.

Voice answers are optional. `pip install -e ".[voice]"` enables them. Typed answers work without it.

## 2. Configure

Create `.env` in this repo root. Do not commit it.

```bash
UNDERSTANDING_AGENT_MODEL=nous
UNDERSTANDING_AGENT_API_KEY=your-key
SERVICE_DATABASE_URL=postgresql+psycopg://postgres.PROJECT_REF:PASSWORD@aws-1-REGION.pooler.supabase.com:5432/postgres?sslmode=require
```

`UNDERSTANDING_AGENT_MODEL` is `nous`, `groq`, `sonnet`, `haiku`, or `org`.

Use the Supabase session pooler string from the project Connect dialog (port 5432). The direct host `db.<project>.supabase.co` is IPv6-only and fails on a network that cannot route IPv6. The pooler username is `postgres.<project-ref>`. Percent-encode reserved characters in the password (`?` is `%3F`, `/` is `%2F`, `#` is `%23`, `@` is `%40`) and keep `sslmode=require`.

Leave `SERVICE_TEAM_TOKEN` unset for this test. An empty token means the local service accepts the persona post without a signature.

`UNDERSTANDING_AGENT_PASSING_SCORE` defaults to 75. A lower score stops the commit and does not ask the remaining questions.

## 3. Start the service

From this repo root, so uvicorn can see `.env`:

```bash
.venv/bin/uvicorn service.app:app --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
curl -s http://127.0.0.1:8000/api/v1/health
```

A healthy response is `{"status":"ok","schema_version":1}`. If Postgres cannot be reached, the process fails during startup. Leave this process running while you commit.

## 4. Make a scratch repo and install the hook

```bash
mkdir -p ~/hook-test
cd ~/hook-test
git init
git config user.name "Test Developer"
git config user.email "you@example.com"
```

Use the email you will look up later. The persona row is keyed by this email.

Point both hook stages at the `understanding-agent` binary in this checkout. Replace the path and the API key.

```bash
mkdir -p .git/hooks
cat > .git/hooks/pre-commit << 'EOF'
#!/bin/sh
export UNDERSTANDING_AGENT_MODEL=nous
export UNDERSTANDING_AGENT_API_KEY=your-key
export UNDERSTANDING_AGENT_SERVER_URL=http://127.0.0.1:8000
exec /ABSOLUTE/PATH/TO/understanding-agentHook/.venv/bin/understanding-agent
EOF
cp .git/hooks/pre-commit .git/hooks/prepare-commit-msg
chmod +x .git/hooks/pre-commit .git/hooks/prepare-commit-msg
```

`pre-commit` asks the questions. `prepare-commit-msg` runs next and only checks that this same diff just passed. It must not ask again. If that second file is missing, a passed interview can still be rejected.

If `/etc/understanding-agent/config.env` already contains `UNDERSTANDING_AGENT_SERVER_URL`, the hook uses that file and ignores the server variable in this script. The model key in this script is still required, because git does not load the checkout's `.env`.

## 5. Pass a real change

```bash
printf 'def add(a, b):\n    return a + b\n' > calc.py
git add calc.py
git commit -m "Add add()"
```

You should see a standards report, then one or more questions about this diff. Type an answer that explains the function and press Enter before the timer runs out. Answer every question the same way.

When every score is at least 75, the hook prints that the commit is allowed. Git then opens the message step and finishes the commit without a second interview. `git log -1 --oneline` shows `Add add()`.

## 6. Fail a change

```bash
printf 'def multiply(a, b):\n    return a * b\n' >> calc.py
git add calc.py
git commit -m "Add multiply()"
```

When the first question appears, answer `I do not know`. The hook prints that understanding was not demonstrated and aborts the commit. It does not ask the later questions. `git status` still shows `calc.py` staged. `git log -1` is still `Add add()`.

## 7. Retry the same diff

Leave the file staged and run `git commit -m "Add multiply()"` again.

The hook writes a new summary and new questions. It starts at question 1. It does not skip a question from the failed try, and it does not reuse that answer. A good answer to every question allows the commit.

## 8. Confirm the persona was saved

From the checkout that owns `.env`, after at least one finished interview:

```bash
cd /ABSOLUTE/PATH/TO/understanding-agentHook
.venv/bin/python - << 'PY'
from sqlalchemy import select
from service.app import get_state
from service.models import DeveloperExchange, DeveloperPersona

email = "you@example.com"
db = get_state().SessionLocal()
persona = db.execute(
    select(DeveloperPersona).where(DeveloperPersona.github_username == email)
).scalar_one_or_none()
if persona is None:
    raise SystemExit(f"no persona for {email}")
print(persona.attempt_count, persona.pass_count, persona.fail_count)
print(persona.summary)
rows = db.execute(
    select(DeveloperExchange).where(DeveloperExchange.persona_id == persona.id)
).scalars().all()
for row in rows:
    print(row.score, row.question_text[:80], row.answer_text[:80])
db.close()
PY
```

You should see one persona row for `you@example.com`, with a pass count and a fail count after steps 5 and 6, and one line per question that was actually asked. The failed interview is included. Questions that were never asked are absent.

The service being down does not change the allow or block decision. It only means this check stays empty. Start the service and run the interview again if the row is missing.

## 9. Skip a trivial edit

After a successful commit, change only a comment:

```bash
printf '\n# total of two numbers\n' >> calc.py
git add calc.py
git commit -m "Comment the adder"
```

A comment-only, formatting-only, import-only, or docs-only diff can pass with no questions. A real logic change goes through the questions again, from the start.
