# PLAN_V1_COMPLETION.md — remaining V1 work (all except UI, which a colleague owns)

Status: **implemented**. Final: 288 unit tests green, all e2e scripts pass
(e2e_gate_first 5 scenes, e2e_security_gate — which this pass also REPAIRED:
it had been silently broken by a scanner-mangled fixture and a system-python
hook invocation — and e2e_polyglot_fixture), context eval harness 1.00
aggregate coverage (CI-gated at 0.80). Baseline was 265 at plan time.

## Implementation report (concise)

1. Redaction tiering: vendor/staged-file/credentialed-URL findings show zero
   secret chars; generic entropy keeps 4 (security_lens._redact); old tests
   updated to the new contract + a tier regression test.
2. Token-budgeted selection: ranked line selection (changed → structural →
   filler), 240-line total budget across functions (context_builder).
3. Context eval harness + 5-case corpus: real-pipeline replay, --strict 0.80
   gate, added to CI. First run caught a REAL bug (nested React handlers
   excluded their component's useState preamble) — fixed with a
   conservative enclosing-definition extension; coverage 0.91 → 1.00. One
   measured gap documented (unchanged sibling functions; needs repo map).
4. Deterministic ChangeKind tier (change_kind.py): typed weights + auditable
   signals, frozen 10-case matrix, printed for the developer, feeds both
   question prompts (micro/macro + master analysis).
5. Violation wire format single-sourced (coding_standards.parse/format_
   violation); server.py's side regex removed; round-trip tests.
6. Backfill CLI (understanding-agent-sync-history): git-author resolution
   for legacy rows, batches of 25, --dry-run, fail-loud by design.
7. Retention enforced: startup purge + POST /api/v1/admin/purge (admin
   token), SERVICE_RETENTION_DAYS.
8. Push guard (understanding-agent-push-guard + pre-push hook id): install
   health check with remediation steps; respects core.hooksPath; 4 tests.
9. cli.py decomposition (light, as planned): printers/probe moved to
   hook_stages.py (852 → 783 lines), aliases keep patch targets working.
10. Docs: ARCHITECTURE/DECISIONS (D18–D25)/OPERATIONS/ENFORCEMENT/service
    README updated; support-matrix checklist added (GUI/voice).

Explicitly NOT done (with reasons): OIDC (D9, unchanged), tree-sitter/repo
map (conditional on harness results; one known gap documented), Windows/
Linux voice + GUI automation (cannot be built/tested from this macOS host —
manual checklist shipped), UI (colleague owns).

Scope decisions up front:
- Context items land in assessment order: eval harness FIRST (measurement
  before mechanism), then token-budgeted selection, then the deterministic
  ChangeKind tier. Tree-sitter/repo-map stay conditional on harness results
  (assessment item c — build only if measurement shows the regex tier failing).
- OIDC per-developer auth stays DEFERRED (decision D9): it needs an identity
  provider and a login flow, contradicting zero-touch identity; revisit when
  the ledger actually gates something people are motivated to spoof.
- Windows/Linux voice: support-matrix documentation only (cannot be built or
  tested from this macOS host); GUI-client matrix likewise shipped as a
  manual checklist — both require real GUIs we cannot drive here.

## Phases

1. Redaction tiering (security_lens): vendor-format / staged-key-file /
   connection-string findings show ZERO secret characters ("***"); generic
   high-entropy findings keep 4 chars + "…" (enough for the author to
   recognize their own value, not enough to matter to an attacker).
   Removes the secret[:8] leak on generic secrets.
2. Token-budgeted context selection (context_builder): replace the
   positional 35-line truncation with importance-ranked selection —
   signature/structural/changed lines always kept, filler dropped first,
   head+tail preserved; plus a TOTAL context budget across functions so one
   giant function cannot starve the rest.
3. Context eval harness (scripts/context_eval_harness.py + corpus): replay
   ChangeDetector→ContextBuilder on fixture repos with expected-concept
   manifests; report per-case coverage (% of expected concepts present in the
   built context); --strict gate. Deterministic, offline, no LLM.
4. Deterministic ChangeKind tier (understanding_agent/change_kind.py):
   path/diff-shape/symbol signals → category weights
   {ui, data_model, control_flow, infra_config, tests, docs} + dominant kind;
   attached to context as a typed field; printed in the defense preamble;
   fed to both question paths as a CHANGE CLASSIFICATION prompt section.
   Frozen test matrix: fixture diffs per category.
5. server.py violations parser: the "file:line: msg" format contract moves
   into coding_standards as one canonical parse function; server consumes it.
6. Backfill CLI: understanding-agent-sync-history — push existing local
   sessions to the central ledger (batched, --dry-run, author resolution
   for legacy rows without identity).
7. Retention enforcement: service purges expired raw answers on startup
   (SERVICE_RETENTION_DAYS) + POST /api/v1/admin/purge (admin token) for cron.
8. Push guard (install enforcement aid): new pre-push hook id
   understanding-agent-push-guard — verifies the package imports and the
   pre-commit hook is installed; loud instructions when not. Second chance
   before a push, complements the ledger check (fresh clones).
9. cli.py decomposition (mechanical): move gate-block printer, advisory
   printer, interactive check, record-only path, and fix/proceed prompt into
   understanding_agent/hook_stages.py; cli.main keeps the orchestration.
10. Documentation pass: ARCHITECTURE/DECISIONS/OPERATIONS/ENFORCEMENT
    updated for everything above; deferred lists shrink accordingly.

## Out of scope (unchanged)
UI (colleague), OIDC (D9), tree-sitter/repo-map (conditional on phase 3
results), Windows/Linux voice implementation, central dashboard views.
