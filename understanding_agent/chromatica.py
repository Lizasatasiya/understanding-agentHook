"""Chromatica — central LLM provider manager.

The planet where every provider voice lives: one module resolves provider,
model, base URL, and API key for ANY OpenAI-compatible endpoint. Switching
providers is one environment variable.
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

    # Env-file fallbacks are handled by just_dance.load_api_key, which
    # delegates here for the env-var half of the search.
    return ""


# ---------------------------------------------------------------------------
# Key resolution ladder (the single home)
# ---------------------------------------------------------------------------

def _parse_key_from_file(filepath: str, preferred_keys: tuple) -> str:
    """Extract specified keys from an env file (KEY=VALUE or export KEY=VALUE)."""
    if not filepath or not os.path.exists(filepath):
        return ""
    try:
        found_keys = {}
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                for key_name in preferred_keys:
                    if line.startswith(key_name):
                        parts = line.split("=", 1)
                        if len(parts) == 2 and parts[0].strip() == key_name:
                            val = parts[1].strip()
                            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                                val = val[1:-1].strip()
                            if val:
                                found_keys[key_name] = val
        for pref in preferred_keys:
            if pref in found_keys:
                return found_keys[pref]
    except Exception:
        pass
    return ""


def _env_search_dirs():
    """Directories searched for .env / .env.local: cwd, its parents, git root."""
    import subprocess
    search_dirs = []
    cur = os.path.abspath(os.getcwd())
    for _ in range(4):
        search_dirs.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    try:
        repo_root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True, stderr=subprocess.DEVNULL
        ).strip()
        if repo_root and repo_root not in search_dirs:
            search_dirs.append(repo_root)
    except Exception:
        pass
    return search_dirs


def load_api_key() -> str:
    """Resolve the active provider's API key — ONE documented ladder.

    1. Process environment: UNDERSTANDING_AGENT_API_KEY, then the active
       provider preset's env vars, then legacy GROQ_API_KEY
       (all env vars outrank all disk files).
    2. .env / .env.local in cwd, parents, and git root — the provider's
       key names first, then legacy NOUS_* then GROQ names.
    3. ~/.config/understanding-agent/.env — same name order.
    """
    # 1. Environment variables
    env_key = resolve_api_key()
    if env_key:
        return env_key

    groq_env = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_env:
        return groq_env

    provider = get_provider()
    preset = PROVIDER_PRESETS[provider]
    file_key_names = (preset["api_key_env"],) + preset["alt_api_key_envs"] + ("UNDERSTANDING_AGENT_API_KEY",)
    legacy_nous = ("NOUS_API_KEY", "NOUSRESEARCH_API_KEY", "NOUS_PORTAL_API_KEY")

    search_dirs = _env_search_dirs()

    def _search_files(key_names):
        for d in search_dirs:
            for fname in (".env", ".env.local"):
                key = _parse_key_from_file(os.path.join(d, fname), key_names)
                if key:
                    return key
        return ""

    # 2. .env files: provider names first, then legacy names
    key = _search_files(file_key_names)
    if key:
        return key
    key = _search_files(legacy_nous)
    if key:
        return key
    key = _search_files(("GROQ_API_KEY",))
    if key:
        return key

    # 3. Central user config
    central = os.path.expanduser("~/.config/understanding-agent/.env")
    for names in (file_key_names, legacy_nous, ("GROQ_API_KEY",)):
        key = _parse_key_from_file(central, names)
        if key:
            return key

    return ""


class LLMManager:
    """Single entry point for LLM endpoint configuration.

    just_dance._request_once consumes this for the actual HTTP call, so
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
