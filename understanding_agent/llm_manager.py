"""LLM configuration for the model and key saved by install.sh.

Both values live in ~/.config/understanding-agent/.env:
UNDERSTANDING_AGENT_MODEL is sonnet, haiku, or nous.
UNDERSTANDING_AGENT_API_KEY is the key for that model.
Questions and answer checks use that same pair. Nothing else is consulted.
"""

import os

# ---------------------------------------------------------------------------
# Supported models
# ---------------------------------------------------------------------------

# Questions and answer checks both use whichever of these the installer selected.
SUPPORTED_MODELS = {
    "nous": {
        "provider": "nous",
        "model": "qwen/qwen3-coder-30b-a3b-instruct",
        "base_url": "https://inference-api.nousresearch.com/v1",
    },
    "sonnet": {
        "provider": "anthropic",
        "model": "claude-sonnet-5-5",
        "base_url": "https://api.anthropic.com/v1",
    },
    "haiku": {
        "provider": "anthropic",
        "model": "claude-haiku-5-5",
        "base_url": "https://api.anthropic.com/v1",
    },
}


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


_GLOBAL_CONFIG = os.path.expanduser("~/.config/understanding-agent/.env")
# Values people paste from the install example. They are not real keys.
_PLACEHOLDER_KEYS = {
    "api_key",
    "your_api_key",
    "your_key",
    "sk-dummy-replace-me",
    "changeme",
    "replace-me",
}


def _usable_secret(value: str) -> str:
    value = (value or "").strip()
    if not value or value.lower() in _PLACEHOLDER_KEYS:
        return ""
    return value


def _parse_env_file(path: str) -> dict:
    found = {}
    if not path or not os.path.isfile(path):
        return found
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1].strip()
                if key and val:
                    found[key] = val
    except Exception:
        return {}
    return found


def _installed() -> dict:
    """Values written by install.sh. This file is the only LLM configuration."""
    return _parse_env_file(_GLOBAL_CONFIG)


def installed_model_choice() -> str | None:
    """Return sonnet, haiku, or nous from the install file. No other source."""
    raw = (_installed().get("UNDERSTANDING_AGENT_MODEL") or "").strip().lower()
    if raw in SUPPORTED_MODELS:
        return raw
    return None


def _active_spec() -> dict:
    choice = installed_model_choice()
    if not choice:
        return {}
    return SUPPORTED_MODELS[choice]


def get_provider() -> str:
    """nous or anthropic, from the installed model. Empty when none is installed."""
    return _active_spec().get("provider", "")


def resolve_base_url() -> str:
    """Endpoint for the installed model. Empty when none is installed."""
    return _active_spec().get("base_url", "")


def resolve_model() -> str:
    """Model id for questions and answer checks. Empty when none is installed."""
    return _active_spec().get("model", "")


def resolve_judge_model() -> str:
    """Answer checks use the same installed model as question generation."""
    return resolve_model()


def resolve_api_key_info() -> tuple[str, str]:
    """Return (api_key, where it was read from).

    Only UNDERSTANDING_AGENT_API_KEY in the install file is used.
    Placeholder text such as YOUR_API_KEY is ignored.
    """
    value = _usable_secret(_installed().get("UNDERSTANDING_AGENT_API_KEY", ""))
    if value:
        return value, _GLOBAL_CONFIG
    return "", "none"


def resolve_api_key() -> str:
    """API key for the selected model. Empty when nothing is configured."""
    return resolve_api_key_info()[0]


def describe_api_key(api_key: str | None = None) -> str:
    """Safe description for logs. Never includes the full key."""
    key, source = resolve_api_key_info()
    used = api_key if api_key is not None else key
    prefix = (used or "")[:7]
    return f"key from {source} (length {len(used or '')}, starts with {prefix!r})"


class LLMManager:
    """Single entry point for LLM endpoint configuration.

    api_utils._nous_request_once consumes this for the actual HTTP call, so
    every caller (summary, questions, evaluation, follow-up) automatically
    follows whatever provider is configured here.
    """

    def __init__(self):
        self._base_url = None
        self._model = None
        self._api_key = None

    @property
    def provider(self) -> str:
        return get_provider()

    @property
    def base_url(self) -> str:
        if self._base_url is None:
            self._base_url = resolve_base_url()
        return self._base_url

    @property
    def model(self) -> str:
        if self._model is None:
            self._model = resolve_model()
        return self._model

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            self._api_key = resolve_api_key()
        return self._api_key

    def reset(self):
        """Drop cached resolutions (used by tests that patch env vars)."""
        self._base_url = None
        self._model = None
        self._api_key = None


# ---------------------------------------------------------------------------
# Thinking / reasoning control
# ---------------------------------------------------------------------------

def thinking_disabled() -> bool:
    """Suppress model thinking/reasoning? Default: ON.

    Bench evidence (scripts/bench_models.py, Nous gateway):
    - deepseek/deepseek-v4-flash plain: 334 reasoning tokens, ~51s/call
    - with reasoning_effort=none: 0 reasoning tokens, ~10s/call
    Set UNDERSTANDING_AGENT_DISABLE_THINKING=off to re-enable thinking.
    """
    return _env("UNDERSTANDING_AGENT_DISABLE_THINKING").lower() not in ("off", "0", "false", "no")


def thinking_control_params(provider: str | None = None) -> dict:
    """Extra request params that suppress model thinking, or {} if none.

    Only sent for providers known to accept them (the Nous gateway accepts
    reasoning_effort). Strict OpenAI endpoints reject unknown params with a
    400, so other providers get {} — thinking control there is the provider
    preset's own business.
    """
    if not thinking_disabled():
        return {}
    p = provider or get_provider()
    if p == "nous":
        return {"reasoning_effort": "none"}
    return {}
