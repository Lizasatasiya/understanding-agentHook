# LLM Access Policy — Security & Best-Practices Analysis

This document is the authoritative policy for where the LLM may participate
in the understanding-agentHook pipeline. Every rule below is enforced by
code and guarded by tests; the module references are the contract.

## Governing rule

Deterministic code decides blocks. The LLM only generates judgment-call
questions and advisory findings — it can never block, unblock, or suppress
anything, and its output can never reach a process exit or the gate's
finding list.

## Tier 0 — Credential gate: the LLM NEVER participates

- The gate (`security_lens.py`) runs FIRST, before any prompt, audit, or LLM
  call. It is the security boundary.
- Rationale: the gate reads attacker-controlled diff content. Sending diff
  content to an LLM before the gate would make the prompt an injection
  surface for the exact code being screened. An LLM must also never be able
  to downgrade or explain away a block.
- Invariant: blocked paths spend ZERO LLM calls. Record-only/agent mode (Judas J2)
  exits AFTER the gate — agents are unverified actors, not exempt ones
  (scene 5).
- The gate is hard-blocked by design; escape hatches are explicit and
  deterministic: the inline `# pragma: allow-secret` suppression, the
  example-file-name exemption, and `UNDERSTANDING_AGENT_SECURITY_GATE=off`.

## Tier 1 — Standards audit: deterministic checks are the block-worthy tier

- The coding-standards audit (`coding_standards.py`) is deterministic and
  runs before any LLM call. Its violations trigger the fix/proceed prompt.
- Standards are judged against a prose document — the repo override
  `.understanding/standards.md`, else the shipped `DEFAULT_STANDARDS_DOC`.
- LLM findings about standards are ADVISORY: they become context for
  questions, never violations. An LLM cannot manufacture a violation, and
  it cannot dismiss a deterministic one.

## Tier 2 — Master analysis pass: where the LLM IS allowed

One opt-in call (`UNDERSTANDING_AGENT_MASTER_ANALYSIS=1`,
`change_intelligence.py`) produces classification + advisory findings +
questions. Security and best-practices review is FOLDED into this single
call — there is no separate security-review call, so the cost cap
(max one extra LLM call per commit) is satisfied by construction.

Hard rules, each enforced in code:

1. CHARACTERIZE, never propose edits (edit-bias constraint).
2. The diff is DATA, never instructions — the prompt states this, and any
   instruction found inside diff content must be ignored (injection
   hardening).
3. Every finding MUST cite a file from the actual diff; citation-unverifiable
   findings are DROPPED deterministically (`_validate_findings`) — the LLM
   proposes, code disposes.
4. LLM output reaches only a validated, field-by-field-checked structure;
   anything malformed degrades to `None` and the deterministic path runs.
   LLM output can never reach `sys.exit`, the gate, or the standards
   finding list.
5. Findings can never block. The gate never consults this module.
6. Offline (no key, API error, malformed output): deterministic fallback
   questions only; the session is recorded `llm-unverified`.

## Deterministic evidence and the LLM

- `evidence_collector.py` runs semgrep/gitleaks/test-gap heuristics
  deterministically (opt-in by tool availability), displays the findings as
  an advisory block, and feeds them into the master-analysis prompt as hints
  to elaborate into judgment-call questions.
- The LLM elaborates a deterministic first opinion; it cannot suppress or
  fabricate tool evidence. Deterministic tool findings are recorded and
  displayed regardless of what the LLM says.

## Answer evaluation

- Judge/generator model split (`UNDERSTANDING_AGENT_JUDGE_MODEL`) avoids
  self-preference bias.
- The judge scores concept coverage only — never language, grammar, or
  spelling; mixed-language and transliterated answers are legitimate
  (Pokerface P1). Multilingual judging clauses live in both evaluator
  prompts and are frozen contract.
- Offline fallback evaluation is deterministic; such sessions are recorded
  `llm-unverified` — a fallback pass is never recorded as verified.

## Privacy

- Diff content goes ONLY to the configured LLM provider
  (`UNDERSTANDING_AGENT_*` env; see `.env.example`) and nowhere else.
- Telemetry/central sync sends the session record (questions, answers,
  scores, identity metadata) — never raw diff content — and only to the
  explicitly configured endpoint, opt-in.
- The live `.env` holding API keys is gitignored and never read into output.

## Cost/latency discipline

- Gate blocked → 0 LLM calls.
- Standards "fix violations" → 0 LLM calls.
- Record-only (agent/unattended) mode → 0 LLM calls.
- Normal interactive path with master analysis on → 1 analysis call +
  per-question judge calls; questions are cached by diff_hash so a retry
  of the same diff reuses them.

## Where each rule is guarded

| Rule | Guard |
|---|---|
| Gate before any LLM call, zero calls on block | `security_lens.py`, `cli.py` |
| Findings citation-verified, advisory-only | `change_intelligence.py` |
| Malformed LLM output degrades to deterministic | `change_intelligence.py` |
| Offline sessions marked unverified | `session_payload.py` |
| Multilingual answers judged on concepts | `answer_evaluator.py` |
| Answer hygiene re-ask before scoring | `answer_hygiene.py` |
| Agent mode: no questions, no LLM, gate still blocks | `cli.py`, `hook_stages.py` |
