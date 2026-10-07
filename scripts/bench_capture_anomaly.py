"""Capture the intermittent empty-content 200 response (raw body) and
stability-check reasoning_effort=none on deepseek-v4-flash."""

import json
import sys
import time
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from understanding_agent.api_utils import load_nous_api_key  # noqa: E402
from bench_llm_latency import realistic_question_prompt  # noqa: E402
from bench_repeat import raw_call  # noqa: E402


def main():
    api_key = load_nous_api_key()
    msgs = [{"role": "user", "content": realistic_question_prompt()}]

    print("--- hunting an empty-content response on qwen3-coder (up to 8 calls) ---")
    captured = None
    for i in range(8):
        payload = {"model": "qwen/qwen3-coder-30b-a3b-instruct", "messages": msgs,
                   "temperature": 0.4, "max_tokens": 1200}
        status, elapsed, data = raw_call(api_key, payload)
        content_len = -1
        try:
            j = json.loads(data)
            content_len = len(j["choices"][0]["message"].get("content") or "")
        except Exception:
            pass
        print(f"  call{i+1}: HTTP {status} {elapsed:5.2f}s content={content_len}")
        if status == 200 and content_len == 0:
            captured = data
            print("\n  CAPTURED EMPTY RESPONSE — raw body:")
            print("  " + data[:2500])
            break
        time.sleep(1)
    if not captured:
        print("  (no empty response this run — intermittent)")

    print("\n--- deepseek-v4-flash + reasoning_effort=none stability (3 runs) ---")
    times = []
    for i in range(3):
        payload = {"model": "deepseek/deepseek-v4-flash", "messages": msgs,
                   "temperature": 0.4, "max_tokens": 1200, "reasoning_effort": "none"}
        status, elapsed, data = raw_call(api_key, payload)
        info = ""
        if status == 200:
            try:
                j = json.loads(data)
                c = j["choices"][0]["message"]
                info = f"out={len(c.get('content') or '')} reasoning_chars={len(c.get('reasoning') or '')} finish={j['choices'][0].get('finish_reason')}"
            except Exception as e:
                info = f"parse: {e}"
        print(f"  run{i+1}: HTTP {status} {elapsed:5.2f}s | {info}")
        times.append(elapsed)
        time.sleep(1)
    print(f"  avg: {sum(times)/len(times):.2f}s")


if __name__ == "__main__":
    main()
