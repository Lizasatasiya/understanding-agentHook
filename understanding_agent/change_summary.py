import os
import json
from .api_utils import load_groq_api_key, extract_json, call_groq_api


class ChangeSummary:
    def generate(self, context: dict) -> dict:
        api_key = load_groq_api_key()
        
        for f in context.get("structured_changes", []):
            if not api_key:
                f["summary"] = self._fallback(f)
                continue
                
            prompt = self._build_prompt_for_single(f)
            summary = self._call_groq(api_key, prompt)
            f["summary"] = summary if summary else self._fallback(f)
            
        return {}

    def _build_prompt_for_single(self, f: dict) -> str:
        lines = [
            "You are a senior developer analyzing a git commit.",
            "Based on the diffs and code context provided, generate a short summary.",
            "Return ONLY a valid JSON object with EXACTLY these three string keys: 'what_changed', 'impact', 'why_it_matters'.",
            "",
            "## Change in this commit",
            f"File: {f.get('file', '?')} | Function: {f.get('function', '?')}()",
            "Diff:",
            f"```diff\n{f.get('diff', '')}\n```",
            "",
            "Return ONLY a JSON object. Example: {\"what_changed\": \"Added X\", \"impact\": \"Y will now Z\", \"why_it_matters\": \"Fixes bug A\"}"
        ]
        return "\n".join(lines)

    def _call_groq(self, api_key: str, prompt: str) -> dict:
        payload = {
            "model": "qwen/qwen3.8-27b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 300
        }

        status, body = call_groq_api(api_key, payload, timeout=30)
        if status != 200:
            return {}

        try:
            result = json.loads(body)
            text = result["choices"][0]["message"]["content"].strip()
            parsed = extract_json(text)
            if isinstance(parsed, dict) and "what_changed" in parsed:
                return parsed
            return {}
        except Exception:
            return {}

    def _fallback(self, f: dict = None) -> dict:
        func = f.get('function', '') if f else ''
        file = f.get('file', '') if f else ''
        target = f"{func}() in {file}" if func and file else file or "staged code"
        return {
            "what_changed": f"Updated logic in {target}.",
            "why_it_matters": "Modifies code behavior for this commit.",
            "impact": "Refer to diff above for detailed line changes."
        }
