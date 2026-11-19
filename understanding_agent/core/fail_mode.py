"""Commit policy when the server has no questions to ask.

This is an environment flag. It does not call a model.
"""

import os


def fail_open_enabled() -> bool:
    """Allow the commit when the server returns no questions.

    UNDERSTANDING_AGENT_FAIL_MODE=closed aborts the commit instead.
    """
    return os.environ.get("UNDERSTANDING_AGENT_FAIL_MODE", "").strip().lower() != "closed"
