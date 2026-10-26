# `cli.py` — The Understanding Agent Commit Pipeline: Technical Reference

This document provides a comprehensive, step-by-step breakdown of [`understanding_agent/cli.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/cli.py) across all abstraction levels. It explains what each stage does, why it exists, which modules and functions it delegates to, how it works internally, the data structures passed, edge cases handled, and the design decisions behind each mechanism.

---

## 1. Executive Summary & Core Philosophy

The `understanding-agent` pre-commit hook enforces **proof of human understanding at commit time**. Research indicates that over 45% of AI-assisted code commits introduce security vulnerabilities or architectural regressions while developers feel artificially confident. The agent bridges this belief-reality gap by requiring developers (or AI agents) to orally or textually defend their changes during `git commit`.

`cli.py` acts as the **central orchestrator** of the entire pre-commit lifecycle. Its architectural principles are:
1. **Deterministic Gates First**: Blocker gates (like real API keys or credentials) must fail fast, offline, with zero LLM cost, before any oral questioning begins.
2. **Deterministic Standards & Evidence**: Static analysis, syntax audits, and security tool evidence are deterministic; LLMs can elaborate or interrogate findings, but cannot suppress or fabricate them.
3. **Fail-Open Policy for Developer Velocity**: If external LLM APIs fail or network connectivity drops, the pipeline degrades gracefully to offline question templates and heuristic evaluations rather than blocking developer work.
4. **Honest Ledger Auditing**: Commits made without full interactive LLM defense (e.g. CI, offline fallback, or autonomous coding agents) are stamped honestly (`llm-unverified`, `agent-unverified`, `unattended-unverified`) for downstream CI and central ledger verification.
5. **Stateful Retries**: Retrying a commit cycle reuses previously passed questions (`Already Answered`), rephrases failed ones, and tracks attempt numbers under the current Git `HEAD`.

---

## 2. High-Level Pipeline Architecture Flowchart

```mermaid
graph TD
    Start(["git commit invoked"]) --> SelfHeal["0. Interpreter Self-Heal<br/>(sys.version >= 3.10)"]
    SelfHeal --> TTYSetup["0b. TTY Reconfiguration<br/>(sys.stdin = /dev/tty)"]
    TTYSetup --> Stage1["1. Environment Detection<br/>(EnvironmentDetector.detect)"]
    Stage1 --> Stage2["2. Change Detection<br/>(ChangeDetector.detect)"]
    
    Stage2 --> EarlyGate{"2b. Early Credential Gate<br/>(SecurityLens.find_critical_findings)"}
    EarlyGate -- "Live Credential Found" --> BlockEarly["_print_gate_block()<br/>exit(1)"]
    EarlyGate -- "Clean" --> EmptyCheck{"Staged Files Empty?"}
    
    EmptyCheck -- "Yes (0 files)" --> ExitClean["exit(0)"]
    EmptyCheck -- "No (Files present)" --> CommitCtx["3. Commit Context & Hash<br/>(HEAD, diff_hash, attempt_key)"]
    
    CommitCtx --> StackDetect["4. Stack Detection<br/>(StackDetector.detect)"]
    StackDetect --> CodeGraph["5. Code Graph Building<br/>(CodeGraph.build)"]
    CodeGraph --> ContextBuild["6. Context Building<br/>(ContextBuilder.build - budgeted diffs)"]
    ContextBuild --> ChangeKind["6b. ChangeKind Classification<br/>(classify_change: UI, Data, Flow, etc.)"]
    
    ChangeKind --> SecurityProfile["7. Security Profiling & Evidence<br/>(SecurityLens, collect_evidence, Identity)"]
    SecurityProfile --> MainGate{"7b. Full Credential Gate<br/>(find_critical_findings)"}
    MainGate -- "Critical Leak Found" --> BlockGate["_print_gate_block()<br/>exit(1)"]
    
    MainGate -- "Pass" --> StandardsAudit["8. Coding Standards Audit<br/>(CodingStandardsChecker.check - 15 rules)"]
    StandardsAudit --> DisplayEvidence["8b. Display Advisory Findings & ChangeKind"]
    
    DisplayEvidence --> AgentModeCheck{"9. Non-Interactive / Agent Mode?<br/>(UNDERSTANDING_AGENT_MODE=agent or no TTY)"}
    AgentModeCheck -- "Yes (Agent/Headless)" --> RecordOnly["Record agent-unverified / unattended-unverified<br/>Save session & Send Telemetry<br/>exit(0)"]
    
    AgentModeCheck -- "No (Interactive Human)" --> StandardsCheck{"Failed Standards?"}
    StandardsCheck -- "Yes (Violations)" --> FixOrProceedPrompt{"Developer Prompt:<br/>[1] Fix vs [2] Proceed"}
    FixOrProceedPrompt -- "[1] Fix" --> AbortFix["Record FAILED<br/>exit(1)"]
    FixOrProceedPrompt -- "[2] Proceed" --> SummaryGen
    StandardsCheck -- "No (All Good)" --> SummaryGen["10. Change Summary Generation<br/>(ChangeSummary.generate)"]
    
    SummaryGen --> StateRecovery["11. Attempt & Question Cache Recovery<br/>(Check .git/understanding_agent_state.json)"]
    StateRecovery --> DiffCached{"Diff Matches Cached State?"}
    
    DiffCached -- "Yes (Retry same diff)" --> ReuseQuestions["Reuse Cached Questions<br/>Preserve scores >= 70%"]
    DiffCached -- "No (Fresh diff)" --> GenerateQ["Generate Fresh Questions<br/>(Master Analysis or Legacy Generator)"]
    
    ReuseQuestions --> QuestionLoop["12. Oral Defense Loop (Per Question)"]
    GenerateQ --> QuestionLoop
    
    subgraph OralDefense ["Oral Defense Loop (Question by Question)"]
        QL_Start(["For each question"]) --> AlreadyAns{"Score >= 70% in previous attempt?"}
        AlreadyAns -- "Yes" --> MarkAns["Print 'Already Answered'<br/>Skip asking"]
        AlreadyAns -- "No" --> RetryRephrase["If Retry: Rephrase failed question"]
        RetryRephrase --> DisplaySpeak["Display question & Speak TTS ('say')"]
        DisplaySpeak --> TimedInput["Timed Input (30-60s) via Interaction"]
        TimedInput --> HygieneCheck{"Speech Hygiene Check<br/>(Clipped mic / stutter?)"}
        HygieneCheck -- "Suspect" --> OneReask["Offer 1 optional re-ask"]
        HygieneCheck -- "Clean" --> EvalScore["Evaluate Answer (AnswerEvaluator)<br/>LLM or Offline Fallback"]
        OneReask --> EvalScore
        
        EvalScore --> FollowUpCheck{"Partial score (25 <= score < 70)<br/>& Follow-up required?"}
        FollowUpCheck -- "Yes" --> AskFollowUp["Ask focused follow-up on missing concept<br/>final_score = max(original, followup)"]
        FollowUpCheck -- "No" --> CheckFatal
        AskFollowUp --> CheckFatal{"Final Score < 25%?<br/>(Total lack of understanding)"}
        
        CheckFatal -- "Yes" --> AbortFatal["Print 'Commit aborted'<br/>Save FAILED session<br/>exit(1)"]
        CheckFatal -- "No" --> RecordQState["Record score & persist state file"]
        RecordQState --> NextQ{"More questions?"}
        MarkAns --> NextQ
        NextQ -- "Yes" --> QL_Start
        NextQ -- "No" --> DefenseDone(["All questions answered"])
    end
    
    QuestionLoop --> DefenseDone
    DefenseDone --> Scoring{"Overall Pass Criteria?<br/>(All >= 70% OR Offline Fallback completed)"}
    
    Scoring -- "PASSED" --> SaveSuccess["Build session (status: PASSED)<br/>Save to local SQLite db<br/>Sync to Central Ledger<br/>Remove state file<br/>exit(0)"]
    Scoring -- "FAILED" --> PromptRetry{"Prompt Developer:<br/>'Try Attempt N+1? (y/n)'"}
    
    PromptRetry -- "'y'" --> IncrementAttempt["attempts += 1<br/>Loop back to QuestionLoop"]
    IncrementAttempt --> QuestionLoop
    PromptRetry -- "'n' or Timeout" --> AbortFail["Print 'Commit aborted'<br/>exit(1)"]
```

---

## 3. Detailed Step-by-Step Walkthrough

Below is the chronological, level-by-level description of every block in [`cli.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/cli.py).

