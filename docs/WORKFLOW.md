# Workflow

One process on the developer machine, and one service. `git commit` runs the hook. The hook reads the staged diff and runs the checks that do not need a model. The service writes the summary, the questions, and the scores. Nothing from a previous commit is reused. When the interview ends, the service stores that developer's questions, answers, and updated persona.

The hook entry is `understanding-agent` (`understanding_agent/core/cli.py`). It runs at `pre-commit`. The same command also runs at `prepare-commit-msg`, but that second run only checks a pass marker for this diff. It does not ask again.

## On `git commit`

1. **Read the staged change.** Detect the repo, the files, the stack, and a change classification. Build a bounded context from the diff. Empty staging area: exit 0.

2. **Block stolen credentials.** A deterministic scan rejects live keys, private key files, and credential-shaped values before any question. `UNDERSTANDING_AGENT_SECURITY_GATE=off` disables this scan.

3. **Show coding-standards findings.** If any failed, the author chooses fix (commit stops) or proceed (questions still run). Security profile, optional semgrep/gitleaks output, and practice-pack hints are used only to shape questions for this diff.

4. **Refuse a bypass.** `--no-verify`, a non-interactive agent commit, or a GUI commit with no terminal stops the commit. On macOS a GUI commit can open Terminal and run the interview there.

5. **Skip a trivial diff.** Formatting, imports, comments, docs, or type annotations can pass without questions when standards did not fail. The hook writes the pass marker and exits.

6. **Ask the server about this diff.** The hook sends the staged context to `POST /api/v1/defense/start`. The server writes the change summary, then generates the questions. Optional master analysis runs on the server only when `UNDERSTANDING_AGENT_MASTER_ANALYSIS` is `on`. The hook does not call the model.

7. **Ask the author, one question at a time.** The hook prints each question and reads a typed or spoken answer, then `POST`s that answer to `/api/v1/defense/{defense_id}/answer`. The server scores it. Below `UNDERSTANDING_AGENT_PASSING_SCORE` (default 75) the server returns `stop` and the commit ends. Remaining questions are not asked. The next `git commit` starts again at a new first question.

8. **Allow the commit.** When the server returns `pass`, the hook writes a pass marker so `prepare-commit-msg` can see that this diff was defended. The git index stays locked while the questions are on screen. The marker is dropped after that commit stage.

9. **Update the developer persona.** The server does this when it returns `pass` or `stop`. It appends the questions and answers from this interview and rewrites the persona summary. The next commit does not read them. If the server cannot be reached, the commit stops.

If the model returns no questions, the commit stops unless `UNDERSTANDING_AGENT_FAIL_MODE` is open.

## What is stored

One `developer_personas` row per git email: attempt counts, mean score, pass rate, scores by category, missing concepts, and a short persona summary.

One `developer_exchanges` row per question asked: the question, the answer, the score, and the evaluation note.

There is no session table. A later commit of the same diff starts from a new summary and a new first question.

The model key is read by the service, from the process environment, then `~/.config/understanding-agent/.env`, then a `.env` in the directory where the service was started. The hook only needs `UNDERSTANDING_AGENT_SERVER_URL`.
