"""Single LLM configuration.

Every call — summaries, questions, answer evaluation, follow-ups — goes to
the org OpenAI-compatible gateway. Change the model by setting
UNDERSTANDING_AGENT_MODEL (or MODEL) in the environment or .env.
"""

import os
import subprocess

# Endpoint is fixed. The model id is the only thing you swap.
ORG_LLM_BASE_URL = "http://llm.yorkdevs.link:2222/v1"
ORG_LLM_MODEL = "default"

_MODEL_ENV_NAMES = ("UNDERSTANDING_AGENT_MODEL", "LLM_MODEL", "MODEL")


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _dotenv_values() -> dict:
    """Model id from .env files. Process environment wins.

    Tests set UNDERSTANDING_AGENT_IGNORE_DOTENV=1.
    """
    if _env("UNDERSTANDING_AGENT_IGNORE_DOTENV") == "1":
        return {}
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
                        if key not in _MODEL_ENV_NAMES or key in found:
                            continue
                        val = val.strip()
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val = val[1:-1].strip()
                        if val:
                            found[key] = val
            except Exception:
                continue
    return found


def resolve_base_url() -> str:
    """Org gateway. Not switchable per provider."""
    return ORG_LLM_BASE_URL


def resolve_model() -> str:
    """The one model id used for every request.

    Process env wins, then .env, then ORG_LLM_MODEL.
    """
    for name in _MODEL_ENV_NAMES:
        value = _env(name)
        if value:
            return value
    file_values = _dotenv_values()
    for name in _MODEL_ENV_NAMES:
        value = file_values.get(name, "")
        if value:
            return value
    return ORG_LLM_MODEL


def resolve_judge_model() -> str:
    """Answer evaluation uses the same model as every other call."""
    return resolve_model()


def resolve_api_key() -> str:
    """Process-env half of key lookup. .env files are read in api_utils."""
    for name in ("UNDERSTANDING_AGENT_API_KEY", "LLM_API_KEY", "API_KEY"):
        value = _env(name)
        if value:
            return value
    return ""


class LLMManager:
    """Cached view of the single endpoint, model, and key."""

    def __init__(self):
        self._base_url = None
        self._model = None
        self._api_key = None

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
        self._base_url = None
        self._model = None
        self._api_key = None
