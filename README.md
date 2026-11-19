# Understanding Agent

A git pre-commit hook that asks the author to explain the staged change before the commit is allowed. Each diff is assessed on its own. The service keeps each developer's questions, answers, and persona. The next commit does not read them.

A commit is allowed only after every answer for that diff meets the passing score, or the change is too trivial to ask about.

## Read next

- [Workflow](docs/WORKFLOW.md) — what happens, in order, from `git commit` to allow or block
- [Setup](docs/SETUP.md) — install, then test a pass, a fail, a retry, and the saved persona

## Layout

| Path | Role |
|---|---|
| `understanding_agent/core/cli.py` | Hook. Local checks, then a fresh summary and questions for this diff |
| `service/` | Stores each developer's persona, questions, and answers |
| `install.sh` | Machine-wide hook install |
| `.pre-commit-hooks.yaml` | Same hook, for repos that use the pre-commit framework |

`start_ui.sh` starts a local dashboard for browsing commits on this machine. It does not store interviews. Do not run it on the same port as another local server.
