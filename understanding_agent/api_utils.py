import os
import json
import re
import time
import subprocess
import http.client
import ssl
from urllib.parse import urlparse

from .llm_manager import resolve_base_url, resolve_model, resolve_api_key, get_provider, PROVIDER_PRESETS, thinking_control_params


def get_base_url() -> str:
    """Return the OpenAI-compatible base URL (resolved by llm_manager)."""
    return resolve_base_url()


def get_model() -> str:
    """Return the LLM model id (resolved by llm_manager)."""
    return resolve_model()


def fail_open_enabled() -> bool:
    """When the LLM service is unreachable, allow the commit (default) or block it.

    UNDERSTANDING_AGENT_FAIL_MODE=closed blocks commits when evaluation
    cannot run. Any other value (or unset) fails open with a warning.
    """
    return os.environ.get("UNDERSTANDING_AGENT_FAIL_MODE", "").strip().lower() != "closed"


def _parse_key_from_file(filepath: str, preferred_keys: tuple = ("NOUS_API_KEY", "NOUSRESEARCH_API_KEY", "NOUS_PORTAL_API_KEY", "GROQ_API_KEY")) -> str:
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


def load_nous_api_key() -> str:
    """Load the active provider's API key from the environment or .env files.

    Provider-aware, resolved via llm_manager:
      1. UNDERSTANDING_AGENT_API_KEY / the provider preset's env vars
      2. .env / .env.local in cwd, parents, git root, ~/.config
      3. Legacy NOUS_* then GROQ_API_KEY names, for backward compatibility
    """
    # 1. Environment variables (process env takes precedence over disk files)
    env_key = resolve_api_key()
    if env_key:
        return env_key

    # Legacy GROQ_API_KEY env var outranks any key found in .env files
    # (preserves the pre-manager precedence: all env vars before all files).
    groq_env = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_env:
        return groq_env

    provider = get_provider()
    preset = PROVIDER_PRESETS[provider]
    file_key_names = (preset["api_key_env"],) + preset["alt_api_key_envs"] + ("UNDERSTANDING_AGENT_API_KEY",)

    search_dirs = _env_search_dirs()

    # 2. Check .env files for the provider's key names first
    for d in search_dirs:
        for fname in (".env", ".env.local"):
            key = _parse_key_from_file(os.path.join(d, fname), file_key_names)
            if key:
                return key

    key = _parse_key_from_file(os.path.expanduser("~/.config/understanding-agent/.env"), file_key_names)
    if key:
        return key

    # 3. Legacy fallback names (NOUS_* then GROQ) for pre-manager setups
    legacy_nous = ("NOUS_API_KEY", "NOUSRESEARCH_API_KEY", "NOUS_PORTAL_API_KEY")
    for d in search_dirs:
        for fname in (".env", ".env.local"):
            key = _parse_key_from_file(os.path.join(d, fname), legacy_nous)
            if key:
                return key

    key = _parse_key_from_file(os.path.expanduser("~/.config/understanding-agent/.env"), legacy_nous)
    if key:
        return key

    for d in search_dirs:
        for fname in (".env", ".env.local"):
            key = _parse_key_from_file(os.path.join(d, fname), ("GROQ_API_KEY",))
            if key:
                return key

    return _parse_key_from_file(os.path.expanduser("~/.config/understanding-agent/.env"), ("GROQ_API_KEY",))


# Alias for backward compatibility
load_groq_api_key = load_nous_api_key
load_api_key = load_nous_api_key


