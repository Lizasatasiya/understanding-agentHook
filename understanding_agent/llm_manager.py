"""Central LLM provider manager.

ONE place to configure every LLM the hook talks to. Fully model-agnostic and
provider-agnostic: any provider exposing an OpenAI-compatible /chat/completions
endpoint (or Anthropic /messages) works out of the box simply by setting a model
name and an API key.

Configuration (generic or provider-specific):

    Generic / Unified:
        MODEL / LLM_MODEL / UNDERSTANDING_AGENT_MODEL       model id
        API_KEY / LLM_API_KEY / UNDERSTANDING_AGENT_API_KEY key
        BASE_URL / LLM_BASE_URL / UNDERSTANDING_AGENT_BASE_URL endpoint override
        PROVIDER / LLM_PROVIDER / UNDERSTANDING_AGENT_PROVIDER preset name

    Provider-specific (auto-detected when present):
        OPENAI_API_KEY / OPENAI_MODEL
        GEMINI_API_KEY (or GOOGLE_API_KEY) / GEMINI_MODEL
        ANTHROPIC_API_KEY / ANTHROPIC_MODEL
        DEEPSEEK_API_KEY / DEEPSEEK_MODEL
        GROQ_API_KEY / GROQ_MODEL
        MISTRAL_API_KEY / MISTRAL_MODEL
        OPENROUTER_API_KEY / OPENROUTER_MODEL
        TOGETHER_API_KEY / TOGETHER_MODEL
        PERPLEXITY_API_KEY / PERPLEXITY_MODEL
        XAI_API_KEY / XAI_MODEL
        COHERE_API_KEY / COHERE_MODEL
        OLLAMA_BASE_URL / OLLAMA_MODEL

Changing provider or model is ONE env var, not five. Legacy variables
(NOUS_API_KEY / NOUS_MODEL / NOUS_BASE_URL / GROQ_API_KEY / GROQ_MODEL /
GROQ_BASE_URL) keep working through the compat resolution below.
"""

import os
import subprocess

# Org OpenAI-compatible gateway. Every call uses this unless a process
# environment variable explicitly selects another provider.
# Replace UNDERSTANDING_AGENT_API_KEY in .env (placeholder: sk-dummy-replace-me).
# Replace ORG_LLM_MODEL with the id from GET /v1/models if "default" is rejected.
ORG_LLM_BASE_URL = "http://llm.yorkdevs.link:2222/v1"
ORG_LLM_MODEL = "default"
DEFAULT_PROVIDER = "yorkdevs"

# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------

PROVIDER_PRESETS = {
    "nous": {
        "base_url": "https://inference-api.nousresearch.com/v1",
        "model": "qwen/qwen3-coder-30b-a3b-instruct",
        "api_key_env": "NOUS_API_KEY",
        "alt_api_key_envs": ("NOUSRESEARCH_API_KEY", "NOUS_PORTAL_API_KEY"),
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
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
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
        "api_key_env": "GEMINI_API_KEY",
        "alt_api_key_envs": ("GOOGLE_API_KEY", "GOOGLE_AI_API_KEY"),
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-5-sonnet-20241022",
        "api_key_env": "ANTHROPIC_API_KEY",
        "alt_api_key_envs": ("CLAUDE_API_KEY",),
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "alt_api_key_envs": (),
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-small-latest",
        "api_key_env": "MISTRAL_API_KEY",
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
    "perplexity": {
        "base_url": "https://api.perplexity.ai",
        "model": "sonar",
        "api_key_env": "PERPLEXITY_API_KEY",
        "alt_api_key_envs": (),
    },
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "model": "grok-2-latest",
        "api_key_env": "XAI_API_KEY",
        "alt_api_key_envs": ("GROK_API_KEY",),
    },
    "cohere": {
        "base_url": "https://api.cohere.com/v2",
        "model": "command-r-plus",
        "api_key_env": "COHERE_API_KEY",
        "alt_api_key_envs": (),
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "model": "llama3.1-70b",
        "api_key_env": "CEREBRAS_API_KEY",
        "alt_api_key_envs": (),
    },
    "sambanova": {
        "base_url": "https://api.sambanova.ai/v1",
        "model": "Meta-Llama-3.1-70B-Instruct",
        "api_key_env": "SAMBANOVA_API_KEY",
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
        # Fully driven by BASE_URL / MODEL / API_KEY.
        "base_url": "",
        "model": "",
        "api_key_env": "UNDERSTANDING_AGENT_API_KEY",
        "alt_api_key_envs": ("LLM_API_KEY", "API_KEY"),
    },
    "yorkdevs": {
        "base_url": ORG_LLM_BASE_URL,
        "model": ORG_LLM_MODEL,
        "api_key_env": "YORKDEVS_API_KEY",
        "alt_api_key_envs": (),
    },
}

