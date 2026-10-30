import json
from .api_utils import load_nous_api_key, call_nous_api, get_model, log_llm_error

class FollowUpGenerator:
    def generate(self, question: dict, answer: dict, evaluation: dict) -> str:
        api_key = load_nous_api_key()
        if not api_key:
            log_llm_error(
                "No API key configured. Set UNDERSTANDING_AGENT_API_KEY. "
                "No follow-up question will be asked."
            )
            return ""

        prompt = self._build_prompt(question, answer, evaluation)
        result = self._call_groq(api_key, prompt)
        return result or ""

    def rephrase_failed_question(self, question: dict) -> str:
        """Return a model rephrase of a previously failed question.

        The original wording is kept when the model does not return one.
        """
        api_key = load_nous_api_key()
        original = question.get("question", "")
        if not api_key:
            log_llm_error(
                "No API key configured. Set UNDERSTANDING_AGENT_API_KEY. "
                "The original question will be asked again."
            )
            return ""

        prompt = (
            "You are rephrasing a code-understanding question for a developer.\n"
            "The developer failed to answer this question on a previous attempt.\n\n"
            f"Original question: {original}\n\n"
            "RULES:\n"
            "1. Keep the EXACT same concept and difficulty.\n"
            "2. Change only the wording / angle (e.g. ask 'what happens if\u2026' instead of 'how does\u2026').\n"
            "3. Keep it short \u2014 one sentence, plain English, no jargon.\n"
            "4. Return ONLY the rephrased question. No quotes, no markdown, no intros.\n"
        )
        return self._call_groq(api_key, prompt) or ""

    def _build_prompt(self, question: dict, answer: dict, evaluation: dict) -> str:
        missing = ", ".join(evaluation.get('missing_concepts', []))
        return f"""You are generating a quick follow-up question for a developer.
Original Question: {question.get('question')}
Developer Answer: {answer.get('answer')}
Missing Concept: {missing}

RULES:
1. Generate ONE single follow-up question targeting the missing concept.
2. CRITICAL: Keep it EXTREMELY short and simple (under 10-12 words).
3. Do NOT ask multi-part or complex scenario questions.
4. It must be quickly answerable in 15-20 seconds.
5. Return ONLY the question text. No quotes, no markdown, no intros.
"""

    def _call_nous(self, api_key: str, prompt: str) -> str:
        payload = {
            "model": get_model(),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5,
            "max_tokens": 128
        }

        status, body = call_nous_api(api_key, payload, timeout=30)
        if status != 200:
            return ""

        try:
            result = json.loads(body)
            choices = result.get("choices") or []
            if not choices:
                log_llm_error("The model response for a follow-up question had no choices.")
                return ""
            text = (choices[0].get("message") or {}).get("content") or ""
            text = text.strip()
            if not text:
                return ""
            
            # Clean think tags if any
            if "<think>" in text and "</think>" in text:
                text = text.split("</think>")[-1].strip()

            # Remove any surrounding quotes
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            if text.startswith('`') and text.endswith('`'):
                text = text.strip('`')
                
            return text.strip()
        except Exception as exc:
            log_llm_error(f"Could not read the model response for a follow-up question: {exc}")
            return ""

    def _call_groq(self, api_key: str, prompt: str) -> str:
        return self._call_nous(api_key, prompt)
