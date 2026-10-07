"""Repeat the ambiguous bench scenarios N times to separate outliers from stable effects,
and dump raw bodies for the zero-output anomaly."""

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
from bench_llm_latency import realistic_question_prompt  # reuse the exact prompt

TIMEOUT = 90


def raw_call(api_key, payload):
    base_url = get_base_url().rstrip("/")
    parsed = urlparse(base_url)
    host = parsed.netloc
    base_path = parsed.path or "/v1"
    endpoint = base_path if base_path.endswith("/chat/completions") else f"{base_path}/chat/completions"
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "understanding-agent-bench/0.1",
    }
    conn = None
    try:
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(host, context=ctx, timeout=TIMEOUT)
        t0 = time.perf_counter()
        conn.request("POST", endpoint, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", "replace")
        return resp.status, time.perf_counter() - t0, data
    except Exception as e:
        return 0, 0.0, str(e)
    finally:
        if conn:
            conn.close()


def main():
    api_key = load_nous_api_key()
    model = get_model()
    msgs = [{"role": "user", "content": realistic_question_prompt()}]

    scenarios = {
        "A1 max_tokens=1200":                {"model": model, "messages": msgs, "temperature": 0.4, "max_tokens": 1200},
        "D2 reasoning_effort=none":          {"model": model, "messages": msgs, "temperature": 0.4, "max_tokens": 1200, "reasoning_effort": "none"},
        "B1 max_tokens=300":                 {"model": model, "messages": msgs, "temperature": 0.4, "max_tokens": 300},
        "F1 max_tokens=600":                 {"model": model, "messages": msgs, "temperature": 0.4, "max_tokens": 600},
    }

    N = 3
    print(f"Model: {model}  |  {N} runs each, non-streaming (the hook's real path)\n")
    for label, payload in scenarios.items():
        times, lens, finish = [], [], set()
        for i in range(N):
            status, elapsed, data = raw_call(api_key, payload)
            content_len = 0
            if status == 200:
                try:
                    j = json.loads(data)
                    c = j["choices"][0]
                    content_len = len(c.get("message", {}).get("content") or "")
                    finish.add(c.get("finish_reason"))
                except Exception:
                    pass
            times.append(elapsed)
            lens.append(content_len)
            print(f"  {label:<28} run{i+1}: HTTP {status} {elapsed:6.2f}s out={content_len:>4} finish={finish and sorted(finish)}")
            time.sleep(1)
        avg = sum(times) / len(times)
        print(f"  {label:<28} avg: {avg:6.2f}s   outputs={lens}  finish_reasons={sorted(finish)}\n")

    # Zero-output deep-dive: full body of B1
    print("--- B1 raw body (max_tokens=300), full response ---")
    status, elapsed, data = raw_call(api_key, scenarios["B1 max_tokens=300"])
    print(f"HTTP {status} in {elapsed:.2f}s")
    print(data[:2000])


if __name__ == "__main__":
    main()
