# Decision Record — what we chose, what we rejected, and why

Each entry: the decision, the alternatives considered, and the reason. Where
a choice was the user's explicit call, that is noted. Policy companions:
[LLM_POLICY.md](LLM_POLICY.md), [ENFORCEMENT.md](ENFORCEMENT.md).

## D1. Commit-time oral defense (pre-commit hook) as the core mechanism

**Decision.** The understanding check is an interactive pre-commit hook: the
author answers generated questions in their terminal before the commit
lands.

**Alternatives.**
- *CI-time async review* (questions posted to a PR): keeps the moment far
  from the change, answers arrive hours later from memory, participation is
  optional — it becomes paperwork.
- *Server-side questioning at push*: cannot ask an interactive question at
  push time with acceptable latency; degrades to deterministic-only checks.
- *Async questions via email/Slack*: kills the commit-time moment entirely.

**Why.** The commit is the only moment where intent, context, and
consequence coincide, and the only moment where failing is cheap (the commit
just doesn't happen yet). This is also why the hybrid delivery verdict
(D10) keeps the hook as the question-delivery mechanism even for teams.

## D2. Deterministic credential gate; the LLM never blocks

**Decision.** Live-credential detection is pure pattern/entropy code, runs
FIRST, and the LLM has zero participation in block decisions. LLM findings
are advisory only and are structurally unable to reach the gate, the
standards violation list, or sys.exit.

**Alternatives.**
- *LLM security review as (part of) the gate*: the gate reads
  attacker-controlled diff content — piping that into a prompt is a
  prompt-injection surface for exactly the code being screened; blocks would
  be nondeterministic and unexplainable; every commit pays LLM latency before
  it can fail fast.
- *Gate after questions*: would make a developer sit through a defense for a
  commit that is doomed anyway.

**Why.** The gate is the security boundary; boundaries are deterministic,
fast, testable, and explainable ("this line matches a live AWS key format").
Zero LLM calls on the blocked path is a test-enforced invariant.

## D3. Checklist-Based Verification instead of a scalar LLM score

**Decision.** Every generated question carries a validated weighted
checklist (2–4 key points with tiered rubrics). The judge produces per-item
rationales and tier picks; the 0–100 score is deterministic aggregation of
those picks — never the LLM's own number. Malformed judge output degrades to
a deterministic offline fallback.

**Alternatives.**
- *"Score this answer 0–100" judge*: LLM-chosen scores are unstable,
  self-preferential (a model scores its own phrasing higher), unauditable,
  and unfalsifiable in tests.

**Why.** The rubric makes scoring reproducible and testable (the offline
fallback can verify the same checklist with content-word overlap), makes
judge failures visible instead of silent, and gives follow-ups a concrete
missing-concept list to target. Judge/generator model split
(`UNDERSTANDING_AGENT_JUDGE_MODEL`) removes the remaining self-preference
bias.

## D4. Fail-open with honest "unverified" marking

**Decision.** If no key is configured or the API is unreachable, the hook
asks offline fallback questions, marks the session `llm-unverified`, and
lets the commit proceed. `UNDERSTANDING_AGENT_FAIL_MODE=closed` reverses
this for teams that want it.

**Alternatives.**
- *Fail-closed (block without LLM)*: an API outage or missing key takes the
  whole team's commit flow hostage — the fastest way to `--no-verify`
  becoming muscle memory.
- *Fail-open silently*: the dishonesty bug — a fallback pass recorded as
  verified would corrupt the ledger's whole premise. Rejected; verification
  is a REQUIRED field with no default in the payload builder.

**Why.** Adoption beats purity: an advisory system must not be the single
point of failure for commits, and honesty is preserved in the record.

## D5. One folded LLM analysis call (master analysis) — no separate security review

**Decision.** When master analysis is enabled, ONE LLM call produces
classification + advisory findings (security and standards) + questions with
checklists. There is no separate security-review call and no extra
evidence-gated call; deterministic tool evidence (semgrep/gitleaks) is fed
into the same prompt as hints.

**Alternatives.**
- *A dedicated security-review call per commit*: doubles cost and latency;
  the findings would still be advisory (D2), so the only change is the bill.
- *Evidence-gated extra call (run only when tools flag)*: was an open
  decision; resolved against after reviewing the landed master-analysis
  architecture — the single call already satisfies the ≤1-extra-call budget,
  and classification/questions need to run anyway.

**Why.** Cost/latency discipline without losing review coverage. Findings are
citation-verified deterministically (a finding citing a file not in the diff
is dropped — "LLM proposes, code disposes").

## D6. Provider-agnostic LLM manager with a compat layer

**Decision.** One env namespace (`UNDERSTANDING_AGENT_PROVIDER/MODEL/
BASE_URL/API_KEY`) drives presets for Nous, Groq, OpenAI, OpenRouter,
Together, Ollama and any custom OpenAI-compatible endpoint. Legacy
`NOUS_*`/`GROQ_*` variables keep working through a compatibility layer with
the original precedence order.

**Alternatives.**
- *Hardcoded provider*: single point of failure and pricing hostage.
- *Clean break with renamed env vars*: breaks every existing setup and
  test for cosmetic gain.

**Why.** The product's value is the protocol, not any model vendor; one env
var switches provider, and the compat layer honors the pre-existing
precedence contract (env vars before .env files, etc.).

## D7. Local session store: SQLite, 0600 (user decision)

**Decision.** Sessions live in `.git/understanding_sessions.db` (SQLite,
0600). A legacy JSON store is imported once on first open and renamed
`.imported` (backup, never deleted). JSON remains only as an explicit export
format. If SQLite is unusable (read-only .git), save/load degrade to the
legacy JSON path.

**Alternatives.**
- *Keep plaintext JSON* (previous behavior): readable by anyone with file
  access; answers are personal data. Explicitly overruled by the user.
- *Postgres locally too*: a server dependency on every dev machine for a
  single-user history; pointless without the central service.
- *SQLite + JSON mirror*: two sources of truth for no reader.

**Why.** Local privacy posture (restricted file perms) without a server
dependency; identical public API so callers (server.py, commit_context,
e2e) never changed.

## D8. Queue-backed central sync, 2.5s budget, commit never blocked

**Decision.** Sync to the ledger is opt-in (`UNDERSTANDING_AGENT_SYNC_URL`).
On failure the session is queued to `.git/understanding_sync_queue.json`
(0600, ≤500) and drained as a batch on the next hook run. Total sync budget
2.5s. The legacy telemetry URL remains a separate, queue-less transport.

**Alternatives.**
- *Synchronous required sync*: a slow ledger delays or blocks every commit —
  the central component becomes the availability bottleneck of a local tool.
- *No offline queue*: airplaned/VPN-dropped sessions would be lost — holes
  in the ledger that look exactly like bypasses (false accusations).

**Why.** The ledger is only trustworthy if it never loses records people
believed were recorded. Queue-then-drain closes that gap without ever
touching commit latency.

## D9. Anti-spoofing: shared team token + HMAC (V1), not per-developer auth

**Decision.** Ledger writes authorize with a shared team token plus an
HMAC-SHA256 signature over the canonical payload (sorted keys, no
whitespace) keyed by a team secret. Identity comes from zero-touch git
metadata (email primary key, hashed machine_id). The documented ceiling:
this proves "someone holding the team secret", not "this developer answered
honestly".

**Alternatives.**
- *OIDC/SSO per developer*: the strongest option long-term, but it
  reintroduces exactly the login friction that kills hook adoption (the
  reason zero-touch identity exists at all). Deferred, not rejected.
- *Per-developer cryptographic attestation (signed commits, key
  registration)*: meaningful cost (key ceremony, onboarding) for a threat
  (a teammate spoofing another teammate's defense record) that is second-
  order before org-wide adoption. Deferred deliberately.

**Why.** Progressive trust: raise the floor cheaply now (token+HMAC defeats
casual forgery and off-team writes), keep the strong options open for when
the ledger actually gates something people are motivated to spoof.

## D10. Hybrid delivery: hook stays interactive; centralization is ledger + enforcement

**Decision.** The hook is the question-delivery mechanism forever; the
central service owns the ledger, enforcement, and (later) config
distribution. No centralized replacement for the defense itself.

**Alternatives.**
- *Centralized-only enforcement* (checks at push, no local hook): loses the
  entire product thesis — push-time checks can only be deterministic
  (gitleaks with extra steps) or async (email questions nobody answers).
- *System-wide git-commit interception* (PATH shim / wrapper that detects
  any `git commit` and opens a centralized flow): technically possible,
  rejected as unenforceable (absolute-path git, alternate shells, libgit2
  tools, agents all bypass it), fragile, and it attests nothing — the same
  trust problem with more moving parts. Full reasoning in
  [ENFORCEMENT.md](ENFORCEMENT.md).

**Why.** The interactive defense is irreducibly local (D1); enforcement is
irreducibly central (client hooks are advisory by nature). The hybrid uses
each side for what only it can do.

## D11. Agents are first-class unverified actors (record-and-pass)

**Decision.** `UNDERSTANDING_AGENT_MODE=agent` (explicit) or an unreachable
TTY (auto) → skip the defense, record `agent-unverified` /
`unattended-unverified`, exit 0 with a loud banner. The gate still
hard-blocks. CI policy decides what agent rows require (e.g. human review).

**Alternatives.**
- *Hard abort (previous behavior)*: an agent literally could not commit
  through a repo with this hook (TTY EOF → empty answer → abort <25%). An
  adoption blocker and a false negative (aborted ≠ audited).
- *Silent pass*: makes agent commits indistinguishable from defended ones —
  corrupts the ledger.

**Why.** Agents are a new actor class, not cheaters: model them honestly
(unverified), let CI layer the human-review requirement on top, and never
let an automation be the reason a human can't ship.

## D12. Multilingual answers: handle, never discard; judge concepts only

**Decision.** The evaluator prompt judges concept coverage, never language,
grammar, or spelling; mixed-script answers are never flagged by hygiene.
Discard is reserved for the un-transcribable case only (one optional re-ask).

**Alternatives.**
- *Discard mixed-language answers*: punishes bilingual developers for no
  quality gain — the judging layer already tolerates language mixing.
- *Chasing better code-switching ASR* (cloud model, fine-tuning): research
  on code-switched speech (MUSCAT benchmark) shows it's an open problem
  (~8% WER even for the best commercial pairs). Robust evaluation beats
  chasing transcription quality. Rejected for V1; documented as the
  evaluation-side strategy instead.

**Why.** The failure that matters is a dictation artifact scoring as a wrong
answer and aborting a commit — answer hygiene (D13) kills that bug class
without touching legitimate answers.

## D13. Answer hygiene: one optional re-ask, never a forced discard

**Decision.** Suspect captures (empty, single token, ASR stutter) get ONE
re-ask ("say it again or type it; Enter submits the text above as-is").
Kill switch via env var.

**Alternatives.**
- *Auto-discard suspect answers*: a false positive throws away a real answer
  the developer just spent 45 seconds on.
- *No hygiene layer*: dictation failure punishes the developer — a clipped
  first word would score 0 and abort the commit.

**Why.** A warning the developer can overrule with one keypress has
near-zero cost; a wrong abort has a real cost. The retry is optional, so
false positives are cheap.

## D14. Standards: 15 deterministic checks + a prose standards document

**Decision.** The standards audit runs 15 changed-line-scoped deterministic
checks; the master-analysis path additionally judges the change against a
prose document (repo override `.understanding/standards.md`, else the
shipped default). LLM standards findings are advisory.

**Alternatives.**
- *Hand-coded regex checks only* (the original design): can't express
  team-specific values at all.
- *LLM-only standards judgment*: blocks become nondeterministic; violates
  the D2 principle that deterministic code decides blocks.

**Why.** Same tiering as security: deterministic decides, LLM enriches. The
prose document lets a team encode its actual standards without shipping code.

## D15. Privacy defaults shipped with storage, not after

**Decision.** Raw answer text is retained `SERVICE_RETENTION_DAYS` (default
90) and then nulled (scores/aggregates kept forever). Per-developer score
comparisons default OFF in any future dashboard; individual views are team
opt-in. machine_id is a one-way hash.

**Alternatives.** Retaining raw answers indefinitely with a TODO; letting
the dashboard grow first and doing privacy "before launch" — both are how
performance-surveillance tools get killed in HR review after adoption
instead of before.

**Why.** Answers are developer-performance data. The system's premise
(understanding, not surveillance) must be encoded in its retention policy,
or the data model will eventually contradict the product story.

## D16. Timestamp-first session pruning

**Decision.** Prune keeps the most recent N (default 500) sessions ranked by
`(timestamp, attempt_number)`.

**Alternative rejected.** The original ranked by `(attempt_number, timestamp)`
— but attempt_number RESETS per commit cycle, so a brand-new attempt-1
session was pruned in favor of old sessions that happened to accrue high
attempt numbers. Found by the agent-mode e2e (the freshly recorded session
vanished); fixed with a regression test.

## D17. CI as the floor (and the enforcement seed)

**Decision.** GitHub Actions matrix (py3.12 + 3.14) runs the full suite +
gate e2e on every push/PR.

**Why.** A regression once sat red on main for 8 days because nothing
watched main. CI is also Phase 1 of the enforcement story: once the ledger
exists, the same CI job gains the proof-of-defense check
(scripts/verify_defense.py).


---

# Completion-pass decisions (PLAN_V1_COMPLETION.md)

## D18. Context quality is measured before it is improved

**Decision.** scripts/context_eval_harness.py (+5-case corpus) replays the
REAL detector/graph/builder on scratch repos and measures % of expected
concepts present in the built context; CI gates it at ≥0.80 (--strict).

**Alternatives.** Building token-budget selection first and "eyeballing"
question quality after; or no measurement (the original state — context
work was vibes).

**Why.** The harness paid for itself immediately: its first run caught a
real extraction bug — nested React handlers ("next definition wins" spans)
excluded their component's useState preamble from every question. Without
measurement that gap was invisible; with it, the fix was one targeted change
and the corpus now pins it forever.

## D19. Ranked selection under budgets, not positional truncation

**Decision.** Over-budget diffs keep lines by importance rank (changed
lines are the change — kept first; structural context next; filler dropped
first), with a 35-line per-function default cap and a 240-line total budget
across functions so one giant function cannot starve the rest.

**Alternatives.** The old behavior (keep the first N structural lines,
truncate at position); or pure token counting without rank.

**Why.** Positional truncation keeps whatever is NEAR THE TOP and silently
drops the end of the change — exactly where late returns/raises live. Rank
0 (the +/- lines) is the only tier that can never be silently lost.

## D20. Deterministic ChangeKind tier with a frozen matrix (LLM refines, never replaces)

**Decision.** change_kind.py classifies every commit (even offline) from
paths/diff-shape/symbols into typed weights over the six frozen domains +
auditable signals; the classification prints for the developer and feeds a
CHANGE CLASSIFICATION section in BOTH question prompts. The domain
matrix lives in `change_kind.py`.

**Alternatives.** LLM-only classification (exists via master analysis) —
offline-blind and unauditable; or no classification (questions target the
wrong dimension for UI vs schema changes).

**Why.** Deterministic-first answers "how does it distinguish UI from system
design changes" with something testable and always-on; the LLM tier remains
a refinement on top, not the source of truth.

## D21. Tiered redaction (vendor: zero chars; generic entropy: 4)

**Decision.** Vendor-format, staged-key-file, and credentialed-URL findings
show ZERO secret characters; generic high-entropy findings keep the first
4 chars + ellipsis.

**Alternative rejected.** The old uniform first-8-chars preview: eight
characters of a high-entropy secret is a meaningful leak for a message that
only needs to say "this one, in this file".

## D22. Push guard: an install AID, not a new block

**Decision.** understanding-agent-push-guard (pre-push hook id) checks that
a pre-commit hook is actually installed where git looks, and exits 1 with
exact remediation steps when not.

**Alternatives.** Blocking pushes on missing sessions (pre-push cannot know
about the ledger reliably); doing nothing (fresh clones stayed invisible
until CI).

**Why.** It checks INSTALLATION STATE only — never session contents — so it
cannot false-positive on a defended commit, and it gives the developer a
fix-it-now moment before the CI check makes the gap public.

## D23. Violation wire format has ONE owner

**Decision.** coding_standards.format_violation/parse_violation define and
decode the "path:line: message (`code`)" contract; server.py consumes the
canonical parser (its private regex is gone); tests pin the round-trip.

**Why.** The format was produced in ~15 places and parsed by a side regex —
the exact "silent semantic drift" shape. Now the producer and consumer share
one definition.

## D24. Backfill is fail-loud; the hook is fail-silent — deliberately different

**Decision.** understanding-agent-sync-history exits 1 and stops at the
first failed batch; the commit-time hook never blocks on sync problems.

**Why.** An explicit operator command must tell the truth about partial
failure (otherwise the ledger looks complete and isn't); a commit path must
never be hostage to the network. Same system, two contexts, two policies.

## D25. Retention is enforced at startup + endpoint, not just documented

**Decision.** The service purges expired raw answers on every startup and
exposes POST /api/v1/admin/purge (admin token) for cron; scores/aggregates
are kept forever.

**Why.** A retention policy that exists only as a function nobody calls is a
policy you cannot demonstrate to an HR reviewer. Enforcement rides the
service lifecycle so it happens even when nobody remembers the cron.
