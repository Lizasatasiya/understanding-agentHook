import os
import json
import re
import time
import subprocess
import http.client
import ssl
from urllib.parse import urlparse

from .llm_manager import resolve_base_url, resolve_model, resolve_api_key, describe_api_key, get_provider, thinking_control_params


def get_base_url() -> str:
    """Return the OpenAI-compatible base URL (resolved by llm_manager)."""
    return resolve_base_url()


def get_model() -> str:
    """Return the LLM model id (resolved by llm_manager)."""
    return resolve_model()


def get_judge_model() -> str:
    """Model used to EVALUATE answers, deliberately separable from the
    question-GENERATION model (RepoProbe: judge and checklist-author should
    differ to avoid self-preference bias). UNDERSTANDING_AGENT_JUDGE_MODEL
    overrides; absent it, the normal model is used.

    Resolution lives in the central LLM manager (llm_manager.resolve_judge_model);
    this stays as the compatibility surface callers already import.
    """
    from .llm_manager import resolve_judge_model
    return resolve_judge_model()


def log_llm_error(message: str) -> None:
    """Print an LLM failure for the developer. Never includes the API key."""
    text = (message or "").strip() or "unknown LLM error"
    if len(text) > 2000:
        text = text[:2000] + "...(truncated)"
    print(f"\n  \033[91m[LLM] ERROR: {text}\033[0m\n", flush=True)


def llm_request_target() -> tuple[str, str, str, str]:
    """Return scheme, host, path, and the exact URL the next call will use."""
    base_url = get_base_url().rstrip("/")
    provider = get_provider()
    parsed = urlparse(base_url)
    scheme = parsed.scheme or "https"
    host = parsed.netloc or "inference-api.nousresearch.com"
    base_path = parsed.path or "/v1"
    if _is_anthropic(base_url, provider):
        if base_path.endswith("/messages"):
            endpoint_path = base_path
        else:
            endpoint_path = f"{base_path}/messages" if base_path else "/v1/messages"
    else:
        if base_path.endswith("/chat/completions"):
            endpoint_path = base_path
        else:
            endpoint_path = f"{base_path}/chat/completions"
    return scheme, host, endpoint_path, f"{scheme}://{host}{endpoint_path}"


def _llm_failure_detail(status: int, body: str, model_id: str) -> str | None:
    """None when the response is a successful assistant message."""
    url = llm_request_target()[3]
    if status == 200:
        try:
            data = json.loads(body)
        except Exception:
            return None
        if _extract_message_text(data):
            return None
        snippet = (body or "").strip()
        if len(snippet) > 1500:
            snippet = snippet[:1500] + "...(truncated)"
        return (
            f"model={model_id} at {url} returned HTTP 200 with no assistant text. "
            f"Body: {snippet or '(empty)'}"
        )
    snippet = (body or "").strip()
    if len(snippet) > 1500:
        snippet = snippet[:1500] + "...(truncated)"
    if status == 0:
        return f"could not connect to {url} for model={model_id}. {snippet or 'connection failed'}"
    detail = (
        f"model={model_id} at {url} returned HTTP {status}. "
        f"{snippet or '(empty body)'}"
    )
    if status in (401, 403):
        detail += " " + describe_api_key()
    return detail


def fail_open_enabled() -> bool:
    """When the LLM service is unreachable, allow the commit (default) or block it.

    UNDERSTANDING_AGENT_FAIL_MODE=closed blocks commits when evaluation
    cannot run. Any other value (or unset) fails open with a warning.
    """
    return os.environ.get("UNDERSTANDING_AGENT_FAIL_MODE", "").strip().lower() != "closed"


def _parse_key_from_file(filepath: str, preferred_keys: tuple = ("UNDERSTANDING_AGENT_API_KEY", "NOUS_API_KEY", "ANTHROPIC_API_KEY")) -> str:
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
    """Directories searched for .env / .env.local: agent hook package root, cwd, its parents, git root."""
    search_dirs = []

    # 1. Always include agent hook's own installation root directory first
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


def load_nous_api_key() -> str:
    """API key for the selected model. See llm_manager.resolve_api_key_info."""
    return resolve_api_key()


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
    """Pull the assistant text out of an LLM response dict (OpenAI, Anthropic, Gemini, Ollama)."""
    if not isinstance(data, dict):
        return ""

    # 1. OpenAI-compatible choices: choices[0].message.content
    choices = data.get("choices") or []
    if choices and isinstance(choices, list):
        first = choices[0] if isinstance(choices[0], dict) else {}
        msg = first.get("message") or {}
        if isinstance(msg, dict):
            text = (msg.get("content") or "").strip()
            if not text and msg.get("reasoning"):
                text = (msg.get("reasoning") or "").strip()
            if not text and msg.get("reasoning_content"):
                text = (msg.get("reasoning_content") or "").strip()
            if text:
                return text
        if first.get("text"):
            text = (first.get("text") or "").strip()
            if text:
                return text

    # 2. Anthropic format: content: [{"type": "text", "text": "..."}]
    content = data.get("content")
    if isinstance(content, list):
        texts = [
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
        ]
        if texts:
            return "".join(texts).strip()
    elif isinstance(content, str) and content.strip():
        return content.strip()

    # 3. Gemini format: candidates: [{"content": {"parts": [{"text": "..."}]}}]
    candidates = data.get("candidates")
    if isinstance(candidates, list) and candidates:
        first_c = candidates[0] if isinstance(candidates[0], dict) else {}
        parts = first_c.get("content", {}).get("parts") or []
        if isinstance(parts, list):
            texts = [
                p.get("text", "") for p in parts
                if isinstance(p, dict) and p.get("text")
            ]
            if texts:
                return "".join(texts).strip()

    # 4. Ollama / generic message dict or output / response fields
    if isinstance(data.get("message"), dict) and data["message"].get("content"):
        return str(data["message"]["content"]).strip()
    for field in ("response", "output", "text"):
        if data.get(field) and isinstance(data[field], str):
            return data[field].strip()

    return ""


