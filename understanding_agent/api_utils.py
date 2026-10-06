import os
import json
import re
import time
import subprocess
import http.client
import ssl


# Default Groq model (override with UNDERSTANDING_AGENT_MODEL)
_DEFAULT_MODEL = "qwen/qwen3.8-27b"


def get_model() -> str:
    """Return the LLM model name, allowing override via UNDERSTANDING_AGENT_MODEL."""
    return os.environ.get("UNDERSTANDING_AGENT_MODEL", "").strip() or _DEFAULT_MODEL


def fail_open_enabled() -> bool:
    """When the LLM service is unreachable, allow the commit (default) or block it.

    UNDERSTANDING_AGENT_FAIL_MODE=closed blocks commits when evaluation
    cannot run. Any other value (or unset) fails open with a warning.
    """
    return os.environ.get("UNDERSTANDING_AGENT_FAIL_MODE", "").strip().lower() != "closed"


def _parse_key_from_file(filepath: str) -> str:
    """Extract GROQ_API_KEY from an env file (KEY=VALUE or export KEY=VALUE)."""
    if not filepath or not os.path.exists(filepath):
        return ""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if line.startswith("GROQ_API_KEY"):
                    parts = line.split("=", 1)
                    if len(parts) == 2 and parts[0].strip() == "GROQ_API_KEY":
                        val = parts[1].strip()
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val = val[1:-1].strip()
                        if val:
                            return val
    except Exception:
        pass
    return ""


def load_groq_api_key() -> str:
    """Load GROQ_API_KEY from the environment or explicit local env files.

    Search order (intentionally conservative — no shell history/config parsing,
    no walking up beyond the repository root):
      1. GROQ_API_KEY environment variable
      2. .env / .env.local in the current working directory
      3. .env / .env.local in the git repository root
      4. ~/.config/understanding-agent/.env
    """
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if api_key:
        return api_key

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

    for d in search_dirs:
        for fname in (".env", ".env.local"):
            key = _parse_key_from_file(os.path.join(d, fname))
            if key:
                return key

    return _parse_key_from_file(os.path.expanduser("~/.config/understanding-agent/.env"))


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


def call_groq_api(api_key: str, payload_dict: dict, timeout: int = 30, retries: int = 2) -> tuple[int, str]:
    """Send an HTTP request to the Groq Chat Completions API.

    Retries transient failures (connection errors, 429, 5xx) with a short
    exponential backoff. Returns (status_code, response_text_or_error).
    status_code 0 means the request never got a response.
    """
    if not api_key:
        return 0, "No GROQ_API_KEY found"

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
    payload = json.dumps(payload_dict).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Length": str(len(payload))
    }

    conn = None
    try:
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection("api.groq.com", context=ctx, timeout=timeout)
        conn.request("POST", "/openai/v1/chat/completions", body=payload, headers=headers)
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
