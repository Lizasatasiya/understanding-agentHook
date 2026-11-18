# Understanding Agent

A git pre-commit hook that asks the author to explain the staged change before the commit is allowed. The hook runs on the developer machine. The model, the questions, and the scoring run on a separate defense service.

The hook never holds the model API key. A commit is allowed only after the service says the answers passed, or after it says the change is too trivial to ask about.

## Read next

- [Workflow](docs/WORKFLOW.md) — what happens, in order, from `git commit` to allow or block
- [Setup](docs/SETUP.md) — run the service and a hook against it on one machine

## Layout

| Path | Role |
|---|---|
| `understanding_agent/core/cli.py` | Hook entry. Local checks, then the interview over HTTP |
| `understanding_agent/core/defense_client.py` | Client for the defense service |
| `service/` | FastAPI service: summary, questions, scoring, persona rows |
| `install.sh` | Machine-wide hook install. Points every repo at one server |
| `.pre-commit-hooks.yaml` | Same hook, for repos that use the pre-commit framework |

`start_ui.sh` starts a local dashboard for browsing commits on this machine. It is not the defense service. Do not run it on the same port.