---

### Step 0: Interpreter Self-Healing & TTY Setup

#### What It Does
1. Checks the running Python interpreter version before any package imports are evaluated.
2. If running under Python `< 3.10` directly (`python -m understanding_agent.cli`), it searches known modern interpreter paths and re-executes the command under Python 3.10+.
3. Reconnects standard input and output streams to `/dev/tty` (or `CON` on Windows).

#### Why It Exists
- **Python Version Mismatch**: On macOS, system-installed `python3` is frequently Apple’s outdated Python 3.9. Importing modern typing features (e.g. PEP-604 union syntax `str | None` or structural pattern matching) causes cryptic syntax or runtime type errors before the CLI can even start.
- **Terminal Hijacking in Git Hooks**: Git pre-commit hooks run non-interactively by default. Standard input (`sys.stdin`) is redirected to `/dev/null` or closed by Git. Without reopening the controlling terminal (`/dev/tty`), interactive keyboard input or audio prompts would immediately fail with EOF.

#### How It Works Under the Hood
```python
# Interpreter self-heal (cli.py:14-43)
if __name__ == "__main__" and sys.version_info < (3, 10):
    # Searches ~/.local/bin/python3 and /usr/local/bin/managed_python3
    # If found and version >= 3.10, executes os.execve() preserving PYTHONPATH.
    # Otherwise exits with a helpful command: uv python install 3.12 --default
```
```python
# TTY Reconfiguration (cli.py:109-119)
con = 'CON' if sys.platform == 'win32' else '/dev/tty'
sys.stdin = open(con, 'r', encoding='utf-8')
sys.stdout = open(con, 'w', encoding='utf-8')
```