PROVIDER_ALIASES = {
    "google": "gemini",
    "claude": "anthropic",
    "grok": "xai",
}


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _dotenv_values() -> dict:
    """UNDERSTANDING_AGENT_* settings from .env files.

    Process environment always wins. Tests set UNDERSTANDING_AGENT_IGNORE_DOTENV=1.
    API keys are intentionally not loaded here; api_utils.load_nous_api_key owns that.
    """
    if _env("UNDERSTANDING_AGENT_IGNORE_DOTENV") == "1":
        return {}
    wanted = (
        "UNDERSTANDING_AGENT_PROVIDER", "LLM_PROVIDER", "PROVIDER",
        "UNDERSTANDING_AGENT_MODEL", "LLM_MODEL", "MODEL",
        "UNDERSTANDING_AGENT_BASE_URL", "LLM_BASE_URL", "BASE_URL",
        "UNDERSTANDING_AGENT_JUDGE_MODEL",
    )
    found = {}
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
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        if repo_root and repo_root not in search_dirs:
            search_dirs.append(repo_root)
    except Exception:
        pass
    search_dirs.append(os.path.expanduser("~/.config/understanding-agent"))

    for directory in search_dirs:
        for fname in (".env", ".env.local"):
            path = os.path.join(directory, fname)
            if not os.path.isfile(path):
                continue
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
                        if key not in wanted or key in found:
                            continue
                        val = val.strip()
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val = val[1:-1].strip()
                        if val:
                            found[key] = val
            except Exception:
                continue
    return found


def _configured(name: str) -> str:
    """Process env, then .env / .env.local."""
    return _env(name) or _dotenv_values().get(name, "")


def detect_provider_from_model(model: str) -> str | None:
    """Infer provider name from the model identifier."""
    if not model:
        return None
    m = model.lower().strip()
    if m.startswith("gemini") or "gemini" in m:
        return "gemini"
    if m.startswith("claude") or "claude" in m:
        return "anthropic"
    if m.startswith(("gpt-", "o1", "o3", "chatgpt", "text-davinci")):
        return "openai"
    if m.startswith("deepseek") or "deepseek" in m:
        return "deepseek"
    if any(k in m for k in ("mistral", "codestral", "mixtral", "ministral", "pixtral")):
        return "mistral"
    if m.startswith("sonar") or "sonar" in m:
        return "perplexity"
    if m.startswith("grok") or "grok" in m:
        return "xai"
    if m.startswith("command") or "command" in m:
        return "cohere"
    if m.startswith("qwen") or "qwen" in m:
        return "nous"
    if m.startswith("llama") or "llama" in m:
        return "groq"
    return None


def detect_provider_from_key(key: str) -> str | None:
    """Infer provider name from the API key format/prefix."""
    if not key:
        return None
    k = key.strip()
    if k.startswith("gsk_"):
        return "groq"
    if k.startswith("sk-ant-"):
        return "anthropic"
    if k.startswith("AIzaSy"):
        return "gemini"
    if k.startswith("sk-nous-"):
        return "nous"
    if k.startswith("sk-or-"):
        return "openrouter"
    if k.startswith("xai-"):
        return "xai"
    return None


def detect_provider_from_env() -> str | None:
    """Auto-detect active provider from environment variables."""
    # 1. Model name in generic env vars
    for var in ("UNDERSTANDING_AGENT_MODEL", "LLM_MODEL", "MODEL"):
        p = detect_provider_from_model(_env(var))
        if p:
            return p

    # 2. Key shape in generic env vars
    for var in ("UNDERSTANDING_AGENT_API_KEY", "LLM_API_KEY", "API_KEY"):
        p = detect_provider_from_key(_env(var))
        if p:
            return p

    # 3. Provider-specific key env vars (skip nous and custom to allow specific detection)
    for prov_name, preset in PROVIDER_PRESETS.items():
        if prov_name in ("nous", "custom", "ollama"):
            continue
        for var in (preset["api_key_env"],) + preset["alt_api_key_envs"]:
            if _env(var):
                return prov_name

    # 4. Check Nous vars (with Groq key sniffing)
    for var in ("NOUS_API_KEY", "NOUSRESEARCH_API_KEY", "NOUS_PORTAL_API_KEY"):
        val = _env(var)
        if val:
            if _detect_groq_key_shape(val):
                return "groq"
            return "nous"

    # 5. Provider-specific model env vars
    for prov_name in PROVIDER_PRESETS:
        if prov_name in ("custom",):
            continue
        if _env(f"{prov_name.upper()}_MODEL"):
            return prov_name

    # 6. Provider-specific base url env vars
    for prov_name in PROVIDER_PRESETS:
        if prov_name in ("custom",):
            continue
        if _env(f"{prov_name.upper()}_BASE_URL"):
            return prov_name

    return None


