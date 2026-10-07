import os
import json
import re
import time
import subprocess
import http.client
import ssl
from urllib.parse import urlparse


# Default Nous Research Subscription API base URL and model (override with NOUS_BASE_URL / NOUS_MODEL / UNDERSTANDING_AGENT_MODEL)
_DEFAULT_BASE_URL = "https://inference-api.nousresearch.com/v1"
_DEFAULT_MODEL = "qwen/qwen3-coder-30b-a3b-instruct"


def get_base_url() -> str:
    """Return the OpenAI-compatible base URL for Nous Research or Groq API."""
    explicit = os.environ.get("NOUS_BASE_URL", "").strip() or \
               os.environ.get("UNDERSTANDING_AGENT_BASE_URL", "").strip() or \
               os.environ.get("GROQ_BASE_URL", "").strip()
    if explicit:
        return explicit
    provider = os.environ.get("UNDERSTANDING_AGENT_PROVIDER", "").strip().lower()
    if provider == "groq":
        return "https://api.groq.com/openai/v1"
    key = load_nous_api_key()
    if key.startswith("gsk_"):
        return "https://api.groq.com/openai/v1"
    return _DEFAULT_BASE_URL


def get_model() -> str:
    """Return the LLM model name, allowing override via NOUS_MODEL, GROQ_MODEL, or UNDERSTANDING_AGENT_MODEL."""
    explicit = os.environ.get("NOUS_MODEL", "").strip() or \
               os.environ.get("UNDERSTANDING_AGENT_MODEL", "").strip() or \
               os.environ.get("GROQ_MODEL", "").strip()
    if explicit:
        return explicit
    provider = os.environ.get("UNDERSTANDING_AGENT_PROVIDER", "").strip().lower()
    if provider == "groq":
        return "qwen/qwen3.8-27b"
    key = load_nous_api_key()
    if key.startswith("gsk_"):
        return "qwen/qwen3.8-27b"
    return _DEFAULT_MODEL


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


def load_nous_api_key() -> str:
    """Load NOUS_API_KEY (or fallback GROQ_API_KEY) from the environment or explicit local env files.

    Search order:
      1. Explicit NOUS_API_KEY / NOUSRESEARCH_API_KEY / NOUS_PORTAL_API_KEY environment variables
      2. .env / .env.local in search dirs (cwd, parents, git root, ~/.config) strictly searching for NOUS keys
      3. Fallback GROQ_API_KEY from environment or .env files only if no NOUS key was found
    """
    nous_key_names = ("NOUS_API_KEY", "NOUSRESEARCH_API_KEY", "NOUS_PORTAL_API_KEY")
    provider = os.environ.get("UNDERSTANDING_AGENT_PROVIDER", "").strip().lower()

    if provider == "groq":
        groq_env = os.environ.get("GROQ_API_KEY", "").strip()
        if groq_env:
            return groq_env
        search_dirs_early = []
        cur_e = os.path.abspath(os.getcwd())
        for _ in range(4):
            search_dirs_early.append(cur_e)
            parent_e = os.path.dirname(cur_e)
            if parent_e == cur_e:
                break
            cur_e = parent_e
        for d in search_dirs_early:
            for fname in (".env", ".env.local"):
                key = _parse_key_from_file(os.path.join(d, fname), ("GROQ_API_KEY",))
                if key:
                    return key

    # 1. Direct environment variables (process environment takes precedence over disk files)
    for env_var in nous_key_names:
        api_key = os.environ.get(env_var, "").strip()
        if api_key:
            return api_key

    groq_env = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_env:
        return groq_env

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

    # 2. Check all .env files specifically for NOUS keys first
    for d in search_dirs:
        for fname in (".env", ".env.local"):
            key = _parse_key_from_file(os.path.join(d, fname), nous_key_names)
            if key:
                return key

    key = _parse_key_from_file(os.path.expanduser("~/.config/understanding-agent/.env"), nous_key_names)
    if key:
        return key

    # 3. Fallback to GROQ_API_KEY only if no NOUS key was defined anywhere
    groq_env = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_env:
        return groq_env

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
    - <think>...</think> reasoning tags
    - Markdown code fences (```json ... ```)
    - Trailing commas before } and ]
    - Single quotes / python syntax
    - Conversational text before or after JSON
    - Leading/trailing whitespace
    """
    if not text or not isinstance(text, str):
        return None

    # 1. Remove reasoning / thinking tags (e.g. Qwen, DeepSeek)
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()

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


def call_nous_api(api_key: str, payload_dict: dict, timeout: int = 30, retries: int = 2) -> tuple[int, str]:
    """Send an HTTP request to the Nous Research OpenAI-compatible Chat Completions API.

    Retries transient failures (connection errors, 429, 5xx) with a short
    exponential backoff. Returns (status_code, response_text_or_error).
    status_code 0 means the request never got a response.
    """
    if not api_key:
        return 0, "No NOUS_API_KEY or GROQ_API_KEY found"

    last_status, last_body = 0, ""
    for attempt in range(retries + 1):
        status, body = _groq_request_once(api_key, payload_dict, timeout)
        if status == 200 or (status != 0 and status != 429 and status < 500):
            return status, body
        last_status, last_body = status, body
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return last_status, last_body


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
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
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