---

### Step 1: Environment Detection

#### Involved Modules
- Class: [`EnvironmentDetector`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/environment_detector.py#L32) in [`environment_detector.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/environment_detector.py)

#### What It Does
Discovers the runtime Git environment: the repository root path, current branch, repository name, primary programming language, and dominant framework.

#### How It Works Under the Hood
1. **Git Metadata**:
   - Executes `git rev-parse --show-toplevel` to discover the absolute path of the repository root (`project_root`).
   - Executes `git rev-parse --abbrev-ref HEAD` to obtain the active Git branch name (defaults to `"unknown"` if detached or bare).
   - Derives `repository` name using `os.path.basename(repo_root)`.
2. **Language Detection (`_detect_language`)**:
   - Walks the repository directory tree up to 2 directory levels deep.
   - Skips non-relevant directories (`.git`, `node_modules`, `venv`, `__pycache__`, `.tox`).
   - Counts file extensions mapped via `_EXTENSION_MAP` (e.g., `.py` $\to$ python, `.ts`/`.tsx` $\to$ typescript, `.go` $\to$ go, `.rs` $\to$ rust).
   - Returns the most frequent language.
3. **Framework Detection (`_detect_framework`)**:
   - **Node/JS/TS**: Reads `package.json` dependencies and `devDependencies`. Identifies frameworks: `NestJS`, `Next.js`, `React`, `Vue`, `Angular`, `Svelte`, `Express`, `Fastify`.
   - **Python**: Inspects `requirements.txt`, `pyproject.toml`, `Pipfile`, `setup.py` for `FastAPI`, `Django`, `Flask`.
   - **Go**: Inspects `go.mod` for `Gin`, `Fiber`.

#### Data Structure Output
```python
{
    "language": "python",
    "repository": "understanding-agent-hook",
    "branch": "main",
    "project_root": "/Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook",
    "framework": "unknown"
}
```

---

### Step 2: Change Detection

#### Involved Modules
- Class: [`ChangeDetector`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/change_detector.py#L30) in [`change_detector.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/change_detector.py)
- Supporting helpers: [`language_support.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/language_support.py)

#### What It Does
Inspects Git's staged index to identify what files and functions were modified, added, or deleted, and calculates overall change scale metrics.

#### How It Works Under the Hood
1. **Staged Files**: Runs `git diff --cached --name-status` to list all staged files and their status codes:
   - `A`: Added
   - `M`: Modified
   - `D`: Deleted
2. **Lockfile & Noise Filtering**: Automatically ignores lockfiles (`package-lock.json`, `poetry.lock`, `Cargo.lock`, `yarn.lock`, etc.).
3. **Critical Invariant for Non-Code & Unknown Files**:
   - Files with unknown extensions (e.g. `.pem`, `.env`, `.sqlite`) or non-code extensions (`.yaml`, `.json`, `.md`) are **not discarded**.
   - They are tracked with a synthetic function `<module>`.
   - *Why?* If non-code files were omitted, a commit containing only a leaked private key or configuration file would cause `cli.py` to exit 0 without running security gates!
4. **Function-Level AST / Regex Parsing**:
   - For code files, retrieves the staged version (`git show :<filepath>`) and HEAD version (`git show HEAD:<filepath>`).
   - In Python: uses Python's standard `ast` module to detect which functions or methods were modified, added, or deleted, along with internal function calls (`added_calls`).
   - In other languages (JS, TS, Go, Java, Rust): uses regex parsers in [`language_support.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/language_support.py).
5. **Scale Metrics Calculation**:
   - Executes `git diff --cached --numstat` to sum `total_added` and `total_deleted` lines of code.
   - Computes `is_large_change = (total_loc > 100) or (total_changed_functions > 3) or (len(files) > 2)`.

#### Data Structure Output
```python
{
    "files": [
        {
            "path": "understanding_agent/cli.py",
            "status": "modified",
            "language": "python",
            "changed_functions": [
                {"name": "main", "change_type": "modified", "added_calls": ["_write_state"]}
            ]
        }
    ],
    "stats": {
        "total_added": 12,
        "total_deleted": 4,
        "total_loc": 16,
        "total_changed_functions": 1,
        "is_large_change": False
    }
}
```

---

### Step 2b: Early Credential Gate (Pre-Check)

#### Involved Modules
- Functions: [`security_gate_enabled()`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/security_lens.py#L110), [`SecurityLens`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/security_lens.py#L115)
- Stage helpers: [`_print_gate_block()`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/hook_stages.py#L67)

#### What It Does & Why
Runs before the "empty staged changes" check. It directly examines the staged Git index to catch force-added private key files or credential files (e.g. `id_rsa`, `server.pem`, `.env.production`) even when no analyzable code files are staged.
If any critical finding is found, it prints actionable unstage commands (`git restore --staged <file>`) and aborts immediately with `sys.exit(1)`. Zero LLM calls are made.

---

### Step 3: Commit Context, Hashing, & Key Generation

#### Involved Modules
- Helper functions in [`commit_context.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/commit_context.py):
  - [`get_head_commit()`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/commit_context.py#L11)
  - [`get_current_commit_message()`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/commit_context.py#L110)
  - [`compute_attempt_key()`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/commit_context.py#L180)

#### How It Works Under the Hood
1. **`head_commit`**: Obtains the Git commit hash of current HEAD via `git rev-parse HEAD` (or returns `"INITIAL_COMMIT"` if repository has no commits yet).
2. **`commit_msg`**: Discovers the commit message being used:
   - On macOS: uses `ctypes` calling `libc.sysctl` with `KERN_PROCARGS2` to read the parent Git process's CLI arguments (e.g. `git commit -m "my message"`).
   - On Linux: inspects `/proc/{parent_pid}/cmdline`.
   - Fallback: reads `.git/COMMIT_EDITMSG`.
3. **`diff_hash`**: Computes SHA-256 over deterministic, sorted JSON serialization of the `changes` dictionary:
   ```python
   diff_hash = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()
   ```
4. **`attempt_key`**: Combines `head_commit`, `diff_hash`, and `commit_msg` into a stable SHA-256 key tracking attempts.

---

### Step 4: Technical Stack Detection

#### Involved Modules
- Class: [`StackDetector`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/stack_detector.py) in [`stack_detector.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/stack_detector.py)

#### What It Does
Builds a multi-faceted profile of the target repository's technology stack:
- Primary and secondary languages
- Frontend and backend frameworks
- Persistence engines (Postgres, Redis, MongoDB, SQLite)
- ORM/Data access layers (Prisma, SQLAlchemy, TypeORM, Hibernate)
- Test runners (pytest, jest, vitest, go test)
- Cloud & container infrastructure (Docker, Kubernetes, Terraform)

This profile is passed to downstream question generators to contextualize technical questions.

---

### Step 5: Code Graph & Context Builder (Budgeted Diffs)

#### Involved Modules
- Class: [`CodeGraph`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/code_graph.py#L15) in [`code_graph.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/code_graph.py)
- Class: [`ContextBuilder`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/context_builder.py#L20) in [`context_builder.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/context_builder.py)

#### What It Does & How
1. **Symbol Graph (`CodeGraph`)**: Scans all changed files to discover class definitions, method signatures, exports, and caller-callee dependencies.
2. **Budgeted Context Building (`ContextBuilder`)**:
   - Formulates the exact prompt context sent to LLMs.
   - **Line Budget Enforcement**: Limits diff explosion. Enforces a 35-line per-function cap and a 240-line total diff budget.
   - **Ranked Line Selection**:
     1. Prioritizes modified and added lines.
     2. Retains structural definitions (function heads, return statements).
     3. Drops boilerplate filler lines first.
   - **Nested Handlers (JS/TS)**: For nested UI handlers (e.g. `onClick` in React), automatically extracts and prepends the enclosing component's preamble so the LLM understands variable scopes.

---

### Step 6: ChangeKind Classification (Deterministic Tier)

#### Involved Modules
- Function: [`classify_change()`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/change_kind.py#L30) in [`change_kind.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/change_kind.py)

#### What It Does
An always-on, offline, deterministic categorization engine that scores changes across six architectural domains:
1. `ui`: User interface components, templates, styles.
2. `data_model`: Schemas, migrations, ORM entities, SQL definitions.
3. `control_flow`: Business logic, state machines, API routes, handlers.
4. `infra_config`: CI workflows, Dockerfiles, environment configurations.
5. `tests`: Unit tests, integration specs, mock fixtures.
6. `docs`: Markdown files, comments, API documentation.

Outputs a `dominant` domain, proportional weights, and an auditable list of detected signals (e.g. `["route definition added", "ORM model modified"]`). This classification steers the focus of questions.

---

### Step 7: Security Profiling, Tool Evidence, Practice Packs, & Identity

#### Involved Modules
- [`SecurityLens`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/security_lens.py)
- [`collect_evidence()`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/evidence_collector.py#L25) in [`evidence_collector.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/evidence_collector.py)
- [`practice_pack_hint()`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/practice_packs.py)
- [`IdentityDetector`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/identity.py)

#### What It Does
1. **Security Profiling**: Evaluates diffs against 20+ vulnerability sink patterns (SQL injection, XSS, SSRF, command injection, weak cryptography, unsafe deserialization, LLM prompt injection).
2. **Tool Evidence**: Checks if static security tools (`semgrep`, `gitleaks`) are installed on the developer's machine and runs them against staged files. Gathers concrete finding locations (file, line number, message).
3. **Practice Packs**: Selects domain-specific questions tailored to the stack (e.g. React hook dependency contracts or Go concurrency safety).
4. **Zero-Touch Identity**: Resolves the Git committer identity (`user.name`, `user.email`) to allow automatic JIT user provisioning on the team's central server.

---

### Step 8: Full Security Gate Hard-Block

#### Involved Modules
- Method: `lens.find_critical_findings(context)`
- Stage Helper: `_print_gate_block(critical, env, project_root)`

#### How It Works Under the Hood
Scans newly added lines for real credentials:
- Well-known vendor tokens (AWS `AKIA...`, GitHub `ghp_...`, Slack, Stripe, OpenAI).
- High-entropy strings ($\ge 20$ chars, $\ge 3.5$ bits/char Shannon entropy, 3+ character classes) assigned to secret-named variables (`api_key`, `secret`, `token`, `password`).
- Private key headers (`-----BEGIN RSA PRIVATE KEY-----`).
- Database connection strings containing embedded passwords.

#### Policy
**Hard block**: If critical findings exist, the hook prints the redacted findings and unstaging commands, then immediately calls `sys.exit(1)`. No oral defense can bypass a real credential leak.

---

### Step 9: Coding Standards Audit & Advisory Presentation

#### Involved Modules
- Class: [`CodingStandardsChecker`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/coding_standards.py#L70) in [`coding_standards.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/coding_standards.py)

#### What It Does
Executes 15 deterministic checks strictly scoped to **lines added in this commit** (never penalizing legacy pre-existing code):
- Function length (max 50 lines for added functions)
- Nesting depth (max 3-4 levels)
- React rules (hook dependencies, direct state mutation, missing cleanups)
- JS/TS idioms (`var` usage, loose `==` equality)
- Python standards (mutable default arguments, specific exception handling)
- Go standards (unhandled error returns)
- Input validation on public endpoints

Prints a colored terminal report displaying passed checks (`[Good]`) and failed checks (`[Violation]`). It also displays any tool evidence findings from `semgrep` or `gitleaks`.

---

### Step 10: Non-Interactive / Autonomous Agent Mode (Judas J2)

#### What It Does & Why
Autonomous AI agents (such as Claude Code, GitHub Copilot CLI, or background scripts) create commits via Git. When the pre-commit hook runs in a headless environment or subshell without a human, reading `/dev/tty` encounters EOF, which would fail the oral defense and block the agent from committing.

#### The Policy
Instead of failing or bypassing security silently:
1. Detects `UNDERSTANDING_AGENT_MODE=agent` or `not _interactive_input_available()`.
2. Deterministic credential gates and coding standards audits **still execute and block if violated**.
3. Bypasses the interactive oral defense loop.
4. Generates a session payload stamped with `verification="agent-unverified"` (or `"unattended-unverified"`).
5. Persists the session locally to SQLite and pushes it to the central ledger.
6. Exits `0`. CI pipelines can then require human sign-off on agent-authored commits.

---

### Step 11: Standards Violation Gateway (Fix vs. Proceed)

#### What It Does
If any coding standards failed, and a human is present, prompts the developer:
```text
Coding standards violations detected (2 failed).
Do you want to fix these violations or proceed further?
  [1] Fix violations
  [2] Proceed further
```
- If the developer chooses **[1] Fix**: records a `FAILED` session in the database and exits with code `1`, allowing them to clean up the code.
- If the developer chooses **[2] Proceed**: proceeds to oral questioning. The failed standards are passed to the question generator, which formulates questions asking the developer to explain or justify why the violation was necessary.

---

### Step 12: Change Summary Generation

#### Involved Modules
- Class: [`ChangeSummary`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/change_summary.py) in [`change_summary.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/change_summary.py)

#### What It Does
Synthesizes a structured summary of the changes:
- `what_changed`: Concise description of architectural changes.
- `intent`: The probable feature, bugfix, or refactor intent.
- `impact`: Potential side-effects on downstream callers or data storage.

*Offline fallback*: If LLM API keys are absent or network requests fail, generates a deterministic summary using changed function names and file paths.

---

### Step 13: Attempt Tracking & Question Generation

#### Involved Modules
- Class: [`QuestionGenerator`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/question_generator.py) in [`question_generator.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/question_generator.py)
- Module: [`change_intelligence.py`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/change_intelligence.py)

#### Attempt State Management
- Reads `.git/understanding_agent_state.json` and loads prior commit sessions from `.git/understanding_sessions.db`.
- Scoped to the current Git HEAD commit.
- Calculates attempt number: `attempts = max(recorded_attempts + 1, state_attempts + 1, 1)`.

#### Question Caching on Retries
If the developer failed a previous attempt and retries without changing the staged diff (`diff_hash` matches):
- Reuses the cached question set.
- Preserves questions already answered with a passing score ($\ge 70\%$).
- If a question was previously failed, [`FollowUpGenerator.rephrase_failed_question()`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/followup_generator.py) rephrases it to give the developer a fresh perspective on the concept.

#### Fresh Generation (When Diff Changes)
1. **Master Analysis Path (Opt-in)**: If `UNDERSTANDING_AGENT_MASTER_ANALYSIS=1`, performs a single unified LLM call that extracts change classifications, verified advisory findings citing diff lines, and questions accompanied by evaluation checklists.
2. **Standard Generation Path**:
   - Scales question count to diff size: Small (2–3), Medium (3–5), Large (6–8).
   - Enforces an 80/20 ratio: 80% logic/diff questions, 20% security/standards questions.
   - Sets type-specific time limits (Architecture: 60s, Reasoning: 45s, Security: 45s, Implementation: 30s).
3. **Offline Fallback Pool**: If offline, generates deterministic questions from code symbols and security profiles.

---

### Step 14: Interactive Oral Defense Loop (Per Question)

#### Involved Modules
- Class: [`Interaction`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/interaction.py) (Terminal I/O, speech synthesis, dictation, audio capture)
- Class: [`AnswerEvaluator`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/answer_evaluator.py) (LLM-based and heuristic evaluation)
- Function: [`classify_answer()`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/answer_hygiene.py#L25) (Speech hygiene detection)
- Class: [`FollowUpGenerator`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/followup_generator.py) (Targeted follow-up probing)

#### Execution Flow per Question

```
[For Each Question in valid_questions]
  │
  ├─ 1. Check if Already Answered (best_score >= 70%)
  │      └─► YES: Print "✓ Already Answered (Attempt #X)", add score, continue to next question.
  │
  ├─ 2. If Retry (Attempt > 1 and failed previously):
  │      └─► Rephrase question with fresh wording.
  │
  ├─ 3. Present Question:
  │      ├─► Print question text to terminal in Cyan/Bold.
  │      └─► Speak question aloud via TTS (`say` command on macOS).
  │
  ├─ 4. Timed Input:
  │      ├─► Displays live countdown timer (e.g. 45s or 60s).
  │      ├─► Accepts typed input or audio dictation.
  │      └─► Handles timeout cleanly.
  │
  ├─ 5. Speech Hygiene Check:
  │      ├─► classify_answer(text) checks for clipped audio, ASR stutter, or accidental empty submits.
  │      └─► If suspect: offers ONE optional re-ask prompt ("Say it again or press Enter to keep").
  │
  ├─ 6. Evaluation (AnswerEvaluator.evaluate):
  │      ├─► Evaluates answer against expected concepts and criteria.
  │      ├─► Assigns score (0 to 100) and understanding label ('good', 'partial', 'insufficient').
  │      └─► Extracts covered points, missing concepts, and whether follow_up_required.
  │
  ├─ 7. Targeted Follow-Up (Partial Understanding):
  │      ├─► Trigger: 25 <= initial_score < 70 AND follow_up_required is True.
  │      ├─► FollowUpGenerator creates a concise follow-up targeting specifically the missing concept.
  │      ├─► Developer answers follow-up (max 45s).
  │      ├─► Evaluates follow-up; final_score = max(initial_score, followup_score).
  │      └─► Replaces evaluation result.
  │
  ├─ 8. Immediate Abort on Total Failure:
  │      ├─► Trigger: final_score < 25%.
  │      ├─► Explains that commit is aborted due to total lack of understanding.
  │      ├─► Saves FAILED session payload to database and sends telemetry.
  │      └─► Calls sys.exit(1) immediately without asking remaining questions.
  │
  └─ 9. Persist State:
         └─► Writes current question progress, scores, and answers to .git/understanding_agent_state.json.
```

---

### Step 15: Scoring, Verification, Persistence, & Telemetry

#### Involved Modules
- Payload Builder: [`build_session_payload()`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/session_payload.py)
- Storage Engine: [`save_session()`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/session_store.py)
- Network Client: [`ServerClient`](file:///Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook/understanding_agent/server_client.py)

#### Passing Logic
1. **Verification Tagging**:
   - `llm-verified`: Questions were generated by LLM **and** all evaluations were scored by LLM.
   - `llm-unverified`: Fallback offline heuristics were used for either question generation or evaluation.
2. **Pass Criteria**:
   - Every individual question achieved `best_score >= 70%`.
   - **Fail-Open Exemption**: If external LLM APIs are unreachable and offline fallback questions were presented, completing all answers without timeouts marks the session as `PASSED` (preventing developer blockage while recording `llm-unverified`).
3. **Storage & Sync**:
   - Builds Schema v3 session dictionary via `build_session_payload()`.
   - Saves record to local SQLite database: `.git/understanding_sessions.db` (permissions `0600`, auto-pruned to 500 records).
   - Asynchronously sends session to the team's central server via `ServerClient().send()`. If offline, enqueues to `.git/understanding_sync_queue.json`.
4. **Resolution**:
   - **Passed**: Prints `✓ Commit allowed: Understanding demonstrated.`, removes `.git/understanding_agent_state.json`, and exits with status `0`.
   - **Failed**: Informs developer of failure and prompts: `Would you like to try Attempt N+1? (y/n)`.
     - Answering `y` increments `attempts` and loops back into the oral defense.
     - Answering `n` aborts with `sys.exit(1)`.

---

## 4. Exit Codes & State Reference

### System Exit Codes

| Exit Code | Trigger Condition | Session Status Recorded |
| :---: | :--- | :--- |
| **`0`** | All questions passed ($\ge 70\%$), or offline fail-open completed | `PASSED` (`llm-verified` or `llm-unverified`) |
| **`0`** | Non-interactive agent mode (`UNDERSTANDING_AGENT_MODE=agent` or headless) | `PASSED` (`agent-unverified` or `unattended-unverified`) |
| **`0`** | Empty staged changes (no files staged) | None (early clean exit) |
| **`1`** | Critical live credential / secret detected in staged changes | None (Gate abort before session build) |
| **`1`** | Developer chose `[1] Fix` on coding standards violations | `FAILED` (`llm-unverified`) |
| **`1`** | Fatal failure on any question ($\text{score} < 25\%$) | `FAILED` (`llm-verified` or `llm-unverified`) |
| **`1`** | Overall understanding failed and developer declined retry (`n`) | `FAILED` (`llm-verified` or `llm-unverified`) |
| **`1`** | User pressed Ctrl+C or closed standard input | `FAILED` (interrupted) |

---

### Environment Variables

| Variable | Values | Purpose |
| :--- | :--- | :--- |
| `UNDERSTANDING_AGENT_MODE` | `agent`, `interactive` | Sets agent record-only mode (bypasses oral defense, runs gate/audit, logs as unverified). |
| `UNDERSTANDING_AGENT_SECURITY_GATE` | `on` (default), `off` | Emergency killswitch to bypass the deterministic credential blocker. |
| `UNDERSTANDING_AGENT_MASTER_ANALYSIS` | `1`, `0` (default) | Opts into the unified single-call LLM Master Analysis pass. |
| `UNDERSTANDING_AGENT_ANSWER_HYGIENE` | `on` (default), `off` | Enables or disables detection of clipped speech / mic stutters. |
| `NOUS_API_KEY` / `OPENAI_API_KEY` | string | API key for LLM inference (loaded from shell or local `.env`). |
| `UNDERSTANDING_AGENT_SERVER_URL` | URL | Central ledger endpoint for syncing commit defense proof across the team. |

---

### File Artifacts Maintained in `.git/`

| File Path | Format | Lifetime | Purpose |
| :--- | :--- | :--- | :--- |
| `.git/understanding_agent_state.json` | JSON | Transient across retry attempts; deleted on successful commit. | Tracks attempt count, HEAD hash, diff hash, and cached question scores. |
| `.git/understanding_sessions.db` | SQLite 3 | Persistent local store (0600 permissions, pruned at 500 rows). | Local audit trail of all pass/fail defense sessions. |
| `.git/understanding_sync_queue.json` | JSON | Persistent until network restores. | Offline queue of session payloads waiting to sync to the central server. |

---

## 5. Architectural Invariants & Failure Modes Summary

1. **Security Isolation**: `SecurityLens` runs before any prompt, standards audit, or LLM invocation. Secrets cannot be talked away.
2. **Context Budgeting**: Diffs are strictly bounded ($240$ lines max, $35$ lines/function max). Large refactors never crash token limits.
3. **Fail-Open Policy**: Network drops and API timeouts never indefinitely block git commits. Commits proceed with fallback questions and honest audit markings.
4. **Zero-Touch Committer Identity**: The agent extracts author metadata automatically from Git, requiring zero login ceremonies for individual developers.
5. **Stateful Cycle Scoping**: Questions and attempt counts belong strictly to the current `HEAD` commit. When a commit succeeds and HEAD advances, state resets cleanly.
