import os
import json
import re
import http.client
import ssl


def _parse_key_from_file(filepath: str) -> str:
    """Extract GROQ_API_KEY from an env file or shell config file."""
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
    """Load GROQ_API_KEY from environment, directory tree .env files, or user profiles."""
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if api_key:
        return api_key

    # Walk up from current working directory to filesystem root
    search_dir = os.path.abspath(os.getcwd())
    while True:
        for fname in (".env", ".env.local"):
            key = _parse_key_from_file(os.path.join(search_dir, fname))
            if key:
                return key
        parent = os.path.dirname(search_dir)
        if parent == search_dir:
            break
        search_dir = parent

    # Check common user configs and shell profile paths
    user_locations = [
        os.path.expanduser("~/.env"),
        os.path.expanduser("~/.config/groq/.env"),
        os.path.expanduser("~/.config/understanding-agent/.env"),
        os.path.expanduser("~/.zshrc"),
        os.path.expanduser("~/.zprofile"),
        os.path.expanduser("~/.bash_profile"),
        os.path.expanduser("~/.bashrc"),
    ]
    for loc in user_locations:
        key = _parse_key_from_file(loc)
        if key:
            return key

    return ""


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


def call_groq_api(api_key: str, payload_dict: dict, timeout: int = 30) -> tuple[int, str]:
    """Send an HTTP request to the Groq Chat Completions API.

    Returns (status_code, response_text_or_error).
    """
    if not api_key:
        return 0, "No GROQ_API_KEY found"

    payload = json.dumps(payload_dict).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Length": str(len(payload))
    }

    try:
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection("api.groq.com", context=ctx, timeout=timeout)
        conn.request("POST", "/openai/v1/chat/completions", body=payload, headers=headers)
        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        return response.status, body
    except Exception as e:
        return 0, str(e)