def _normalize_response_body(body: str, model_id: str = "") -> str:
    """Normalize responses from Anthropic, Gemini, Ollama, etc. into standard OpenAI format."""
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            return body
        # If already standard OpenAI format with choices containing a message, keep as is
        if data.get("choices") and isinstance(data["choices"], list):
            first = data["choices"][0]
            if isinstance(first, dict) and "message" in first:
                return body

        # Extract text from non-standard formats and wrap into OpenAI shape
        extracted = _extract_message_text(data)
        if extracted:
            return json.dumps({
                "id": data.get("id", "res-norm"),
                "object": "chat.completion",
                "model": model_id or data.get("model", ""),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": extracted,
                        },
                        "finish_reason": data.get("stop_reason") or data.get("finish_reason") or "stop",
                    }
                ],
            })
    except Exception:
        pass
    return body


def call_llm_api(api_key: str, payload_dict: dict, timeout: int = 30, retries: int = 2) -> tuple[int, str]:
    """Send an HTTP request to the provider's API (OpenAI-compatible or Anthropic).

    Retries transient failures (connection errors, 429, 5xx, and the gateway's
    200-with-empty-content glitch) with a short exponential backoff. Returns
    (status_code, response_text_or_error). status_code 0 means the request
    never got a response.
    """
    if not api_key:
        return 0, "No API key configured (set UNDERSTANDING_AGENT_API_KEY)"

    model_id = payload_dict.get("model") or "(none)"
    print(f"  [LLM] model={model_id} -> {llm_request_target()[3]}", flush=True)

    # Suppress model thinking for providers that support it (bench-measured:
    # deepseek-v4-flash 51s -> 10s per call). Kill switch:
    # UNDERSTANDING_AGENT_DISABLE_THINKING=off
    payload_dict = dict(payload_dict)
    payload_dict.update(thinking_control_params())

    last_status, last_body = 0, ""
    for attempt in range(retries + 1):
        status, body = _groq_request_once(api_key, payload_dict, timeout)
        if _is_meaningful_response(status, body):
            detail = _llm_failure_detail(status, body, model_id)
            if detail:
                log_llm_error(detail)
            return status, body
        last_status, last_body = status, body
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    detail = _llm_failure_detail(last_status, last_body, model_id)
    if detail:
        log_llm_error(detail)
    return last_status, last_body


def _is_meaningful_response(status: int, body: str) -> bool:
    """A response is final only if it succeeded AND carries assistant text."""
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


# ---------------------------------------------------------------------------
# Naming: call_llm_api / load_api_key are the canonical, provider-agnostic
# names. The historic nous/groq names remain as deprecated aliases pointing at
# the SAME function objects, so existing imports, mock-patch targets, and
# colleague code keep working unchanged. Call sites migrate gradually.
# ---------------------------------------------------------------------------
call_nous_api = call_llm_api          # deprecated alias
call_groq_api = call_llm_api          # deprecated alias
# load_api_key / load_groq_api_key aliases are defined near load_nous_api_key.


def _is_anthropic(base_url: str, provider: str) -> bool:
    return provider == "anthropic" or "api.anthropic.com" in base_url


def _format_anthropic_payload(payload_dict: dict) -> dict:
    anthropic_payload = {
        "model": payload_dict.get("model", "claude-sonnet-5-5"),
        "max_tokens": payload_dict.get("max_tokens", 1024),
    }
    if "temperature" in payload_dict:
        anthropic_payload["temperature"] = payload_dict["temperature"]

    raw_messages = payload_dict.get("messages", [])
    system_parts = []
    filtered_messages = []
    for m in raw_messages:
        if isinstance(m, dict):
            if m.get("role") == "system":
                system_parts.append(m.get("content", ""))
            else:
                filtered_messages.append({
                    "role": m.get("role", "user"),
                    "content": m.get("content", ""),
                })
    if system_parts:
        anthropic_payload["system"] = "\n\n".join(system_parts)
    anthropic_payload["messages"] = filtered_messages
    return anthropic_payload


def _nous_request_once(api_key: str, payload_dict: dict, timeout: int) -> tuple[int, str]:
    base_url = get_base_url().rstrip("/")
    provider = get_provider()
    scheme, host, endpoint_path, _full_url = llm_request_target()

    if _is_anthropic(base_url, provider):
        payload_data = _format_anthropic_payload(payload_dict)
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": "understanding-agent/0.3.0",
        }
    else:
        payload_data = payload_dict
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "understanding-agent/0.3.0",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    payload = json.dumps(payload_data).encode("utf-8")
    headers["Content-Length"] = str(len(payload))

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

        # Normalize non-OpenAI responses into standard OpenAI format
        if response.status == 200:
            body = _normalize_response_body(body, payload_dict.get("model", ""))

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
