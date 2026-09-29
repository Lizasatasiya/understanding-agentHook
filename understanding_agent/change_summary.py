import os
import json
import http.client
import ssl

class ChangeSummary:
    def generate(self, context: dict) -> dict:
        api_key = self._load_api_key()
        
        for f in context.get("structured_changes", []):
            if not api_key:
                f["summary"] = self._fallback()
                continue
                
            prompt = self._build_prompt_for_single(f)
            summary = self._call_groq(api_key, prompt)
            f["summary"] = summary if summary else self._fallback()
            
        return {}

    def _load_api_key(self) -> str:
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if api_key:
            return api_key

        search_dir = os.path.dirname(os.path.abspath(__file__))
        for _ in range(4):
            env_path = os.path.join(search_dir, ".env")
            if os.path.exists(env_path):
                with open(env_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("GROQ_API_KEY"):
                            return line.split("=", 1)[1].strip().strip('"').strip("'")
            search_dir = os.path.dirname(search_dir)
        return ""

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
        payload = json.dumps({
            "model": "qwen/qwen3.8-27b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 300
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Length": str(len(payload))
        }

        try:
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection("api.groq.com", context=ctx, timeout=30)
            conn.request("POST", "/openai/v1/chat/completions", body=payload, headers=headers)
            response = conn.getresponse()

            if response.status != 200:
                return {}

            result = json.loads(response.read().decode("utf-8"))
            text = result["choices"][0]["message"]["content"].strip()

            if text.startswith("```json"):
                text = text[7:].strip()
            if text.startswith("```"):
                text = text[3:].strip()
            if text.endswith("```"):
                text = text[:-3].strip()

            parsed = json.loads(text)
            return parsed

        except Exception as e:
            return {}

    def _fallback(self) -> dict:
        return {
            "what_changed": "Unknown change due to missing LLM configuration or parsing error.",
            "why_it_matters": "Cannot determine why it matters.",
            "impact": "Unknown impact."
        }
