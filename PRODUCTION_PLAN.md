# PRODUCTION_PLAN.md — PoC → Production-Grade Restructure ("The Chromatica Track")

Status: in progress. Each phase lands with the full suite green
(`python3.14 -m unittest discover -s tests` + `python3.14 tests/e2e_gate_first.py`).

## Governing rules

1. Frozen behavior unchanged unless this plan says otherwise: 75% pass /
   70% per-question / 25% abort, 25–70% follow-up band, per-type time limits,
   2–8 question scaling, 80/20 diff/standards ratio.
2. ONE deliberate policy change (user-mandated, supersedes the old fail-open
   "76-score quirk" from PLAN.md W4): when the LLM is unavailable, fallback
   questions are asked, answers get NO score, and the session is stored as
   `llm-unverified`. A substantive answer still lets the commit proceed
   (fail-open); a dismissive/empty answer still aborts.
3. Module renames are a clean break (no compat shims) — we control the repo,
   tests, docs, and entrypoints. Version bumps to 0.4.0.
4. Every Gaga-named module carries a first-line docstring that states its job
   in plain words; the Gaga is flavor, never a comprehension tax.

## Module naming map (Lady Gaga edition)

| old | new | reference | plain-words job |
|---|---|---|---|
| cli.py | applause.py | "I live for the applause" | showrunner entrypoint; a passing commit earns the applause |
| environment_detector.py | gypsy.py | "Gypsy" | finds which venue we're playing tonight (repo/branch/author) |
| change_detector.py | enigma.py | "Enigma" | solves what actually changed in the staged diff |
| language_support.py | scheisse.py | "Scheiße" | speaks every language (multilingual function extraction) |
| stack_detector.py | born_this_way.py | "Born This Way" | your repo's stack, detected from its manifests |
| security_lens.py | paparazzi.py | "Paparazzi" | hunts what you're exposing; secrets get caught by the paparazzi |
| coding_standards.py | teeth.py | "show me your teeth" | the audit that shows its teeth over changed lines |
| evidence_collector.py | rain_on_me.py | "Rain On Me" | findings rain in from scanners you already have |
| practice_packs.py | the_cure.py | "The Cure" | best-practice medicine per framework/domain |
| code_graph.py | bad_romance.py | "Bad Romance" | the caller/callee love-hate graph |
| context_builder.py | joanne.py | "Joanne" | the stripped-down essence of a diff (skeletonizer) |
| change_summary.py | shallow.py | "Shallow" | the surface summary, before the deep questions |
| question_generator.py | poker_face.py | "Poker Face" | deals the questions; read your own code's poker face |
| llm_manager.py | chromatica.py | "Chromatica" | the planet where every provider voice lives |
| api_utils.py | just_dance.py | "Just Dance" | transport that retries until the API just dances |
| interaction.py | telephone.py | "Telephone" | the line between you and the hook (TUI, TTS, mic) |
| answer_evaluator.py | judas.py | "Judas" | betrays your bluff — scores honest understanding |
| followup_generator.py | million_reasons.py | "Million Reasons" | a million reasons to ask one more thing (follow-ups) |
| evaluation_models.py | the_fame.py | "The Fame" | the shape of a score, written down (dataclasses) |
| commit_context.py | marry_the_night.py | "Marry The Night" | commits to the night: HEAD, message, attempt keys |
| session_store.py | always_remember.py | "Always Remember Us This Way" | remembers your attempts (last N sessions) |
| server.py | monster_ball.py | "The Monster Ball" (tour) | the dashboard ball (FastAPI) |
| server_client.py | john_wayne.py | "John Wayne" | rides out to deliver telemetry, only when asked |
| ui/ | ui/ (kept) | — | dashboard assets, packaged unchanged |

