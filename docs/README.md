# Documentation Index

Everything here is written against the implemented system (v0.4.0, commit
82cee04), not against intentions — every flow, number, and module reference
was verified against the code.

| Doc | Audience | What it covers |
|---|---|---|
| [OVERVIEW.md](OVERVIEW.md) | Everyone, non-technical | What this system is, the problem it solves, what happens when a developer commits, what it deliberately never does, deployment shapes for solo devs and teams |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Engineers | Module map with input/output contracts, the full commit-time pipeline, session data schema v3, local + central storage, the evaluation protocol, failure modes |
| [DECISIONS.md](DECISIONS.md) | Engineers, tech leadership | Every major design decision with the alternatives considered and why they were rejected |
| [OPERATIONS.md](OPERATIONS.md) | Platform/ops, team leads | Install, configuration reference, CI, deploying the central ledger, wiring enforcement, voice setup, testing, troubleshooting |
| [LLM_POLICY.md](LLM_POLICY.md) | Engineers, security | Where the LLM is allowed to participate; injection hardening; cost discipline |
| [ENFORCEMENT.md](ENFORCEMENT.md) | Platform/ops, security | Bypass taxonomy (--no-verify, agents, GUIs), proof-of-defense checks, the hook-vs-centralized verdict, rejected alternatives |

Living plan documents in the repo root carry the history:
PLAN.md (PoC→production hardening), WORKSTREAMS_ASSESSMENT.md and
PLAN_POKERFACE_JUDAS.md (the Bad Romance / Pokerface / Judas workstreams),
CENTRALIZED_DATABASE_ARCHITECTURE.md (the central DB blueprint + implemented
amendments §9).
