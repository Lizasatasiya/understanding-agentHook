import os
import json
from .api_utils import load_groq_api_key, extract_json, call_groq_api


import re

class QuestionGenerator:
    # Time limits (seconds) keyed by question type
    TIME_LIMITS = {
        "Code Logic":              30,
        "Data Flow":               45,
        "Dependencies":            45,
        "Reasoning":               60,
        "Edge Cases":              60,
        "Change Impact":           60,
        "What-if":                 60,
    }
    _VALID_TYPES = set(TIME_LIMITS.keys())

    def generate(self, context: dict, summary: dict) -> list:
        api_key = load_groq_api_key()
        if not api_key:
            print("\n  \033[93m⚠️  [LLM] Warning: GROQ_API_KEY is not set.\033[0m")
            print("  \033[2mSet GROQ_API_KEY in your .env or shell (export GROQ_API_KEY=\"gsk_...\") to generate custom questions.\033[0m\n", flush=True)
            return self._fallback(context)

        prompt = self._build_prompt(context, summary)
        questions = self._call_groq(api_key, prompt)
        if questions:
            return questions
        return self._fallback(context)

    def _build_prompt(self, context: dict, summary: dict) -> str:
        """
        Build a focused, concise prompt using only:
        - Change summary (what, why, impact)
        - Changed functions and their new calls
        - Key dependencies (what calls what, where it's defined)
        """
        valid_types = ", ".join(f'"{t}"' for t in self.TIME_LIMITS)
        lines = [
            "You are a senior developer reviewing a code change.",
            "Generate between 2 and 7 specific questions to test if the author understands their own change.",
            "The number of questions should depend on how complex the change is. If the change is small, 2 or 3 is enough.",
            "CRITICAL: Keep the questions EXTREMELY short and simple (under 15 words). Ask one basic thing per question.",
            "Keep it simple but focus on logic and data flow. No generic questions.",
            "",
            "## Changed Functions",
        ]

        # Cap to 5 most significant changes to avoid overwhelming the LLM
        changes = context.get("structured_changes", [])
        if len(changes) > 5:
            changes = sorted(
                changes,
                key=lambda c: (
                    len(c.get('dependency_summary', '')),
                    len(c.get('diff', ''))
                ),
                reverse=True
            )[:5]
            lines.append(f"(Showing 5 most significant changes out of {len(context.get('structured_changes', []))} total)\n")

        for f in changes:
            summary = f.get('summary', {})
            lines.append(f"File: {f.get('file', '?')} | Function: {f.get('function', '?')}()")
            lines.append(f"Summary: {summary.get('what_changed', 'N/A')} Impact: {summary.get('impact', 'N/A')}")
            lines.append("Diff:")
            lines.append(f"```diff\n{f.get('diff', '')}\n```")
            lines.append("Dependencies invoked by this function:")
            lines.append(f"{f.get('dependency_summary', '')}")
            lines.append("")

        lines += [
            "",
            "## Output Format",
            "Return ONLY a JSON array (between 2 and 7 items). Each item must be an object with:",
            '  "question_id": <a unique string like "q1", "q2">,',
            '  "question": <the question string>,',
            f'  "type": one of {valid_types},',
            '  "expected_concepts": [<list of concept strings>],',
            '  "evaluation_criteria": [<list of criteria strings>]',
            "",
            'Example: [{"question_id": "q1", "question": "Why is X called before Y?", "type": "Code Logic", "expected_concepts": ["X must be validated before Y executes"], "evaluation_criteria": ["understands the validation order"]}]',
        ]
        return "\n".join(lines)

    def _call_groq(self, api_key: str, prompt: str) -> list:
        """Call Groq API and robustly parse question array from response."""
        payload = {
            "model": "qwen/qwen3.8-27b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5,
            "max_tokens": 1024
        }

        status, body = call_groq_api(api_key, payload, timeout=30)
        if status != 200:
            if status != 0:
                print(f"\n  \033[93m[⚠️  Groq API returned HTTP {status}: {body[:200]}]\033[0m\n", flush=True)
            return []

        try:
            result = json.loads(body)
            text = result["choices"][0]["message"]["content"].strip()
            raw = extract_json(text)

            # Handle dicts: {"questions": [...]}, {"items": [...]}, or dict-of-dicts
            if isinstance(raw, dict):
                for key in ("questions", "items", "data", "quiz", "result", "output"):
                    if key in raw and isinstance(raw[key], list):
                        raw = raw[key]
                        break
                else:
                    dict_vals = [v for v in raw.values() if isinstance(v, dict) and "question" in v]
                    if dict_vals:
                        raw = dict_vals

            # If JSON extraction didn't yield a list, try extracting numbered question lines
            if not isinstance(raw, list) or not raw:
                parsed_lines = []
                cleaned_text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
                for line in cleaned_text.splitlines():
                    clean_line = line.strip()
                    m = re.match(r"^(?:(?:\d+[\.\)]|Q\d+[:\.]?|-|\*)\s*)(.+)$", clean_line)
                    if m:
                        candidate_q = m.group(1).strip().strip('"').strip("'")
                        if "?" in candidate_q and len(candidate_q) > 15:
                            parsed_lines.append({
                                "question": candidate_q,
                                "type": "Code Logic"
                            })
                if parsed_lines:
                    raw = parsed_lines

            if not isinstance(raw, list) or not raw:
                return []

            raw = raw[:7]

            result_list = []
            for i, item in enumerate(raw, 1):
                if isinstance(item, str):
                    item = {"question": item, "type": "Reasoning"}
                q_type = item.get("type", "Reasoning")
                if q_type not in self._VALID_TYPES:
                    q_type = "Reasoning"
                item["type"] = q_type
                item["time_limit"] = self.TIME_LIMITS[q_type]
                
                if "question_id" not in item:
                    item["question_id"] = f"q{i}"
                if "expected_concepts" not in item or not isinstance(item["expected_concepts"], list):
                    item["expected_concepts"] = []
                if "evaluation_criteria" not in item or not isinstance(item["evaluation_criteria"], list):
                    item["evaluation_criteria"] = []
                item["is_fallback"] = False
                    
                result_list.append(item)
            return result_list

        except Exception as e:
            return []

    def _fallback(self, context: dict = None) -> list:
        # If we have structured changes, tailor questions to actual functions/files
        changes = (context or {}).get("structured_changes", [])
        if changes:
            target = changes[0]
            func = target.get("function")
            file = target.get("file", "changed file")
            target_desc = f"{func}() in {file}" if func else file
            
            return [
                {
                    "question_id": "q1",
                    "question": f"What behavior does the change in {target_desc} introduce?",
                    "type": "Change Impact",
                    "time_limit": 60,
                    "expected_concepts": ["modifies behavior", "implements requirements"],
                    "evaluation_criteria": ["understands impact"],
                    "is_fallback": True
                },
                {
                    "question_id": "q2",
                    "question": f"What edge cases did you consider while updating {target_desc}?",
                    "type": "Edge Cases",
                    "time_limit": 60,
                    "expected_concepts": ["handles error states", "validates input"],
                    "evaluation_criteria": ["understands edge cases"],
                    "is_fallback": True
                }
            ]

        return [
            {
                "question_id": "q1",
                "question": "What behavior did your change introduce?",
                "type": "Change Impact",
                "time_limit": 60,
                "expected_concepts": ["change introduces new behavior"],
                "evaluation_criteria": ["understands impact"],
                "is_fallback": True
            },
            {
                "question_id": "q2",
                "question": "Why is the new function called before the main logic executes?",
                "type": "Code Logic",
                "time_limit": 30,
                "expected_concepts": ["validation happens before execution"],
                "evaluation_criteria": ["understands logic flow"],
                "is_fallback": True
            },
            {
                "question_id": "q3",
                "question": "What should happen when the new check fails?",
                "type": "Edge Cases",
                "time_limit": 60,
                "expected_concepts": ["system should gracefully handle failure"],
                "evaluation_criteria": ["understands failure cases"],
                "is_fallback": True
            },
        ]


    def validate(self, questions: list) -> list:
        """Accept question dicts; skip malformed entries."""
        valid = []
        for q in questions:
            if isinstance(q, dict) and isinstance(q.get("question"), str) and len(q["question"]) > 10:
                valid.append(q)
        return valid
