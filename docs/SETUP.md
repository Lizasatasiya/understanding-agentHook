# Setup and test

Do this from a checkout of this repo. You need Python 3.10 or newer, a Postgres database, and a model API key on the service. The hook runs on your machine, does the local checks, and asks the service to run the model.

Use a throwaway git repo for the commit tests. Do not try the first run inside a repo you care about.

## 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[service]"
pip install -r service/requirements.txt
```

The service extra adds FastAPI, uvicorn, SQLAlchemy, pydantic, and the model client. `service/requirements.txt` adds the Postgres driver.

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

Leave `SERVICE_TEAM_TOKEN` unset for the local test. The service reads that variable from the process environment, not from `.env`. An empty value accepts the interview without `X-Team-Token`.

`UNDERSTANDING_AGENT_PASSING_SCORE` defaults to 75. A lower score stops the commit and does not ask the remaining questions. The service reads it. Restart the service after changing it.

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

Point both hook stages at the `understanding-agent` binary in this checkout. Replace the path.

```bash
mkdir -p .git/hooks
cat > .git/hooks/pre-commit << 'EOF'
#!/bin/sh
export UNDERSTANDING_AGENT_SERVER_URL=http://127.0.0.1:8000
exec /ABSOLUTE/PATH/TO/understanding-agentHook/.venv/bin/understanding-agent
EOF
cp .git/hooks/pre-commit .git/hooks/prepare-commit-msg
cat > .git/hooks/post-commit << 'EOF'
#!/bin/sh
GIT_DIR="$(git rev-parse --git-dir 2>/dev/null)" || exit 0
rm -f "$GIT_DIR/understanding_agent_verified.json"
exit 0
EOF
chmod +x .git/hooks/pre-commit .git/hooks/prepare-commit-msg .git/hooks/post-commit
```

`pre-commit` runs the local checks and the interview. `prepare-commit-msg` runs next and only checks that this same diff just passed. It must not ask again. `post-commit` deletes that pass marker so the next commit starts clean. If `prepare-commit-msg` is missing, a passed interview can still be rejected.

Do not put the model key in this script. If `/etc/understanding-agent/config.env` already contains `UNDERSTANDING_AGENT_SERVER_URL`, the hook uses that file and ignores the server variable here.

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

If the service cannot be reached, the commit stops and this check stays empty. Start the service and run the interview again.

## 9. Skip a trivial edit

After a successful commit, change only a comment:

```bash
printf '\n# total of two numbers\n' >> calc.py
git add calc.py
git commit -m "Comment the adder"
```

A comment-only, formatting-only, import-only, or docs-only diff can pass with no questions and without calling the service. A real logic change goes through the questions again, from the start.

## 10. Run the same test from another machine

The service stays on the machine from steps 1–3. The other machine gets only the hook. It does not get the model key, and it does not need this repo's `.env`.

On the service machine, stop the process that is bound to `127.0.0.1` and start it on the network interface the other machine can reach:

```bash
cd /ABSOLUTE/PATH/TO/understanding-agentHook
.venv/bin/uvicorn service.app:app --host 0.0.0.0 --port 8000
```

Find this machine's address, for example `192.168.1.20`, and confirm from the other machine:

```bash
curl -s http://192.168.1.20:8000/api/v1/health
```

A healthy response is `{"status":"ok","schema_version":1}`. If that fails, the service host or a firewall is still blocking port 8000.

To require a shared token, export it in the same shell that starts uvicorn. A `SERVICE_TEAM_TOKEN` line in `.env` is ignored.

```bash
export SERVICE_TEAM_TOKEN=dev-token
.venv/bin/uvicorn service.app:app --host 0.0.0.0 --port 8000
```

On the other machine, install the hook for every repo. Python 3.10 or newer is required. The installer must be root.

When that machine has a checkout of this repo:

```bash
cd /ABSOLUTE/PATH/TO/understanding-agentHook
sudo bash install.sh --server http://192.168.1.20:8000 --team-token dev-token
```

When it does not have a checkout, the installer downloads the hook from GitHub. The account running the installer needs read access to that private repo. Replace `main` with the branch you want installed.

```bash
REF=main
curl -fsSL "https://raw.githubusercontent.com/Lizasatasiya/understanding-agentHook/${REF}/install.sh" \
  | sudo REF="$REF" bash -s -- --server http://192.168.1.20:8000 --team-token dev-token
```

`--team-token` must match `SERVICE_TEAM_TOKEN` on the service. The installer writes the server URL and the token to `/etc/understanding-agent/config.env`. It does not write a model key. Once that file exists, the hook ignores `UNDERSTANDING_AGENT_SERVER_URL` in a shell.

Then repeat steps 5, 6, and 7 in a scratch repo on that machine. Do not copy the hook script from step 4. `git commit` uses the system hook. The questions are printed on that machine. The service terminal on the first machine shows the model calls. Step 8 still runs on the service machine, using the git email from the scratch repo.

Remove the hook from the other machine with `sudo bash install.sh --uninstall` from a checkout, or with the same curl command and `--uninstall` instead of `--server`:

```bash
REF=main
curl -fsSL "https://raw.githubusercontent.com/Lizasatasiya/understanding-agentHook/${REF}/install.sh" \
  | sudo REF="$REF" bash -s -- --uninstall
```

That clears the system hooks path, `/etc/understanding-agent`, and `/usr/local/share/understanding-agent`. A scratch repo that used step 4 instead is uninstalled by deleting `.git/hooks/pre-commit`, `.git/hooks/prepare-commit-msg`, and `.git/hooks/post-commit`.