def get_provider() -> str:
    """Active provider name (a PROVIDER_PRESETS key)."""
    # Process env only here, so a shell override still beats .env.
    p = (_env("UNDERSTANDING_AGENT_PROVIDER") or _env("LLM_PROVIDER") or _env("PROVIDER")).lower()
    if not p:
        # Repo .env selects the org gateway ahead of auto-detection from
        # leftover provider keys (for example a Nous key still in .env).
        p = (_dotenv_values().get("UNDERSTANDING_AGENT_PROVIDER")
             or _dotenv_values().get("LLM_PROVIDER")
             or _dotenv_values().get("PROVIDER")
             or "").lower()
    if p:
        p = PROVIDER_ALIASES.get(p, p)
        if p in PROVIDER_PRESETS:
            return p
        return DEFAULT_PROVIDER

    detected = detect_provider_from_env()
    if detected:
        return detected

    return DEFAULT_PROVIDER


def _detect_groq_key_shape(key: str) -> bool:
    return key.startswith("gsk_")


def resolve_base_url() -> str:
    """Resolve the chat-completions base URL for the active provider."""
    # 1. Explicit overrides win, in precedence order.
    for var in ("UNDERSTANDING_AGENT_BASE_URL", "LLM_BASE_URL", "BASE_URL"):
        v = _configured(var)
        if v:
            return v

    provider = get_provider()

    # 2. Provider-specific base url env vars
    provider_base_urls = (
        f"{provider.upper()}_BASE_URL",
        "NOUS_BASE_URL",
        "GROQ_BASE_URL",
        "OPENAI_BASE_URL",
        "GEMINI_BASE_URL",
        "GOOGLE_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_BASE_URL",
        "DEEPSEEK_BASE_URL",
        "OLLAMA_BASE_URL",
    )
    for var in provider_base_urls:
        v = _env(var)
        if v:
            return v

    if provider == "nous":
        # Legacy sniff: a gsk_ key with no explicit provider means Groq.
        if _detect_groq_key_shape(_resolve_api_key("nous")):
            return PROVIDER_PRESETS["groq"]["base_url"]

    if provider in PROVIDER_PRESETS:
        return PROVIDER_PRESETS[provider]["base_url"]
    return ""


def resolve_model() -> str:
    """Resolve the model id for the active provider."""
    # 1. Generic model overrides
    for var in ("UNDERSTANDING_AGENT_MODEL", "LLM_MODEL", "MODEL"):
        v = _configured(var)
        if v:
            return v

    provider = get_provider()

    # 2. Provider-specific model env vars
    provider_models = (
        f"{provider.upper()}_MODEL",
        "NOUS_MODEL",
        "GROQ_MODEL",
        "OPENAI_MODEL",
        "GEMINI_MODEL",
        "GOOGLE_MODEL",
        "ANTHROPIC_MODEL",
        "CLAUDE_MODEL",
        "DEEPSEEK_MODEL",
        "MISTRAL_MODEL",
        "OLLAMA_MODEL",
    )
    for var in provider_models:
        v = _env(var)
        if v:
            return v

    if provider == "nous" and _detect_groq_key_shape(_resolve_api_key("nous")):
        return PROVIDER_PRESETS["groq"]["model"]

    if provider in PROVIDER_PRESETS:
        return PROVIDER_PRESETS[provider]["model"]
    return ""


def resolve_judge_model() -> str:
    """Model used to EVALUATE answers (the judge), separable from the
    question-GENERATION model on purpose: authoring and judging the same
    checklist with the same model invites self-preference bias
    (RepoProbe, arXiv 2608.04783, keeps its judge outside the evaluated set).

    UNDERSTANDING_AGENT_JUDGE_MODEL overrides; absent it, the normal model is
    used (single-model setups are still valid, just less controlled).
    """
    return _configured("UNDERSTANDING_AGENT_JUDGE_MODEL") or resolve_model()


def _resolve_api_key(provider: str) -> str:
    """Resolve the API key for a provider, honoring central vars first."""
    for var in ("UNDERSTANDING_AGENT_API_KEY", "LLM_API_KEY", "API_KEY"):
        v = _env(var)
        if v:
            return v

    if provider in PROVIDER_PRESETS:
        preset = PROVIDER_PRESETS[provider]
        for var in (preset["api_key_env"],) + preset["alt_api_key_envs"]:
            v = _env(var)
            if v:
                return v

    v = _env(f"{provider.upper()}_API_KEY")
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

    # Check any known provider API key in environment
    for prov_name, preset in PROVIDER_PRESETS.items():
        if prov_name in ("custom", "ollama"):
            continue
        for var in (preset["api_key_env"],) + preset["alt_api_key_envs"]:
            v = _env(var)
            if v:
                return v

    # ollama and other local presets need no key.
    if provider in ("ollama",):
        return ""

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
