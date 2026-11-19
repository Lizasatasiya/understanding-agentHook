"""Team standards prose used by master analysis.

The hook's coding-standards report is deterministic and does not read this
document. Master analysis judges the diff against it.
"""

import os

DEFAULT_STANDARDS_DOC = """# Coding Standards (default)

Judge ONLY what this commit ADDED — never legacy code the author did not
touch. Findings must describe what the change does and the risk it carries.

## Correctness & error handling
- Error paths are explicit: changed code that can fail must handle the
  failure, not let it silently corrupt state or crash a caller.
- Exceptions catch the narrowest type needed; bare catch-alls that hide
  real bugs are violations.
- External resources (connections, files, timers, subscriptions) are
  released on every path, including the error path.
- Failures propagate usefully to callers: swallowed errors that leave
  callers assuming success are violations.

## Language & framework idioms
- (JS/TS) Prefer const/let over var; strict equality (===) over coercing
  comparisons; no unsafe type coercion on external input.
- (React) State and props are treated as immutable; no direct mutation;
  hooks respect their dependency contracts; async effects clean up.
- (Python) No mutable default arguments; resources use context managers.
- (Go) Returned errors are checked, not discarded.

## Complexity
- Changed functions stay small and shallow: deep nesting is a smell
  (prefer early returns/guard clauses); very long functions get flagged
  only when the change ADDED the length.

## Input & security hygiene (advisory, beyond the credential gate)
- Functions accepting external input validate it before use.
- Queries and commands built from external input use parameterization,
  never string concatenation with untrusted data.
- New config/env handling does not log or expose secrets.
"""


def load_standards_doc(project_root: str | None = None) -> str:
    """Load the repo's standards document, falling back to the default.

    Resolution order: <project_root>/.understanding/standards.md, then
    cwd/.understanding/standards.md, then the shipped default.
    """
    candidates = []
    if project_root:
        candidates.append(os.path.join(project_root, ".understanding", "standards.md"))
    candidates.append(os.path.join(os.getcwd(), ".understanding", "standards.md"))
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as handle:
                document = handle.read().strip()
            if document:
                return document
        except OSError:
            continue
    return DEFAULT_STANDARDS_DOC
