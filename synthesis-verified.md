# Project Synthesis — Independently Verified (2026-10-16)

Written fresh against live runs, not inherited from synthesis.md. Every number
below was produced by an actual command in this session unless marked
"unverified". A companion verdict on the earlier synthesis.md is at the bottom.

## Verified state right now

- Working tree clean (only untracked docs), main = e9506e8, up to date with origin.
- Unit suite: `python3.14 -m unittest discover -s tests` → **179 tests, 4 FAIL**
  (all in tests/test_api_and_evaluator.py, TestAnswerEvaluator). Confirmed by run.
- `python3.14 tests/e2e_gate_first.py` → **all scenes PASS** (gate blocks with
  zero LLM calls, offline fallback asks the security question, sessions prune
  to 25, exit 0). Confirmed by run.

## The red suite — corrected diagnosis

The earlier synthesis (synthesis.md) correctly identified the broken code but
**misattributed the regression commit**.

What the code does now (answer_evaluator.py):

1. `_evaluate_fallback` (answer_evaluator.py:49) computes a real coverage score:
   dismissive → score 0, follow_up_required=False (lines 76-89); substantive →
   coverage-banded score 85+/70-band/76 with covered/missing concepts
   (lines 91-126).
2. Then the substantive branch **throws it all away**: the return at
   answer_evaluator.py:128-140 hardcodes score 75, covered_concepts=[],
   missing_concepts=[], follow_up_required=True, confidence 0.4 — the computed
   `score`, `covered`, `missing`, `follow_up`, and `eval_text` are dead values.

Git archaeology (git log -L, plus running the suite at the parent commits):

- At **ac1a7ef** the fallback returned the computed score, populated
  covered/missing concepts, and dismissive answers scored **10 with
  follow_up_required=True**. Suite: 28/28 OK in that module.
- **20fb32a** (colleague, "refactor: enhance ... fallback evaluation logic")
  is the regression origin: it rewrote `_evaluate_fallback` (89 lines changed),
  inverted the dismissive branch (0 + no follow-up instead of 10 + follow-up)
  and replaced the final return with the hardcoded 75.
- **2fba63a** touched answer_evaluator.py with exactly ONE line:
  `expected = question.get("expected_concepts") or []` — necessary for the
  coverage computation but not the regression. Blaming it was wrong.

The 4 failures, exactly:

- test_api_failure_fails_open_by_default: expects 76, gets 75.
- test_no_key_fails_open_by_default: expects 76, gets 75.
- test_fallback_evaluates_correct_answer_above_76: expects score ≥85 and
  populated covered_concepts; gets 75/empty.
- test_fallback_evaluates_dismissive_answer_low: expects score ≤15 AND
  follow_up_required=True; gets 0/False. (The earlier synthesis mentioned the
  first half of this contract but not that the dismissive branch also needs its
  follow_up_required restored to True.)

The tests encode the frozen fail-open contract (user-mandated thresholds:
76 baseline pass, 85+ substantive, dismissive low WITH follow-up). Fix the
code, not the tests: restore the ac1a7ef return block (use the computed
score/covered/missing/follow_up/eval_text) and set the dismissive branch's
follow_up_required back to True. ~10-line patch; the frozen behavior contract
is already in the project skill.

## Verified spot-checks of the rest of the codebase

- Gate on the no-files early exit: cli.py 2b comment block present at ~line
  111-123; `SecurityLens.find_critical_findings` runs before `sys.exit(0)` on
  empty changes. Invariant intact.
- cli.py main() is 683 lines with three near-identical `session_data = {`
  constructions (cli.py:210, 580, 635). The fix-violations block (210) has no
  `verification` field — that path records status without verification, the
  other two carry `llm-verified`/`llm-unverified`. Confirmed by grep.
- Gate: `UNDERSTANDING_AGENT_SECURITY_GATE=off` is the only disable
  (security_lens.py:191-192), pragma/example escape hatches at :184.
