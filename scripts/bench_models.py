"""Bench alternative Nous models, including known thinking models, to isolate
whether thinking is the latency source and whether reasoning-disable params help."""

import json
import sys
import time
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from understanding_agent.api_utils import load_nous_api_key, get_base_url  # noqa: E402
from bench_llm_latency import realistic_question_prompt  # noqa: E402
from bench_repeat import raw_call  # noqa: E402

MODELS = [
    "qwen/qwen3-coder-30b-a3b-instruct",      # current default (claims no reasoning)
    "deepseek/deepseek-v4-flash",              # README's old default
    "qwen/qwen3-250m" if False else "qwen/qwen3-coder-30b",  # placeholder guard
]

# Clean the list (drop the placeholder guard)
MODELS = [m for m in MODELS if "placeholder" not in m and m != "qwen/qwen3-coder-30b"]

EXTRA_MODELS = [
    "deepseek/deepseek-v3.1" if False else "deepseek/deepseek-v4-flash",
]

VARIANTS = {
    "plain":  {},
    "no-think (reasoning_effort=none)": {"reasoning_effort": "none"},
}


def main():
    api_key = load_nous_api_key()
    msgs = [{"role": user_role(), "content": realistic_question_prompt()}]

    models = MODELS  # deepseek-v4-flash already included above

    for model in models:
        print(f"\n{'='*76}\nMODEL: {model}\n{'='*76}")
        for vlabel, extra in VARIANTS.items():
            payload = {
                "model": model,
                "messages": msgs,
                "temperature": 0.4,
                "max_tokens": 1200,
                **extra,
            }
            status, elapsed, data = raw_call(api_key, payload)
            info = ""
            reasoning_chars = 0
            if status == 200:
                try:
                    j = json.loads(data)
                    msg = j["choices"][0]["message"]
                    content = msg.get("content") or ""
                    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
                    usage = j.get("usage", {}).get("completion_tokens_details", {}) or {}
                    info = (f"out={len(content):>4} reasoning_chars={len(reasoning):>4} "
                            f"reasoning_tokens={usage.get('reasoning_tokens', '?')} "
                            f"finish={j['choices'][0].get('finish_reason')}")
                except Exception as e:
                    info = f"parse error: {e}"
            else:
                info = data[:150]
            print(f"  {vlabel:<34} HTTP {status} {elapsed:6.2f}s | {info}")
            time.sleep(1)


def user_role():
    return "user"


if __name__ == "__main__":
    main()
