"""Temporary flow tracing for the defense service.

Every server log is a call to trace(). To remove the logs, delete this file
and delete those calls. Strings are shortened. API keys are never logged.
"""

import json
import logging

log = logging.getLogger("service.trace")

_OMIT = {"api_key", "authorization", "x_team_token", "x_signature", "diff"}
_LIMIT = 800


def enable() -> None:
    """Attach one stderr handler. Safe to call on every app startup."""
    if log.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    log.propagate = False


def trace(phase: str, **fields) -> None:
    if not log.isEnabledFor(logging.INFO):
        return
    safe = {key: _brief(value) for key, value in fields.items() if key not in _OMIT}
    log.info("%s %s", phase, json.dumps(safe, default=str, sort_keys=True))


def _brief(value):
    if isinstance(value, str):
        text = " ".join(value.split())
        if len(text) > _LIMIT:
            return text[:_LIMIT] + "…"
        return text
    if isinstance(value, dict):
        kept = []
        for key, item in value.items():
            if key in _OMIT:
                continue
            kept.append((key, _brief(item)))
            if len(kept) >= 12:
                break
        return dict(kept)
    if isinstance(value, (list, tuple)):
        return [_brief(item) for item in list(value)[:12]]
    return value