def _safe_json_loads(s: str):
    """Safely parse JSON or python literal representations with resilience to formatting errors."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()

    # 1. Standard json.loads
    try:
        return json.loads(s)
    except Exception:
        pass

    # 2. Fix trailing commas before closing braces/brackets
    cleaned = re.sub(r",\s*([\]}])", r"\1", s)
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 3. Python literal eval fallback for single quotes, True/False/None
    try:
        import ast
        py_s = re.sub(r"\btrue\b", "True", cleaned, flags=re.IGNORECASE)
        py_s = re.sub(r"\bfalse\b", "False", py_s, flags=re.IGNORECASE)
        py_s = re.sub(r"\bnull\b", "None", py_s, flags=re.IGNORECASE)
        res = ast.literal_eval(py_s)
        if isinstance(res, (dict, list)):
            return res
    except Exception:
        pass

    return None


def extract_json(text: str):
    """Robustly extract a JSON object or array from LLM response text.

    Handles:
    - Reasoning/thinking tags
    - Markdown code fences (```json ... ```)
    - Trailing commas before } and ]
    - Single quotes / python syntax
    - Conversational text before or after JSON
    - Leading/trailing whitespace
    """
    if not text or not isinstance(text, str):
        return None

    # 1. Remove reasoning / thinking tags (e.g. Qwen, DeepSeek)
    cleaned = _strip_reasoning(text).strip()

    # 2. Try markdown code fences first
    fence_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    for match in re.finditer(fence_pattern, cleaned, re.IGNORECASE):
        candidate = match.group(1).strip()
        res = _safe_json_loads(candidate)
        if res is not None:
            return res

    # 3. Try finding outermost JSON array: [...]
    start_arr = cleaned.find('[')
    end_arr = cleaned.rfind(']')
    if start_arr != -1 and end_arr != -1 and end_arr > start_arr:
        candidate = cleaned[start_arr:end_arr + 1].strip()
        res = _safe_json_loads(candidate)
        if res is not None:
            return res

    # 4. Try finding outermost JSON object: {...}
    start_obj = cleaned.find('{')
    end_obj = cleaned.rfind('}')
    if start_obj != -1 and end_obj != -1 and end_obj > start_obj:
        candidate = cleaned[start_obj:end_obj + 1].strip()
        res = _safe_json_loads(candidate)
        if res is not None:
            return res

    # 5. Direct parse attempt as fallback
    return _safe_json_loads(cleaned)


def _strip_reasoning(text: str) -> str:
    """Remove <think>...</think> reasoning tags emitted by reasoning models."""
    return re.sub(r"<think>[\s\S]*?</think>", "", text)


def _extract_message_text(data: dict) -> str:
    """Pull the assistant text out of an OpenAI-compatible response dict.

    Returns "" for the gateway's intermittent empty-content glitch
    (HTTP 200, finish_reason=stop, content=null — observed on the Nous
    gateway roughly 1 call in 5; bench data in scripts/bench_capture_anomaly.py).
    """
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    text = (msg.get("content") or "").strip()
    if not text and msg.get("reasoning"):
        text = (msg.get("reasoning") or "").strip()
    if not text and msg.get("reasoning_content"):
        text = (msg.get("reasoning_content") or "").strip()
    if not text and choices[0].get("text"):
        text = (choices[0].get("text") or "").strip()
    return text


def call_nous_api(api_key: str, payload_dict: dict, timeout: int = 30, retries: int = 2) -> tuple[int, str]:
    """Send an HTTP request to the provider's OpenAI-compatible chat-completions API.

    Retries transient failures (connection errors, 429, 5xx, and the gateway's
    200-with-empty-content glitch) with a short exponential backoff. Returns
    (status_code, response_text_or_error). status_code 0 means the request
    never got a response.
    """
    if not api_key:
        return 0, "No API key configured for the active provider (set UNDERSTANDING_AGENT_API_KEY, NOUS_API_KEY, or GROQ_API_KEY)"

    # Suppress model thinking for providers that support it (bench-measured:
    # deepseek-v4-flash 51s -> 10s per call). Kill switch:
    # UNDERSTANDING_AGENT_DISABLE_THINKING=off
    payload_dict = dict(payload_dict)
    payload_dict.update(thinking_control_params())

    last_status, last_body = 0, ""
    for attempt in range(retries + 1):
        status, body = _groq_request_once(api_key, payload_dict, timeout)
        if _is_meaningful_response(status, body):
            return status, body
        last_status, last_body = status, body
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return last_status, last_body


def _is_meaningful_response(status: int, body: str) -> bool:
    """A response is final only if it succeeded AND carries assistant text.

    The Nous gateway intermittently returns HTTP 200 with
    finish_reason=stop and content=null while still charging tokens
    (completion_tokens=206 in the captured sample). Treating that as
    success made callers silently fall back and re-call the LLM.
    """
    if status != 200:
        return status != 0 and status != 429 and status < 500
    try:
        data = json.loads(body)
    except Exception:
        return True  # can't judge; let the caller's parser deal with it
    return bool(_extract_message_text(data))


def _groq_request_once(api_key: str, payload_dict: dict, timeout: int) -> tuple[int, str]:
    """Compatibility bridge for mock patches and direct calls."""
    return _nous_request_once(api_key, payload_dict, timeout)


# Backward compatibility alias
call_groq_api = call_nous_api


def _nous_request_once(api_key: str, payload_dict: dict, timeout: int) -> tuple[int, str]:
    base_url = get_base_url().rstrip("/")
    parsed = urlparse(base_url)
    scheme = parsed.scheme or "https"
    host = parsed.netloc or "inference-api.nousresearch.com"
    base_path = parsed.path or "/v1"

    if base_path.endswith("/chat/completions"):
        endpoint_path = base_path
    else:
        endpoint_path = f"{base_path}/chat/completions"

    payload = json.dumps(payload_dict).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "understanding-agent/0.3.0",
        "Content-Length": str(len(payload))
    }

    conn = None
    try:
        if scheme == "http":
            conn = http.client.HTTPConnection(host, timeout=timeout)
        else:
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, context=ctx, timeout=timeout)

        conn.request("POST", endpoint_path, body=payload, headers=headers)
        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        return response.status, body
    except Exception as e:
        return 0, str(e)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# Backward compatibility alias
_groq_request_once = _nous_request_once
