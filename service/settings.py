"""Server interview settings.

The hook does not read these. Change them in the service process environment
and restart the service. The default step order is summary, then the trivial
bypass, then question generation.
"""

import os


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


class ServiceSettings:
    def __init__(
        self,
        passing_score: int,
        bypass_trivial: bool,
        interview_ttl_seconds: int,
        start_steps: tuple[str, ...],
    ):
        self.passing_score = passing_score
        self.bypass_trivial = bypass_trivial
        self.interview_ttl_seconds = interview_ttl_seconds
        self.start_steps = start_steps

    @classmethod
    def from_env(cls) -> "ServiceSettings":
        raw_steps = os.environ.get(
            "SERVICE_DEFENSE_STEPS", "summary,trivial_gate,questions"
        )
        steps = tuple(part.strip() for part in raw_steps.split(",") if part.strip())
        return cls(
            passing_score=_int("UNDERSTANDING_AGENT_PASSING_SCORE", 75),
            bypass_trivial=_flag("SERVICE_BYPASS_TRIVIAL", True),
            interview_ttl_seconds=_int("SERVICE_INTERVIEW_TTL_SECONDS", 30 * 60),
            start_steps=steps or ("summary", "trivial_gate", "questions"),
        )