- Redaction `secret[:8] + "…"` confirmed at security_lens.py:226 — the
  production concern about first-8-chars of generic high-entropy secrets is
  legitimate and still open.
- LLM layer: `str | None` PEP 604 at llm_manager.py:219 (fine on ≥3.10);
  legacy names (call_nous_api, load_nous_api_key, _groq_request_once,
  load_groq_api_key alias) all still present — naming debt confirmed.
- Fallback evaluator prompt cap `max_tokens: 450` confirmed
  (answer_evaluator.py:232).
- Sessions: prune default 500, `UNDERSTANDING_AGENT_MAX_SESSIONS` override
  (session_store.py:244). Telemetry opt-in, fail-silent (test suite covers both).
- Server: binds 127.0.0.1:8000 (server.py:688); violations parser is regex
  over report strings (server.py:86 `file:line: msg`) — the format coupling
  concern is confirmed.
- interaction.py is darwin-first (termios, `sys.platform == "darwin"` TTS
  checks, non-darwin paths exist but nothing exercises them).
- Packaging: setup.py only, no pyproject.toml (verified absent);
  version 0.3.0, python_requires>=3.10; .pre-commit-hooks.yaml has two hook
  ids, points at main, no tags exist (git tag → empty). Pin-rev concern valid.
- README says "160+ unit and e2e tests" (README.md:129); actual count is 179
  collected (182 test defs incl. e2e helpers, 179 run). Stale but harmless.
- PLAN.md:127 confirms CI is "suggested, not done". No CI exists. Main being
  red since 20fb32a (Oct 8) is the direct proof of the gap.

## Production gaps — my ranking (unchanged in substance, sharpened)

1. **Fix the 4 red tests** by restoring the frozen fallback contract
   (answer_evaluator.py:76-89 and :128-140). ~10 lines. Before anything else.
2. **CI** (GitHub Actions: py 3.12 + 3.14, unittest + e2e_gate_first.py). The
   regression landed Oct 8 and sat red for 8 days because nothing watches main.
3. **Session payload builder**: one function producing session_data, versioned —
   kills the 3-way duplication and the already-present verification-field drift.
4. **cli.py decomposition** (gate / standards / question loop / persistence).
5. **Naming + packaging**: nous/groq renames behind aliases; pyproject.toml;
   tag a release so consumers pin rev.
6. Redaction tiering for generic-entropy secrets; Windows/Linux TTY support
   statement.

## Verdict on synthesis.md (the grounding check you asked for)

- Accurate: test counts and failures, e2e results, the dead-code description
  of `_evaluate_fallback`, run-order and gate-on-empty-files claims, the
  session_data duplication and missing verification field, redaction/prune/
  127.0.0.1/max-tokens/README-count/no-CI/no-pyproject/no-tags observations,
  the ranked gaps. Spot-checked every concrete one; none failed.
- Wrong on one material point: **the regression commit is 20fb32a, not
  2fba63a** (one-line addition). Attribution matters here because "fix or
  revert" discussions with the colleague should point at the right commit,
  and 20fb32a's diff also silently changed the dismissive branch — a change
  the synthesis under-reported.
- Under-reported: the dismissive branch regression (score 0 with
  follow_up_required=False vs the frozen 10/True) is a second, independent
  contract break inside the same function, not just "tests encode dismissive
  answers score low".
- Unverifiable-from-code claims (e.g. "deliberately or accidentally") were
  appropriately hedged in the original.

## How this was verified

Commands run this session: `git log/status/show -L` (regression origin),
`python3.14 -m unittest discover -s tests` (179/4 fail), suite at
20fb32a~1 (green), `python3.14 tests/e2e_gate_first.py` (pass), targeted
grep/sed reads of cli.py, answer_evaluator.py, security_lens.py,
llm_manager.py, session_store.py, server.py, interaction.py, setup.py,
.pre-commit-hooks.yaml, PLAN.md, README.md.
