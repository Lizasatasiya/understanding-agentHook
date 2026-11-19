# Workflow

One process. `git commit` runs the hook on the developer machine. The hook reads that staged diff, asks the model about it, and scores the answers. Nothing from a previous commit is reused. When the interview ends, the service stores that developer's questions, answers, and updated persona.

The hook entry is `understanding-agent` (`understanding_agent/core/cli.py`). It runs at `pre-commit`. The same command also runs at `prepare-commit-msg`, but that second run only checks a pass marker for this diff. It does not ask again.

## On `git commit`

1. **Read the staged change.** Detect the repo, the files, the stack, and a change classification. Build a bounded context from the diff. Empty staging area: exit 0.

2. **Block stolen credentials.** A deterministic scan rejects live keys, private key files, and credential-shaped values before any question. `UNDERSTANDING_AGENT_SECURITY_GATE=off` disables this scan.

3. **Show coding-standards findings.** If any failed, the author chooses fix (commit stops) or proceed (questions still run). Security profile, optional semgrep/gitleaks output, and practice-pack hints are used only to shape questions for this diff.

4. **Refuse a bypass.** `--no-verify`, a non-interactive agent commit, or a GUI commit with no terminal stops the commit. On macOS a GUI commit can open Terminal and run the interview there.

5. **Skip a trivial diff.** Formatting, imports, comments, docs, or type annotations can pass without questions when standards did not fail. The hook writes the pass marker and exits.

6. **Ask the model about this diff only.** Write a new change summary, then generate new questions. A previous attempt, even of the same diff, is not read. Optional master analysis runs only when `UNDERSTANDING_AGENT_MASTER_ANALYSIS` is `on`.

7. **Ask the author, one question at a time.** The hook prints the question and reads a typed or spoken answer, then scores it. Below `UNDERSTANDING_AGENT_PASSING_SCORE` (default 75) the commit stops. Remaining questions are not asked. The next `git commit` starts again at a new first question.

8. **Allow the commit.** When every answer passes, the hook writes a pass marker so `prepare-commit-msg` can see that this diff was defended. The git index stays locked while the questions are on screen. The marker is dropped after that commit stage.

9. **Update the developer persona.** On a pass or a stop, the hook sends the questions and answers from this interview to `POST /api/v1/persona`. The service appends those rows to that git email and rewrites the persona summary from the running scores. The next commit does not read them. If the service cannot be reached, the allow or block decision still stands.

If the model returns no questions, the commit stops unless `UNDERSTANDING_AGENT_FAIL_MODE` is open.

## What is stored

One `developer_personas` row per git email: attempt counts, mean score, pass rate, scores by category, missing concepts, and a short persona summary.

One `developer_exchanges` row per question asked: the question, the answer, the score, and the evaluation note.

There is no session table. A later commit of the same diff starts from a new summary and a new first question.

The model key is read on the machine that runs the hook: process environment, then `~/.config/understanding-agent/.env`, then a `.env` in the working directory. The persona write uses `UNDERSTANDING_AGENT_SERVER_URL`.
