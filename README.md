# Understanding Agent

A git pre-commit hook that asks the author to explain the staged change before the commit is allowed. Each diff is assessed on its own. The service keeps each developer's questions, answers, and persona. The next commit does not read them.

A commit is allowed only after every answer for that diff meets the passing score, or the change is too trivial to ask about.

## Read next

- [Workflow](docs/WORKFLOW.md) — what happens, in order, from `git commit` to allow or block
- [Setup](docs/SETUP.md) — local end-to-end test, then the same test from another machine

## Layout

| Path | Role |
|---|---|
| `understanding_agent/core/cli.py` | Hook. Local checks, then the defense server for this diff |
| `service/` | Model calls, scores, and each developer's persona |
| `install.sh` | Machine-wide hook install |
| `.pre-commit-hooks.yaml` | Same hook, for repos that use the pre-commit framework |

`start_ui.sh` starts a local dashboard for browsing commits on this machine. It does not store interviews. Do not run it on the same port as another local server.
