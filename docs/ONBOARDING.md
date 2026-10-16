# Onboarding a Developer Machine — the two-command setup

For a colleague's Mac (works on the same corporate image). You (the setup
guide) hand over: repo access + the LLM key (team vault, never chat). They
run two commands per machine, one per project. No sudo anywhere.

## What they run

```bash
# ONE TIME per machine:
git clone https://github.com/Lizasatasiya/understanding-agentHook.git ~/understanding-agentHook
sh ~/understanding-agentHook/scripts/setup_dev_machine.sh
# → installs uv if missing, makes python3 = 3.12 (no sudo), installs pre-commit.
# → open a NEW terminal afterwards (PATH lookups are cached per session).

# ONE TIME per project:
sh ~/understanding-agentHook/scripts/wire_project.sh /path/to/their/project \
   --env /path/to/team/.env        # or skip --env and paste the key into the scaffolded .env
```

Then they work exactly as before: stage a change, `git commit` — the
defense fires. First commit builds the hook's venv (takes ~1 minute once).

## What the scripts do (and never do)

setup_dev_machine.sh:
- uv via astral.sh installer if missing; `uv python install 3.12 --default`
  → ~/.local/bin/python3 = 3.12 (ONE python; Apple's 3.9 stays untouched
  for the OS and is never reached — the hook CLI re-execs itself under the
  modern python even if an old interpreter launches it)
- `uv tool install pre-commit` → ~/.local/bin/pre-commit

wire_project.sh (in the TARGET project):
- .pre-commit-config.yaml → local hook repo at rev: main, and pre-commit
  install. The mutable-rev warning is expected for local-path dev wiring;
  for team adoption pin the git URL + release tag (the file says how).
- .env (0600, gitignored): copied from --env or scaffolded with a TODO for
  NOUS_API_KEY. The key NEVER travels through the repo or this script.
- warns if no git user.email (needed for session attribution).

## Verify (each new machine, 2 minutes)

```bash
cd /path/to/their/project
python3 ~/understanding-agentHook/scripts/demo_probe.py
# → prints classification, offline questions, and 2 LIVE questions (~10-15s).
#    No key errors? Done.
```

Gate check (optional, shows the block): stage a fake vendor-shaped key
(built by string concatenation so no real secret exists anywhere),
`git commit`, watch it abort, then restore:

```bash
python3 - <<'EOF'
aws = "AKIA" + "IOSFODNN7" + "EXAMPLE"
open("scratch_creds.py", "w").write(f'import os\nAWS_ACCESS_KEY_ID = "{aws}"\n')
EOF
git add scratch_creds.py && git commit -m "oops"   # → blocked, exit 1
git restore --staged scratch_creds.py && rm scratch_creds.py
```

## Optional team stack (only if demoing the ledger / dashboard)

```bash
# central ledger (one machine serves the team):
cd ~/understanding-agentHook
SERVICE_DATABASE_URL=sqlite:////tmp/ledger.db SERVICE_TEAM_TOKEN=<team-token> \
  python3 -m uvicorn service.app:app --port 8766          # needs [service] extra
# per-developer hook sync (in the project or their shell):
export UNDERSTANDING_AGENT_SYNC_URL=http://<ledger-host>:8766
export UNDERSTANDING_AGENT_TEAM_KEY=<team-token>
export UNDERSTANDING_AGENT_TEAM_SECRET=<team-secret>      # if HMAC enabled

# local dashboard (per machine):
uv venv ~/.understanding-demo-venv --python 3.12
uv pip install --python ~/.understanding-demo-venv/bin/python -e "~/understanding-agentHook[ui]"
UNDERSTANDING_AGENT_REPO_ROOT=/path/to/their/project \
  ~/.understanding-demo-venv/bin/python -m understanding_agent.server
# → http://127.0.0.1:8000/ui (auto-refreshes every 8s)
```

## Troubleshooting (the three real failure modes we've hit)

| Symptom | Fix |
|---|---|
| `python3` says 3.9.x | Stale terminal session (zsh caches lookups) — open a NEW terminal. The tools also self-heal: they re-exec under the modern python automatically. |
| Hook output looks stale after pulling hook-repo changes | pre-commit snapshots the hook repo at its recorded rev — in each project: `pre-commit clean && pre-commit install-hooks` (or `pre-commit autoupdate --repo <path>`) |
| "NOUS_API_KEY is not set" warning | .env missing in the TARGET project root or key empty — offline fallback ran (by design); fill .env for live questions |
| `zsh: permission denied: python3.14` | A broken leftover alias on some machines — ignore it; only `python3` is used anywhere |

## What a demo project needs (for the management-demo format)

Any repo with one self-contained staged change works. The strongest demo
shape: a real bug in their own code, fix staged (not committed), then run
the scenes from demo/MANAGEMENT_DEMO_RUNBOOK.md — the probe shows the
classification and the exact live questions for THEIR diff before anyone
commits anything.
