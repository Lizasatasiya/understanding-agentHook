"""Central LLM provider manager.

ONE place to configure every LLM the hook talks to. Any provider exposing an
OpenAI-compatible /chat/completions endpoint works by setting, at most:

    UNDERSTANDING_AGENT_PROVIDER   preset name (nous, groq, openai, openrouter,
                                   together, ollama, custom) — default: nous
    UNDERSTANDING_AGENT_MODEL       model id (any preset, or custom)
    UNDERSTANDING_AGENT_BASE_URL    endpoint override (makes ANY
                                   OpenAI-compatible provider work)
    UNDERSTANDING_AGENT_API_KEY    key (else the preset's default env var)

Changing provider or model is ONE env var, not five. Legacy variables
(NOUS_API_KEY / NOUS_MODEL / NOUS_BASE_URL / GROQ_API_KEY / GROQ_MODEL /
GROQ_BASE_URL / UNDERSTANDING_AGENT_PROVIDER=groq) keep working through the
compat resolution below, so existing user setups and tests do not break.

Nothing here does retries or JSON parsing — api_utils owns transport and
response-shape handling; this module only resolves WHAT to call.
"""

import os

# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------

PROVIDER_PRESETS = {
    "nous": {
        "base_url": "https://inference-api.nousresearch.com/v1",
        # Valid model in the Nous Research catalog; override with the env vars.
        "model": "qwen/qwen3-coder-30b-a3b-instruct",
        "api_key_env": "NOUS_API_KEY",
        "alt_api_key_envs": ("NOUSRESEARCH_API_KEY", "NOUS_PORTAL_API_KEY"),
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        # Verified production model id from Groq's model catalog.
        "model": "llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
        "alt_api_key_envs": (),
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "alt_api_key_envs": (),
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "api_key_env": "OPENROUTER_API_KEY",
        "alt_api_key_envs": (),
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "api_key_env": "TOGETHER_API_KEY",
        "alt_api_key_envs": (),
    },
    "ollama": {
        # Local server; no key required.
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5-coder:7b",
        "api_key_env": "OLLAMA_API_KEY",
        "alt_api_key_envs": (),
    },
    "custom": {
        # Fully driven by UNDERSTANDING_AGENT_BASE_URL / _MODEL / _API_KEY.
        "base_url": "",
        "model": "",
        "api_key_env": "UNDERSTANDING_AGENT_API_KEY",
        "alt_api_key_envs": (),
    },
}


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def get_provider() -> str:
    """Active provider name (a PROVIDER_PRESETS key)."""
    p = _env("UNDERSTANDING_AGENT_PROVIDER").lower()
    return p if p in PROVIDER_PRESETS else "nous"


def _detect_groq_key_shape(key: str) -> bool:
    return key.startswith("gsk_")


def resolve_base_url() -> str:
    """Resolve the chat-completions base URL for the active provider."""
    # 1. Explicit overrides win, in precedence order.
    for var in ("UNDERSTANDING_AGENT_BASE_URL", "NOUS_BASE_URL",
                "GROQ_BASE_URL"):
        v = _env(var)
        if v:
            return v

    provider = get_provider()
    if provider == "nous":
        # Legacy sniff: a gsk_ key with no explicit provider means Groq.
        if _detect_groq_key_shape(_resolve_api_key("nous")):
            return PROVIDER_PRESETS["groq"]["base_url"]
    return PROVIDER_PRESETS[provider]["base_url"]


def resolve_model() -> str:
    """Resolve the model id for the active provider."""
    for var in ("UNDERSTANDING_AGENT_MODEL", "NOUS_MODEL", "GROQ_MODEL"):
        v = _env(var)
        if v:
            return v

    provider = get_provider()
    if provider == "nous" and _detect_groq_key_shape(_resolve_api_key("nous")):
        return PROVIDER_PRESETS["groq"]["model"]
    return PROVIDER_PRESETS[provider]["model"]


def _resolve_api_key(provider: str) -> str:
    """Resolve the API key for a provider, honoring the central var first."""
    # Central var first.
    v = _env("UNDERSTANDING_AGENT_API_KEY")
    if v:
        return v

    preset = PROVIDER_PRESETS[provider]
    for var in (preset["api_key_env"],) + preset["alt_api_key_envs"]:
        v = _env(var)
        if v:
            return v
    return ""


def resolve_api_key() -> str:
    """Resolve the API key for the active provider (may be empty for local)."""
    provider = get_provider()

    key = _resolve_api_key(provider)
    if key:
        return key

    # Legacy cross-provider sniff: a gsk_ key found under a NOUS_* env var
    # means the user configured Groq before the provider var existed.
    if provider == "nous":
        for var in ("NOUS_API_KEY", "NOUSRESEARCH_API_KEY", "NOUS_PORTAL_API_KEY"):
            v = _env(var)
            if _detect_groq_key_shape(v):
                return v

    # ollama and other local presets need no key.
    if provider in ("ollama",):
        return ""

    # Env-file fallbacks are handled by api_utils.load_nous_api_key, which
    # delegates here for the env-var half of the search.
    return ""


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
