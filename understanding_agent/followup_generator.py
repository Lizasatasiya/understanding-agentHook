import json
from .api_utils import load_groq_api_key, call_groq_api

class FollowUpGenerator:
    def generate(self, question: dict, answer: dict, evaluation: dict) -> str:
        api_key = load_groq_api_key()
        if not api_key:
            return "Could you elaborate on what happens when the validation fails?"
            
        prompt = self._build_prompt(question, answer, evaluation)
        result = self._call_groq(api_key, prompt)
        if result:
            return result
        return "Could you provide more specific details?"

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
        payload = {
            "model": "qwen/qwen3.8-27b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5,
            "max_tokens": 128
        }

        status, body = call_groq_api(api_key, payload, timeout=30)
        if status != 200:
            return ""

        try:
            result = json.loads(body)
            text = result["choices"][0]["message"]["content"].strip()
            
            # Clean think tags if any
            if "<think>" in text and "</think>" in text:
                text = text.split("</think>")[-1].strip()

            # Remove any surrounding quotes
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            if text.startswith('`') and text.endswith('`'):
                text = text.strip('`')
                
            return text.strip()
        except Exception:
            return ""
