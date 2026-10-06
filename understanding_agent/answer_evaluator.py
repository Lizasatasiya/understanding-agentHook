from .evaluation_models import EvaluationResult
from .api_utils import load_groq_api_key, extract_json, call_groq_api, get_model, fail_open_enabled
import json

class AnswerEvaluator:
    def evaluate(self, question: dict, answer: dict, context: dict, summary: dict) -> EvaluationResult:
        # Empty or timed-out answers need no LLM evaluation: they are wrong by definition
        if not (answer.get("answer") or "").strip():
            return EvaluationResult({
                "score": 0,
                "evaluation": "No answer provided (empty or timed out).",
                "follow_up_required": False,
                "missing_concepts": []
            })

        api_key = load_groq_api_key()
        if not api_key:
            # No key configured: fail open rather than fail the whole commit pipeline
            if fail_open_enabled():
                return EvaluationResult({
                    "score": 75,
                    "evaluation": "Offline evaluation (no GROQ_API_KEY configured).",
                    "follow_up_required": False,
                    "missing_concepts": []
                })
            return EvaluationResult({
                "score": 0,
                "evaluation": "Cannot verify understanding: GROQ_API_KEY is not configured and UNDERSTANDING_AGENT_FAIL_MODE=closed.",
                "follow_up_required": False,
                "missing_concepts": []
            })

        prompt = self._build_prompt(question, answer, context, summary)
        result_dict = self._call_groq(api_key, prompt)
        if result_dict:
            return EvaluationResult(result_dict)

        # LLM evaluation failed (API error, unparseable response, or timeout).
        # Fail open with a warning so a third-party outage never blocks commits,
        # unless UNDERSTANDING_AGENT_FAIL_MODE=closed is explicitly set.
        if fail_open_enabled():
            return EvaluationResult({
                "score": 75,
                "evaluation": "LLM evaluation unavailable (API error or timeout); failing open.",
                "follow_up_required": False,
                "missing_concepts": []
            })
        return EvaluationResult({"score": 50, "evaluation": "Failed to evaluate answer", "follow_up_required": False})

    def _build_prompt(self, question: dict, answer: dict, context: dict, summary: dict) -> str:
        is_large = context.get("is_large_change", False)
        lines = [
            "You are evaluating whether a developer understands their own code change.",
            "",
            "CRITICAL EVALUATION GUIDELINES:",
            "- Focus ONLY on the core LOGIC and INTENT of the developer's answer.",
            "- High-level, short, or general logical answers ARE FULLY ACCEPTABLE if the core engineering sense is right (e.g., 'data lost on server restart', 'need a db lock to avoid race conditions', 'use a database for multiple servers').",
            "- Do NOT require textbook definitions or long essays. Developers are typing or speaking via mic in a live terminal; brief 1-2 sentence answers are completely valid.",
            "- Be tolerant of speech-to-text transcription artifacts and minor voice mishearings.",
            "- If the developer shows the right basic intuition, award a PASSING score (75% to 95%).",
            "- If the answer is completely wrong, irrelevant, nonsensical, blank, or dismissive (e.g., 'ok', 'idk', 'dunno', random words), assign a score strictly below 25%.",
            "",
            "Evaluate against:",
            "- changed code and components",
            "- architectural intent and dependencies",
            "- expected concepts"
        ]

        if is_large:
            arch_sum = context.get("summary", summary or {})
            lines.append("\n## Architectural Summary")
            lines.append(f"What Changed: {arch_sum.get('what_changed', 'N/A')}")
            lines.append(f"Impact: {arch_sum.get('impact', 'N/A')}")
            if arch_sum.get("key_risks"):
                lines.append(f"Key Risks: {arch_sum.get('key_risks')}")
            
            lines.append("\n## Changed Files & Components")
            for item in context.get("file_summary", []):
                funcs = ", ".join(item.get("functions", [])) or "module-level changes"
                lines.append(f"- {item.get('file')}: {funcs}")

        lines.append("\n## Code Context")
        changes = context.get("structured_changes", [])
        for f in changes[:4]:
            lines.append(f"File: {f.get('file', '?')} | Function: {f.get('function', '?')}()")
            lines.append("Diff:")
            lines.append(f"```diff\n{f.get('diff', '')}\n```")
            dep = f.get('dependency_summary', '')
            if dep and dep != "No external dependencies called.":
                lines.append(f"Dependencies: {dep}\n")
            
        lines += [
            "## Question Details",
            f"Question: {question.get('question')}",
            f"Question Type: {question.get('type', 'Reasoning')}",
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
            "",
            "If there are any 'incorrect_claims', drastically lower the score."
        ]
        return "\n".join(lines)

    def _call_groq(self, api_key: str, prompt: str) -> dict:
        payload = {
            "model": get_model(),
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
