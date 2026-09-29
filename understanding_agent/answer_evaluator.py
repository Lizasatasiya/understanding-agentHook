import os
import json
import http.client
import ssl
from .evaluation_models import EvaluationResult

class AnswerEvaluator:
    def evaluate(self, question: dict, answer: dict, context: dict, summary: dict) -> EvaluationResult:
        api_key = self._load_api_key()
        if not api_key:
            return EvaluationResult({
                "score": 50, 
                "evaluation": "Mock evaluation (no API key)",
                "follow_up_required": True,
                "missing_concepts": ["Mock missing concept"]
            })
        
        prompt = self._build_prompt(question, answer, context, summary)
        result_dict = self._call_groq(api_key, prompt)
        if result_dict:
            return EvaluationResult(result_dict)
            
        return EvaluationResult({"score": 50, "evaluation": "Failed to evaluate", "follow_up_required": False})

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
        payload = json.dumps({
            "model": "qwen/qwen3.8-27b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1024
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

            return json.loads(text)

        except Exception:
            return {}
