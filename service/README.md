# Central Understanding Service (the Judas ledger)

FastAPI ingestion service for understanding-agentHook session payloads.
Receives sessions from the pre-commit hook over the org network, JIT-provisions
developers and repositories from the payload's zero-touch identity block, and
serves the proof-of-defense lookup that CI/pre-receive uses to detect
unaudited commits (see ../docs/ENFORCEMENT.md).

## Quick start (SQLite, no Docker)

    pip install -r requirements.txt
    SERVICE_TEAM_TOKEN=dev-token uvicorn service.app:app --port 8000

Point a hook at it from any repo:

    UNDERSTANDING_AGENT_SYNC_URL=http://127.0.0.1:8000 \
    UNDERSTANDING_AGENT_TEAM_KEY=dev-token \
    UNDERSTANDING_AGENT_MODE=agent python -m understanding_agent.cli

## Production (Postgres via Docker)

    cd service
    SERVICE_TEAM_TOKEN=... SERVICE_TEAM_SECRET=... SERVICE_ADMIN_TOKEN=... \
      docker compose up -d

The service auto-creates its schema on startup (SQLAlchemy `create_all`).

## Environment

| Variable | Purpose | Default |
|---|---|---|
| SERVICE_DATABASE_URL | SQLAlchemy URL (sqlite:/// or postgresql+psycopg://) | sqlite:///./understanding_ledger.db |
| SERVICE_TEAM_TOKEN | Required X-Team-Token on ingestion (empty = open, dev only) | (empty) |
| SERVICE_TEAM_SECRET | HMAC-SHA256 secret for X-Signature verification | (empty) |
| SERVICE_ADMIN_TOKEN | Required X-Admin-Token on org-structure endpoints | (empty) |
| SERVICE_RETENTION_DAYS | Days raw answer text is retained before purging | 90 |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET  | /api/v1/health | Liveness + API schema_version |
| POST | /api/v1/sessions | Ingest one session (JIT user/repo, idempotent by session_id) |
| POST | /api/v1/sessions/batch | Drain the hook's offline queue (`{"sessions": [...]}`) |
| GET  | /api/v1/defense?repo_slug=&from=&to= | Proof-of-defense rows for CI/pre-receive |
| POST | /api/v1/admin/repositories | Assign a repo to an org/team |
| POST | /api/v1/admin/members | Add a user to a team |

All responses carry `schema_version`.

## Org hierarchy

organizations → teams → team_members(users); repositories.team_id. Sessions
resolve per developer (user_id), per repo (repo_id), per team (repo.team_id) —
no free-text attribution. Admin endpoints wire repos/members into the
hierarchy; everything else JIT-provisions on first sight.

## Privacy & retention

Raw answer text is personal data: retained SERVICE_RETENTION_DAYS (default
90), then nulled by `purge_expired_answers` (scores and aggregates are kept
forever). Per-developer score views are HR-adjacent: the dashboard side
(Pokerface P4, deferred) must default them OFF and make individual drill-down
a team opt-in. See the AMENDMENTS section in
../CENTRALIZED_DATABASE_ARCHITECTURE.md.

## Anti-spoofing ceiling (be honest in reviews)

The shared team token + HMAC proves "someone holding the team secret wrote
this payload", not "this developer answered honestly". Full per-developer
attestation is out of scope pre-adoption (documented, deliberate).
