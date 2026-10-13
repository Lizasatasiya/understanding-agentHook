# 🧠 Understanding Agent: AI-Powered Code Understanding Pre-Commit Hook

> An intelligent, voice-interactive Git pre-commit hook that verifies developers actually understand the code they are committing — now **multi-language**, **security-aware**, and **domain-aware**.

**Supported stacks**: Python (incl. ML/data-science), JavaScript/TypeScript (React, Next, Vue, Angular, Node, Express, NestJS), C#/.NET (ASP.NET Core, EF Core), Go, Rust, Java, Kotlin, Ruby, PHP, Swift, C/C++, SQL. Frameworks and domains are detected from your manifests (package.json, pyproject/requirements, *.csproj, go.mod, Cargo.toml, ...) and questions adapt accordingly.

---

## 📌 Table of Contents

- [The Problem: Why This Exists](#-the-problem-why-this-exists)
- [How It Works: Core Architecture](#-how-it-works-core-architecture)
- [Dual-Mode Engine: Micro vs. Macro Changes](#-dual-mode-engine-micro-vs-macro-changes)
- [Key Features](#-key-features)
- [Project Structure & Components](#-project-structure--components)
- [Tech Stack & Dependencies](#-tech-stack--dependencies)
- [Installation & Setup](#-installation--setup)
- [Integration with Any Git Repository](#-integration-with-any-git-repository)
- [Audio & Voice Agent Guide](#-audio--voice-agent-guide)
- [Design Decisions & Trade-Offs](#-design-decisions--trade-offs)

---

## 🎯 The Problem: Why This Exists

With the rise of AI coding assistants (GitHub Copilot, Cursor, ChatGPT, Gemini, Claude), developers can generate hundreds of lines of code in seconds. While productivity increases, it introduces critical organizational risks:

1. **Vibe Coding & Blind Commits**: Developers commit complex logic, regexes, or async workflows without understanding edge cases or side effects.
2. **Hidden Invariants & Logic Bombs**: Automated code often works on the happy path but fails silently during failure states, concurrency spikes, or boundary conditions.
3. **Rubber-Stamp Code Reviews**: Reviewers assume the author understands the code, leading to unvetted changes entering `main`.

### The Solution: The "Oral Defense" Git Hook
`understanding-agent` acts as an automated, interactive **oral defense** at commit time. Whenever a developer runs `git commit`, the hook:
1. Analyzes the staged diff and cross-file dependencies.
2. Formulates 2–3 targeted questions testing **logic, data flow, invariants, and edge cases**.
3. Speaks the questions aloud and accepts answers either by **typing** or by **voice** via your microphone (transcribed using local OpenAI Whisper).
4. Evaluates the answers using an LLM evaluator against the actual AST changes.
5. **Allows the commit** if average understanding score is **> 75%**, or **aborts the commit** if understanding is missing.

---

## 🏗️ How It Works: Core Architecture

```mermaid
flowchart TD
    A["git commit -m '...'"] --> B["1. Environment & Change Detection\n(AST Hunk Filtering + Scale Metrics)"]
    B --> C["2. CodeGraph Builder\n(Extract Callers, Callees & Signatures)"]
    C --> D["3. Context Builder & Skeletonizer\n(Compress Large Diffs > 35 lines)"]
    
    D --> G{"Credential Gate\n(deterministic, no LLM)"}
    G -- "live key staged" --> X["⛔ Commit Blocked Immediately\n(zero prompts, zero LLM calls)"]
    G -- "clean" --> E{Commit Scale Check}
    
    E -- "< 100 LoC (Small)" --> F1["Micro Pipeline\n- Granular Function Diff\n- 2-3 Line-Level Logic Questions"]
    E -- "100-250 LoC (Medium)" --> F2["Medium Pipeline\n- 3-5 Questions"]
    E -- "> 250 LoC (Large)" --> F3["Macro Pipeline\n- 6-8 Architectural Questions"]
    
    F1 --> H["4. Coding Standards Audit\n(deterministic) → fix or proceed"]
    F2 --> H
    F3 --> H
    H --> I["5. Summary + Question Generation\n(via central LLM provider manager)"]
    I --> J["6. Interactive Terminal UI\n- macOS 'say' TTS\n- [Tab] Mic Input (Local Whisper STT)\n- ANSI Live Countdown Timer"]
    J --> K["7. Answer Evaluator\n(Rubric: Correctness, Understanding, Reasoning, Specificity)"]
    
    K --> L{Score >= 75%?}
    L -- "Yes" --> M["✅ Commit Allowed"]
    L -- "No" --> N["⛔ Commit Aborted (Attempt Saved for Retry)"]
```

---

## ⚡ Adaptive Question Scaling: Small vs. Medium vs. Large Changes

The hook automatically adapts its evaluation strategy based on the size and complexity of the commit:

| Feature | Small (`< 100 LoC`) | Medium (`100–250 LoC`) | Large (`> 250 LoC`) |
|---|---|---|---|
| **Primary Focus** | Line-by-line logic, return values, local loops | Cross-function data flow, component coordination | System architecture, data contracts, failure modes |
| **Questions** | 2–3 focused questions | 3–5 questions | 6–8 architectural questions (never essay-length) |
| **Diff Representation** | Full per-function unified diff | Compressed per-function diff | Semantic skeleton (signatures, branches, calls) |
| **Question Style** | Specific, under 25 words, answerable in 30–45s | Same constraints, broader scope | Architectural, no syntax trivia |
| **Evaluation Rubric** | Specific line & data accuracy | Component interaction accuracy | Conceptual grasp of state invariants, error boundaries & system intent |

Within every scale, ~80% of questions target the actual diff and at most ~20% address flagged coding-standards violations.

---

## 🚀 Key Features

- **🎙️ Voice-First Interaction**: Reads questions aloud (`say`) and transcribes developer speech in real-time using local OpenAI Whisper (`sounddevice` / `pyaudio`). Press **[Tab]** to toggle speech capture.
- **🔌 Central LLM Provider Manager**: One module (`chromatica.py`) resolves provider, model, base URL, and API key. Switch between Nous Research, Groq, OpenAI, OpenRouter, Together, Ollama (local), or ANY OpenAI-compatible endpoint by changing ONE environment variable — no code changes, no five-variable dances.
- **⚡ Fast, Resilient Execution**: Sub-second question generation with the configured provider; retries with exponential backoff on transient errors; robust JSON extraction from reasoning models.
- **🎯 Multi-Language Change Analysis**: Function-level change detection across 15+ languages — native Python `ast` for Python, line-anchored definition matching for TS/JS/C#/Go/Rust/Java and more. Only genuinely-touched functions become questions.
- **🛡️ Security Lens (deterministic)**: Every staged change is scanned for security-relevant surfaces — injection sinks, auth flows, hardcoded secrets, unsafe deserialization, command injection, LLM-output trust, disabled TLS, cleartext HTTP. Security-relevant changes always get one targeted security question (never a hard block). **Exception: stealable credentials hard-block the commit BEFORE any prompt or LLM call** — git history is permanent, so no answer can make committing a real credential safe. The gate covers: 25+ vendor-pinned token formats (AWS, Google, Stripe, Anthropic, GitHub, GitLab, npm, PyPI, Slack, Telegram, SendGrid, Twilio, ...), private key material, connection strings with inline credentials (`postgres://user:***@...`), basic-auth URLs, high-entropy values assigned to secret-named variables (Shannon-entropy gated, with a `# pragma: allow-secret` escape hatch), and **staged key files** (`.pem`, `.p12`, `id_rsa`, `.npmrc`, `.netrc`, ... — detected by filename alone, contents never read). Exact vendor formats block even in test files (a real leaked key is real anywhere); fixture-shaped values in tests stay exempt. The gate scans the raw staged diff (module-level code included), redacts secrets in its output, suggests `git restore --staged <file>` per finding, calls out staged-but-gitignored files, and can be disabled per-commit with `UNDERSTANDING_AGENT_SECURITY_GATE=off`.
- **🚫 No Wasted LLM Calls**: The credential gate and coding-standards audit run BEFORE any LLM call. A blocked credential or a "fix violations" choice costs zero API spend. Same-diff retries reuse cached questions.
- **🔍 Honest Verification Marking**: If the LLM is unreachable, the hook asks its offline fallback questions and records your answers **without assigning any score** — a substantive answer lets the commit proceed (fail-open), the session is stored as `llm-unverified`, and the terminal says so. Dashboards never show an offline fallback pass as a verified pass. Strict teams set `UNDERSTANDING_AGENT_FAIL_MODE=closed` to block instead.
- **🔬 Evidence-Driven Questions**: Reuses your already-installed scanners (semgrep, gitleaks) and test-gap analysis as *question evidence* — scanner findings become "explain this finding" questions, inheriting scanner recall without false-positive-blocking commits.
- **📚 Domain Practice Packs**: Framework- and domain-aware best-practice topics: React state/effect invariants, NestJS DTO validation boundaries, .NET async/CancellationToken and EF Core behavior, ML train/test separation and reproducibility, data-pipeline idempotency, SQL migration safety.
- **🔄 Smart State Persistence**: Saves attempts in `.git/understanding_agent_state.json` hashed against the staged diff. If an attempt fails, developers can retry without regenerating or paying for redundant API calls. The sessions log is auto-pruned (default: last 500 sessions).
- **🔄 Targeted Follow-Up Engine**: If an answer shows partial understanding (score 25–70%), the hook generates a precise follow-up question targeting the missing concept, spoken and answered like any other question.
- **📊 Opt-In Telemetry**: Dispatches session audit payloads to a self-hosted dashboard for team-wide code understanding metrics. Disabled by default; set `UNDERSTANDING_AGENT_TELEMETRY_URL` to enable.

### Configuration (Environment Variables)

| Variable | Default | Purpose |
|---|---|---|
| `UNDERSTANDING_AGENT_PROVIDER` | `nous` | LLM provider preset: `nous`, `groq`, `openai`, `openrouter`, `together`, `ollama`, `custom` |
| `UNDERSTANDING_AGENT_MODEL` | preset default | Model id for any provider (e.g. `deepseek/deepseek-v4-flash` on Nous, `llama-3.3-70b-versatile` on Groq) |
| `UNDERSTANDING_AGENT_BASE_URL` | preset default | Override to point at ANY OpenAI-compatible endpoint (this alone enables custom providers) |
| `UNDERSTANDING_AGENT_API_KEY` | — | API key for the active provider. Legacy `NOUS_API_KEY` / `GROQ_API_KEY` still work |
| `UNDERSTANDING_AGENT_FAIL_MODE` | `open` | `open`: if the LLM is unreachable, the commit is allowed with a warning and marked `llm-unverified`. `closed`: the commit is blocked (strict verification) |
| `UNDERSTANDING_AGENT_TELEMETRY_URL` | unset (off) | Endpoint to POST session audit payloads to (e.g. `http://dashboard.internal:8000/understanding-session`) |
| `UNDERSTANDING_AGENT_SECURITY_GATE` | on | Hard-blocks commits containing live credentials (AWS keys, private keys, API tokens). `off` disables the credential gate for a commit (questions still run) |
| `UNDERSTANDING_AGENT_MAX_SESSIONS` | `500` | Cap on persisted session records in `.git/understanding_sessions.json`. Values < 2 disable pruning |
| `UNDERSTANDING_AGENT_DISABLE_THINKING` | on | Suppresses model thinking/reasoning (sends `reasoning_effort=none` on Nous). Bench-measured: deepseek-v4-flash drops from ~51s to ~10s per call. Set `off` to re-enable thinking |

**Privacy note**: your staged diff (source code) is sent to the configured LLM provider for question generation and evaluation — that is how the hook works. It is never sent anywhere else unless you explicitly set `UNDERSTANDING_AGENT_TELEMETRY_URL`.

---

## 📂 Project Structure & Components

Every module is named for a Lady Gaga song — the first line of each docstring states the module's plain-words job; the Gaga is flavor, never a comprehension tax.

```text
understanding-agent-hook/
├── .github/workflows/ci.yml  # CI: unit suite + 3 e2e suites (ubuntu/macos × py3.12/3.14)
├── .pre-commit-hooks.yaml    # Hook definitions for the pre-commit framework
├── pyproject.toml            # Canonical package manifest (console scripts, extras)
├── setup.py                  # Thin shim for legacy tooling
├── LICENSE                   # MIT License
├── PLAN.md                   # PoC → production hardening plan & status (historic)
├── PRODUCTION_PLAN.md        # Production restructure plan ("Chromatica track")
├── scripts/                  # LLM latency/capture benches (bench_*.py)
├── tests/                    # 180 unit tests + 3 e2e suites (gate, security, polyglot)
└── understanding_agent/
    ├── __init__.py
    ├── applause.py            # "I live for the applause" — showrunner entrypoint (pre-commit main)
    ├── gypsy.py               # environment detection: which venue tonight (repo/branch/author)
    ├── enigma.py              # staged-change detection: multi-language diff parser & scale metrics
    ├── scheisse.py            # per-language function extraction (TS/JS/C#/Go/Rust/Java/...)
    ├── born_this_way.py      # stack detection from manifests (package.json, pyproject, *.csproj, ...)
    ├── paparazzi.py           # security lens + THE credential gate (hard-blocks stealable secrets)
    ├── teeth.py               # deterministic coding-standards audit (changed lines only)
    ├── rain_on_me.py          # scanner evidence (semgrep/gitleaks/test-gaps) as question input
    ├── the_cure.py            # domain best-practice question topics per stack
    ├── bad_romance.py         # caller/callee dependency graph
    ├── joanne.py              # diff extraction & semantic skeletonizer (large diffs)
    ├── shallow.py             # the surface summary of a change
    ├── poker_face.py          # scale-aware question generator (2–8 questions, 80/20 ratio)
    ├── chromatica.py          # central LLM provider manager — provider/model/key in ONE place
    ├── just_dance.py          # LLM transport: raw HTTP, retry, JSON extraction (zero-dep core)
    ├── telephone.py           # terminal UI: timed input, TTS ('say'), [Tab] mic dictation
    ├── judas.py               # answer evaluator — betrays your bluff; offline answers go unscored
    ├── million_reasons.py     # targeted follow-up questions on missing concepts
    ├── the_fame.py            # EvaluationResult dataclass (the shape of a score)
    ├── marry_the_night.py     # commit context: HEAD, commit message, attempt keys & numbering
    ├── always_remember.py     # session persistence, dedupe, reconciliation, pruning
    ├── monster_ball.py        # FastAPI dashboard API (opt-in [ui] extra)
    ├── john_wayne.py          # opt-in telemetry client (only rides with UNDERSTANDING_AGENT_TELEMETRY_URL)
    └── ui/                    # vanilla JS/CSS single-page dashboard (index.html, app.js, style.css)
```

---

> **Scope**: code understanding questions are generated for the staged code files in the supported language list above. Docs/config-only commits are skipped.

## 🛠️ Tech Stack & Dependencies

### Core Python Runtime
- **Python 3.10+** (enforced via `python_requires` in setup.py; tested on 3.12 & 3.14 on macOS)
- **Standard Library Zero-Dependency Core**: The core git parsing, AST analysis, LLM transport, terminal UI, and HTTP client use native Python modules (`ast`, `subprocess`, `hashlib`, `http.client`, `ssl`, `difflib`, `termios`, `tty`, `select`).

### LLM Infrastructure
- **Provider**: any OpenAI-compatible API — Nous Research (default), Groq, OpenAI, OpenRouter, Together, Ollama/local, or a custom endpoint via `UNDERSTANDING_AGENT_BASE_URL`
- **Default model**: `qwen/qwen3-coder-30b-a3b-instruct` on Nous — override with `UNDERSTANDING_AGENT_MODEL` (or legacy `NOUS_MODEL` / `GROQ_MODEL`)
- **Configuration**: one central manager (`llm_manager.py`) — provider, model, base URL, and key resolution in one place; switching platforms is one env var
- **HTTP Layer**: Native `http.client.HTTPSConnection` with socket cleanup, retry + exponential backoff, and robust JSON extraction (handles reasoning tags, code fences, trailing commas)

### Optional Extras
- **Voice** (`pip install -e ".[voice]"`): `sounddevice`, `openai-whisper`, `numpy` + system `ffmpeg`
- **Dashboard UI** (`pip install -e ".[ui]"`): `fastapi`, `uvicorn`, `pydantic>=2`

---

## ⚙️ Installation & Setup

### 1. Clone the Repository
```bash
git clone https://github.com/Lizasatasiya/understanding-agentHook.git
cd understanding-agentHook
```

### 2. Configure Your LLM Provider
Obtain an API key from [portal.nousresearch.com](https://portal.nousresearch.com/) (default) or use any OpenAI-compatible provider:
```bash
export UNDERSTANDING_AGENT_API_KEY="..."
# Optional: switch provider/model with one variable each
# export UNDERSTANDING_AGENT_PROVIDER=groq
# export UNDERSTANDING_AGENT_MODEL=llama-3.3-70b-versatile
```
*(The hook also reads `.env` / `.env.local` from the current directory, the git repository root, and `~/.config/understanding-agent/.env`. Legacy `NOUS_API_KEY` / `GROQ_API_KEY` are still honored.)*

### 3. Install Voice Dependencies (macOS)
```bash
# Install ffmpeg for microphone recording
brew install ffmpeg

# Install voice dependencies into your Python environment
pip install sounddevice openai-whisper numpy
```

### 4. Install Locally in Editable Mode
```bash
pip install -e .
```

---

## 🔌 Integration with Any Git Repository

To enable the code understanding check in any target repository:

### 1. Add `.pre-commit-config.yaml` to Your Project
In your project root, add:

```yaml
repos:
-   repo: https://github.com/Lizasatasiya/understanding-agentHook.git
    rev: main
    hooks:
    -   id: understanding-agent
```

*(For local testing/development, point `repo` to the local path: `repo: /path/to/understanding-agent-hook`)*

### 2. Install the Pre-Commit Hook
```bash
# In your target repository
pre-commit install
```

### 3. Commit As Usual
```bash
git add .
git commit -m "Implement new feature"
```

The understanding check will automatically run in your terminal before the commit finishes.

---

## 🎙️ Audio & Voice Agent Guide

### How Voice Works in Pre-Commit
When `pre-commit` runs hooks, it runs inside an isolated virtualenv sandbox. The hook includes a **Dynamic Host Bridge** in [`interaction.py`](understanding_agent/interaction.py) that detects host Python site-packages. This ensures `whisper`, `sounddevice`, and `numpy` remain accessible without reinstalling heavy models inside every sandbox.

### Voice Flow
1. **Auditory Cue**: When a question appears, the hook speaks it aloud via macOS `say`.
2. **Keyboard or Mic Choice**: 
   - Type your response directly, or
   - Press **`[Tab]`** to trigger voice listening.
3. **Live Streaming Transcription**: As you speak, transcribed words appear live in your terminal.
4. **Editable Review**: Transcribed text lands directly in your editable prompt. You can adjust the text or hit **`Enter`** to submit.

---

## 💡 Design Decisions & Trade-Offs

1. **Why a hosted OpenAI-compatible API over local LLMs?**
   - Git pre-commit hooks run synchronously. Local 7B/14B models on CPU/GPU take 8–15 seconds to load and generate tokens. Hosted inference delivers complete summaries and questions in **< 1.5 seconds**, keeping commit latency negligible. The provider manager makes the choice reversible per-team: point `UNDERSTANDING_AGENT_PROVIDER=ollama` at a local server and nothing else changes.
2. **Why scale questions 2–8 instead of a flat 2–3?**
   - A 500-LoC architectural change genuinely needs more coverage than a 20-LoC fix, but an unbounded exam turns every commit into a 10-minute exam — developers would bypass the hook (`git commit --no-verify`). The scale ladder (2–3 / 3–5 / 6–8) with hard length limits ("under 25 words, answerable in 30–45 seconds") balances rigor against velocity.
3. **Why hard-block credentials before asking questions?**
   - An oral defense verifies understanding; it cannot verify that a secret is safe to leak. Git history is permanent and cloned everywhere — the only safe time to stop a credential is before the commit object exists. So the gate runs before any prompt and costs zero LLM spend.
4. **Why fail open (by default) but record answers without a score?**
   - A third-party LLM outage must not freeze every commit in the org — that turns a hook into an outage amplifier. But failing open silently would let dashboards claim verified commits that were scored by a keyword-matching fallback. So offline answers are stored **unscored** with the `llm-unverified` marker: fail-open ergonomics AND honest telemetry. Strict teams set `UNDERSTANDING_AGENT_FAIL_MODE=closed`.
5. **Why deterministic checks before any LLM call?**
   - The credential gate, coding-standards audit, security lens, and evidence collection are pure pattern/AST analysis — fast, free, and reproducible. Running them first means blocked commits spend nothing, and the LLM only ever sees diffs that already passed the deterministic filters.
6. **Why semantic diff skeletons for large diffs?**
   - Diffs > 50 lines consume thousands of tokens, triggering LLM "lost in the middle" hallucinations. Compressing diffs to signatures, branch conditions, and external calls focuses the LLM on architecture rather than boilerplate syntax.

---

## 🧪 Running Tests

The suite is stdlib-only (no LLM, no network) using Python's built-in `unittest` runner. Python **3.10+** required; CI runs 3.12 and 3.14 on Ubuntu and macOS.

```bash
python3 -m unittest discover -s tests   # 180 unit tests
python3 tests/e2e_gate_first.py         # scratch git repo: gate blocks w/ zero LLM calls,
                                        # offline fallback still asks the security question,
                                        # sessions prune
python3 tests/e2e_security_gate.py      # real `git commit` with a live-shaped key is blocked
python3 tests/e2e_polyglot_fixture.py   # NestJS/TS + .NET/C# + ML/Python question pipeline
```

CI runs exactly this matrix on every push/PR (`.github/workflows/ci.yml`).

---

## 🖥️ Platform Support

| Platform | Status | Notes |
|---|---|---|
| macOS | ✅ Full | TTY input, `say` TTS, [Tab] mic dictation, KERN_PROCARGS2 commit-message recovery |
| Linux | ✅ Core | Question/answer flow over TTY works (termios/tty). No TTS (`say` is macOS-only) — espeak/festival integration is future work. Commit-message recovery falls back to `/proc` and `ps`. CI-verified (ubuntu-latest). |
| Windows | ⚠️ Untested | `termios`/`tty` import is guarded; without a TTY the hook degrades to non-interactive (no timed input, no voice). Real Windows support (CTTY/`CONIN$`) is tracked as future work. |

The credential gate, coding-standards audit, question generation, evaluation, and session persistence are pure Python and platform-independent — the gaps are all in the interactive/voice layer.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
