import os
import json
import http.client
import ssl

class FollowUpGenerator:
    def generate(self, question: dict, answer: dict, evaluation: dict) -> str:
        api_key = self._load_api_key()
        if not api_key:
            return "Could you elaborate on what happens when the validation fails?"
            
        prompt = self._build_prompt(question, answer, evaluation)
        result = self._call_groq(api_key, prompt)
        if result:
            return result
        return "Could you provide more specific details?"

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
                            api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if api_key:
                                return api_key
            search_dir = os.path.dirname(search_dir)

        return ""
        
    def _build_prompt(self, question: dict, answer: dict, evaluation: dict) -> str:
        missing = ", ".join(evaluation.get('missing_concepts', []))
        return f"""You are evaluating a developer's understanding.
Original Question: {question.get('question')}
Developer Answer: {answer.get('answer')}
Evaluation: {evaluation.get('evaluation')}
Missing Concepts: {missing}

Generate a SINGLE follow-up question that targets exactly the missing concepts.
Return ONLY the question text. Do not use quotes, JSON, or formatting.
"""

    def _call_groq(self, api_key: str, prompt: str) -> str:
        payload = json.dumps({
            "model": "qwen/qwen3.8-27b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5,
            "max_tokens": 128
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
                return ""

            result = json.loads(response.read().decode("utf-8"))
            text = result["choices"][0]["message"]["content"].strip()
            
            # Remove any surrounding quotes
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
                
            return text

        except Exception:
            return ""