Function-level renames (clean, aliases dropped where we control all callers):
- `call_nous_api` / `call_groq_api` → `call_llm` (just_dance.py)
- `load_nous_api_key` / `load_groq_api_key` → `load_api_key` (moves to chromatica.py)
- `_nous_request_once` / `_groq_request_once` → `_request_once`
- `AnswerEvaluator._call_groq/_call_nous` → `_call_llm`
- The `hasattr(load_groq_api_key, "assert_called")` mock-detection hack in the
  evaluator is DELETED — tests now patch the real target on the new module.
- Neutral, established names stay: `resolve_model`, `resolve_base_url`,
  `extract_json`, `fail_open_enabled`, `EvaluationResult`, `SecurityLens`,
  `QuestionGenerator`, `FollowUpGenerator`, `Interaction`, session-store
  function names, `LLMManager`, `PROVIDER_PRESETS`.

Entrypoints: console scripts unchanged in name
(`understanding-agent`, `understanding-agent-ui`), retargeted to
`understanding_agent.applause:main` and `understanding_agent.monster_ball:run`.

## Phases

### P1 — Fallback no-score contract (policy change, user-mandated)
- `EvaluationResult` gains `unscored` (and `to_dict` carries it).
- Fallback evaluation: substantive answer → score 0, `unscored=True`,
  `llm_verified=False`, follow_up off, evaluation text says "recorded, not
  scored"; dismissive answer → score 0, unscored=False (abort path unchanged).
- Closed-mode LLM failure → score 0 with explicit "FAIL_MODE=closed" text
  (the old placeholder 50 goes away).
- cli: abort guard becomes `< 25 AND not unscored`; pass decision keeps the
  existing fallback bypass (all answers substantive → commit proceeds);
  terminal prints the one-line UNVERIFIED warning on a fail-open pass
  (promised in PLAN.md W4, currently missing from the terminal output).
- Tests updated to the new contract on purpose (they encoded the old 76/85
  quirk; 4 of them are red on main today for the same region).

### P2 — The Chromatica rename
- `git mv` all modules per the map; rewrite intra-package imports, test
  patch targets, scripts/bench imports, setup entrypoints.
- Remove the mock-detection hack; tests patch real targets.

### P3 — One builder, one reconciliation home
- `always_remember.build_session_payload(...)` — the single, versioned
  (schema_version: 1) session/telemetry payload builder. All three cli
  call sites (abort, standards-fix, final) use it. Fixes today's drift:
  the standards-fix session lacked the verification field.
- Attempt/state reconciliation (currently inlined in cli) moves to
  `marry_the_night` (attempt math) + `always_remember`
  (cached-question restore), leaving applause.py as pure orchestration.
- Behavioral no-ops; existing session tests must pass unmodified.

### P4 — Key resolution consolidation
- `.env` file search + legacy key names move from just_dance into
  chromatica as one documented precedence ladder inside `load_api_key()`.
  Env vars before disk files (unchanged); NOUS_*/GROQ_* names keep working.
- just_dance becomes pure transport (HTTP, retry, JSON extraction).

### P5 — Packaging, CI, docs
- pyproject.toml (canonical) + setup.py thin shim; version 0.4.0;
  extras [voice] [ui] unchanged.
- .github/workflows/ci.yml: ubuntu + macos matrix, Python 3.12 & 3.14,
  unittest discover + e2e_gate_first.py. No deploy stage (nothing to
  deploy); release/tagging workflow deferred until first tagged release.
- README: module tree with new names + one-line jobs, support matrix
  (macOS full incl. voice/TTS; Linux: TTY input works, no `say` TTS —
  espeak/festival integration is future work; Windows: untested —
  termios/tty import is guarded and the hook degrades to non-interactive,
  tracked as future work), test command on 3.12/3.14, honest test count.
- PLAN.md gets a closing addendum pointing here.

## Out of scope (unchanged reasoning)
- Voice stack rework, dashboard redesign, standards-rule retuning,
  `is_std` heuristic rework (deferred issue from PLAN.md).
