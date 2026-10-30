"""LLM configuration for the three supported models: nous, sonnet, and haiku.

UNDERSTANDING_AGENT_MODEL selects one of those names. Questions and answer
checks both use it. The API key is UNDERSTANDING_AGENT_API_KEY.
"""

import os
import subprocess

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


def _env_search_dirs():
    """Directories searched for .env: hook root, cwd, its parents, git root."""
    search_dirs = []
    # 1. Agent hook's own repo directory (first priority)
    hook_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(hook_dir):
        search_dirs.append(hook_dir)

    cur = os.path.abspath(os.getcwd())
    for _ in range(4):
        if cur not in search_dirs:
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


def _dotenv_values() -> dict:
    """Parse key-value pairs from .env files in search_dirs (first file found wins)."""
    if _env("UNDERSTANDING_AGENT_IGNORE_DOTENV") == "1":
        return {}
    for directory in _env_search_dirs():
        for fname in (".env", ".env.local"):
            path = os.path.join(directory, fname)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    file_found = {}
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
                        if val:
                            file_found[key] = val
                    if file_found:
                        return file_found
            except Exception:
                continue
    return {}


def _configured(name: str) -> str:
    """Process env wins, then .env."""
    return _env(name) or _dotenv_values().get(name, "")


def _global_model_choice() -> str:
    """Model name written by install.sh into ~/.config/understanding-agent/.env."""
    if _env("UNDERSTANDING_AGENT_IGNORE_DOTENV") == "1":
        return ""
    path = os.path.expanduser("~/.config/understanding-agent/.env")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("export "):
                    if line.startswith("export "):
                        line = line[7:].strip()
                    else:
                        continue
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                if key.strip() != "UNDERSTANDING_AGENT_MODEL":
                    continue
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1].strip()
                return val.lower()
    except Exception:
        return ""
    return ""


def installed_model_choice() -> str | None:
    """Return sonnet, haiku, or nous when that is the configured model."""
    raw = (
        _env("UNDERSTANDING_AGENT_MODEL")
        or _env("LLM_MODEL")
        or _env("MODEL")
        or _global_model_choice()
        or _dotenv_values().get("UNDERSTANDING_AGENT_MODEL", "")
    ).lower()
    if raw in SUPPORTED_MODELS:
        return raw
    return None


def _active_choice() -> str:
    """sonnet, haiku, or nous. Anything else falls back to nous."""
    return installed_model_choice() or "nous"


def get_provider() -> str:
    """nous or anthropic, from the selected model."""
    return SUPPORTED_MODELS[_active_choice()]["provider"]


def resolve_base_url() -> str:
    """Endpoint for the selected model."""
    return SUPPORTED_MODELS[_active_choice()]["base_url"]


def resolve_model() -> str:
    """Model id for questions and answer checks."""
    return SUPPORTED_MODELS[_active_choice()]["model"]


def resolve_judge_model() -> str:
    """Answer checks use the same model as question generation."""
    return resolve_model()


_KEY_ENVS = {
    "nous": ("NOUS_API_KEY", "NOUSRESEARCH_API_KEY", "NOUS_PORTAL_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
}


def resolve_api_key() -> str:
    """API key for the selected model. Empty when nothing is configured."""
    for var in ("UNDERSTANDING_AGENT_API_KEY", "LLM_API_KEY", "API_KEY"):
        value = _configured(var)
        if value:
            return value
    for var in _KEY_ENVS[get_provider()]:
        value = _configured(var)
        if value:
            return value
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
