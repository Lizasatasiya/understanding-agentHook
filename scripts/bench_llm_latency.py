"""Latency bench for the configured LLM provider (default: Nous Research).

Measures where per-call latency comes from:
  - time to first token (TTFT)  -> thinking/reasoning + queueing + network
  - total time                  -> TTFT + output generation length
  - effects of max_tokens, prompt size, and thinking-disable parameters

Usage (from the repo root, so .env is found):
    python3.14 scripts/bench_llm_latency.py            # full matrix
    python3.14 scripts/bench_llm_latency.py --quick    # baseline + best variant only

The API key is loaded via understanding_agent.api_utils.load_nous_api_key()
(env var or .env) and is NEVER printed or logged.
"""

import http.client
import json
import ssl
import sys
import time
import os
from urllib.parse import urlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from understanding_agent.api_utils import load_nous_api_key, get_base_url, get_model  # noqa: E402

TIMEOUT = 90


def stream_timed_call(api_key, payload):
    """POST /chat/completions with stream=True; measure TTFT and total time.

    Returns (status, ttft_s, total_s, generated_chars, body_snippet).
    Falls back to non-streaming if the server rejects stream=true.
    """
    base_url = get_base_url().rstrip("/")
    parsed = urlparse(base_url)
    host = parsed.netloc
    base_path = parsed.path or "/v1"
    endpoint = base_path if base_path.endswith("/chat/completions") else f"{base_path}/chat/completions"

    body = json.dumps(dict(payload, stream=True)).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "understanding-agent-bench/0.1",
        "Accept": "text/event-stream",
    }

    conn = None
    try:
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(host, context=ctx, timeout=TIMEOUT)
        t0 = time.perf_counter()
        conn.request("POST", endpoint, body=body, headers=headers)
        resp = conn.getresponse()

        if resp.status != 200:
            return resp.status, None, None, 0, resp.read().decode("utf-8", "replace")[:300]

        ttft = None
        generated = 0
        got_reasoning_tokens = 0
        # Read the SSE stream line-by-line
        while True:
            line = resp.readline()
            if not line:
                break
            now = time.perf_counter()
            s = line.decode("utf-8", "replace").strip()
            if not s.startswith("data:"):
                continue
            data = s[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except Exception:
                continue
            if ttft is None and chunk.get("choices"):
                delta = chunk["choices"][0].get("delta") or {}
                # TTFT = first CONTENT token; reasoning tokens also indicate
                # the model has started (but is thinking, not answering)
                if delta.get("content") or delta.get("reasoning") or delta.get("reasoning_content"):
                    ttft = now - t0
            if chunk.get("choices"):
                delta = chunk["choices"][0].get("delta") or {}
                generated += len(delta.get("content") or "")
                if delta.get("reasoning") or delta.get("reasoning_content"):
                    got_reasoning_tokens += len(delta.get("reasoning") or delta.get("reasoning_content") or "")
        total = time.perf_counter() - t0
        return 200, ttft, total, generated, f"reasoning_chars={got_reasoning_tokens}"
    except Exception as e:
        return 0, None, None, 0, str(e)[:300]
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def plain_timed_call(api_key, payload):
    """Non-streaming timed call via the package's own transport (retries off)."""
    from understanding_agent.api_utils import _nous_request_once
    t0 = time.perf_counter()
    status, body = _nous_request_once(api_key, payload, TIMEOUT)
    return status, time.perf_counter() - t0, body[:300]


def realistic_question_prompt():
    """A prompt the same size/shape as QuestionGenerator's micro prompt (~3KB)."""
    diff_lines = "\n".join(
        f"+    total += item['qty'] * item['unit_price_{i}']" for i in range(12))
    return f"""You are a senior developer reviewing a code change.
Generate EXACTLY 3 specific questions to test if the author understands their own change.
CRITICAL: Keep the questions short and simple (under 25 words). Ask one basic thing per question.
Keep it simple but focus on logic and data flow. No generic questions.

Repository Stack: React (typescript)

## Changed Functions
File: src/billing/Calculator.tsx | Function: calculateInvoice()
Summary: Adds line-item totals with per-row discounts Impact: invoice total now includes discount-aware summation
Diff:
```diff
{diff_lines}
+    if (coupon) total = applyCoupon(total, coupon)
+    return round2(total)
```
Dependencies invoked by this function: applyCoupon(), round2()

## Output Format
Return ONLY a JSON array with EXACTLY 3 items. Each item must be an object with:
  "question_id": <a unique string like "q1", "q2">,
  "question": <the question string>,
  "type": one of "Code Logic", "Data Flow", "Edge Cases",
  "expected_concepts": [<list of concept strings>],
  "evaluation_criteria": [<list of criteria strings>]

Example: [{{"question_id": "q1", "question": "Why is X called before Y?", "type": "Code Logic", "expected_concepts": ["X must be validated before Y executes"], "evaluation_criteria": ["understands the validation order"]}}]"""


SMALL_PROMPT = "List 3 colors as a JSON array of strings. Return ONLY the JSON array."


def fmt(status, ttft, total, gen, note):
    ttft_s = f"{ttft:6.2f}s" if ttft is not None else "   n/a "
    total_s = f"{total:6.2f}s" if total is not None else "   n/a "
    return f"HTTP {status} | TTFT {ttft_s} | total {total_s} | out {gen:>5} chars | {note}"


def main():
    quick = "--quick" in sys.argv

    api_key = load_nous_api_key()
    if not api_key:
        print("No API key found (env or .env). Aborting — nothing to test against.")
        sys.exit(1)

    base_url = get_base_url()
    model = get_model()
    # Never print the key; printing base_url/model is safe.
    print(f"Provider endpoint: {base_url}")
    print(f"Model:             {model}")
    print(f"Key:               <loaded, {len(api_key)} chars, not shown>")
    print()

    base_msgs = [{"role": "user", "content": realistic_question_prompt()}]
    small_msgs = [{"role": "user", "content": SMALL_PROMPT}]

    results = []

    def bench(label, payload, stream=True):
        print(f"--- {label}")
        if stream:
            status, ttft, total, gen, note = stream_timed_call(api_key, payload)
            print("    " + fmt(status, ttft, total, gen, note))
            results.append((label, status, ttft, total, gen, note))
        else:
            status, elapsed, note = plain_timed_call(api_key, payload)
            print(f"    HTTP {status} | total {elapsed:6.2f}s | non-stream | {note[:120]}")
            results.append((label, status, None, elapsed, 0, note[:120]))
        print()

    base_payload = {"model": model, "messages": base_msgs, "temperature": 0.4, "max_tokens": 1200}

    # A. Baseline — exactly what QuestionGenerator sends today
    bench("A1 baseline: realistic prompt, max_tokens=1200 (current default)", dict(base_payload))

    if quick:
        # Just the most likely fix to compare
        bench("Q1 baseline + reasoning disabled", dict(base_payload, reasoning={"enabled": False}))
        summary(results)
        return

    # B. Output budget effect
    bench("B1 realistic prompt, max_tokens=300", dict(base_payload, max_tokens=300))

    # C. Prompt size effect (small prompt, same output budget)
    bench("C1 small prompt, max_tokens=1200", {"model": model, "messages": small_msgs, "temperature": 0.4, "max_tokens": 1200})

    # D. Thinking-disable parameter variants (what the gateway accepts)
    bench("D1 + reasoning.enabled=false", dict(base_payload, reasoning={"enabled": False}))
    bench("D2 + reasoning_effort=none", dict(base_payload, reasoning_effort="none"))
    bench("D3 + chat_template_kwargs.enable_thinking=false", dict(base_payload, chat_template_kwargs={"enable_thinking": False}))
    bench("D4 + enable_thinking=false (top-level)", dict(base_payload, enable_thinking=False))

    # E. Non-streaming sanity check (what the hook actually does today)
    bench("E1 baseline via non-streaming transport (hook's real path)", dict(base_payload), stream=False)

    summary(results)


def summary(results):
    print("=" * 78)
    print(f"{'scenario':<58} {'TTFT':>7} {'total':>7}")
    print("-" * 78)
    for label, status, ttft, total, gen, note in results:
        ttft_s = f"{ttft:.2f}s" if ttft is not None else "-"
        total_s = f"{total:.2f}s" if total is not None else "-"
        flag = "" if status == 200 else f" (HTTP {status})"
        print(f"{label:<58}{ttft_s:>7} {total_s:>7}{flag}")
    print("=" * 78)
    print("Reading: TTFT ≈ thinking/queue time; (total - TTFT) ≈ output generation time.")
    print("If TTFT dominates the baseline and a D-variant kills it, thinking is the root cause.")


if __name__ == "__main__":
    main()
