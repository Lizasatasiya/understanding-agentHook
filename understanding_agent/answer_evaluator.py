from .evaluation_models import EvaluationResult
from .api_utils import load_groq_api_key, extract_json, call_groq_api
import json

class AnswerEvaluator:
    def evaluate(self, question: dict, answer: dict, context: dict, summary: dict) -> EvaluationResult:
        api_key = load_groq_api_key()
        if not api_key:
            return EvaluationResult({
                "score": 75, 
                "evaluation": "Offline evaluation (no GROQ_API_KEY configured).",
                "follow_up_required": False,
                "missing_concepts": []
            })
        
        prompt = self._build_prompt(question, answer, context, summary)
        result_dict = self._call_groq(api_key, prompt)
        if result_dict:
            return EvaluationResult(result_dict)
            
        return EvaluationResult({"score": 50, "evaluation": "Failed to evaluate answer", "follow_up_required": False})

    def _build_prompt(self, question: dict, answer: dict, context: dict, summary: dict) -> str:
        lines = [
            "You are evaluating whether a developer understands their own code change.",
            "Use ONLY the supplied repository evidence.",
            "Evaluate the developer's explanation against:",
            "- the actual changed code",
            "- dependencies",
            "- expected concepts",
            "- evaluation criteria",
            "",
            "Do not reward generic explanations.",
            "Do not assume an answer is correct simply because it sounds technically plausible.",
            "Do not require the developer to use the same wording as the expected concepts.",
            "",
            "## Code Context"
        ]
        
        changes = context.get("structured_changes", [])
        for f in changes[:5]:
            lines.append(f"File: {f.get('file', '?')} | Function: {f.get('function', '?')}()")
            lines.append("Diff:")
            lines.append(f"```diff\n{f.get('diff', '')}\n```")
            lines.append("Dependencies:")
            lines.append(f"{f.get('dependency_summary', '')}\n")
            
        lines += [
            "## Question Details",
            f"Question: {question.get('question')}",
            f"Expected Concepts: {', '.join(question.get('expected_concepts', []))}",
            f"Evaluation Criteria: {', '.join(question.get('evaluation_criteria', []))}",
            "",
            "## Developer Answer",
            f"Answer: {answer.get('answer')}",
            "",
            "## Task",
            "Output a JSON object with:",
            " - 'score' (0-100 integer)",
            " - 'technical_correctness' (0-40 integer)",
            " - 'code_understanding' (0-30 integer)",
            " - 'reasoning' (0-20 integer)",
            " - 'specificity' (0-10 integer)",
            " - 'covered_concepts' (list of strings)",
            " - 'missing_concepts' (list of strings)",
            " - 'incorrect_claims' (list of strings)",
            " - 'evaluation' (string)",
            " - 'confidence' (0.0 - 1.0 float)",
            " - 'follow_up_required' (boolean)",
            "",
            "If there are any 'incorrect_claims', drastically lower the score."
        ]
        return "\n".join(lines)

    def _call_groq(self, api_key: str, prompt: str) -> dict:
        payload = {
            "model": "qwen/qwen3.8-27b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1024
        }

        status, body = call_groq_api(api_key, payload, timeout=30)
        if status != 200:
            return {}

        try:
            result = json.loads(body)
            text = result["choices"][0]["message"]["content"].strip()
            parsed = extract_json(text)
            if isinstance(parsed, dict) and "score" in parsed:
                return parsed
            return {}
        except Exception:
            return {}
