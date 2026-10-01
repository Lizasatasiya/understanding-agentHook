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
        "System Architecture":     60,
        "Invariants":              60,
    }
    _VALID_TYPES = set(TIME_LIMITS.keys())

    def generate(self, context: dict, summary: dict) -> list:
        api_key = load_groq_api_key()
        if not api_key:
            print("\n  \033[93m⚠️  [LLM] Warning: GROQ_API_KEY is not set.\033[0m")
            print("  \033[2mSet GROQ_API_KEY in your .env or shell (export GROQ_API_KEY=\"gsk_...\") to generate custom questions.\033[0m\n", flush=True)
            return self._fallback(context)

        prompt = self._build_prompt(context, summary)
        is_large = context.get("is_large_change", False)
        questions = self._call_groq(api_key, prompt, is_large=is_large)
        if questions:
            return questions
        return self._fallback(context)

    def _build_prompt(self, context: dict, summary: dict) -> str:
        if context.get("is_large_change", False):
            return self._build_macro_prompt(context, summary)

        valid_types = ", ".join(f'"{t}"' for t in self.TIME_LIMITS)
        lines = [
            "You are a senior developer reviewing a code change.",
            "Generate between 2 and 3 specific questions to test if the author understands their own change.",
            "CRITICAL: Keep the questions EXTREMELY short and simple (under 15 words). Ask one basic thing per question.",
            "Keep it simple but focus on logic and data flow. No generic questions.",
            "",
            "## Changed Functions",
        ]

        changes = context.get("structured_changes", [])
        if len(changes) > 4:
            changes = sorted(
                changes,
                key=lambda c: (
                    len(c.get('dependency_summary', '')),
                    len(c.get('diff', ''))
                ),
                reverse=True
            )[:4]

        for f in changes:
            f_sum = f.get('summary', {})
            lines.append(f"File: {f.get('file', '?')} | Function: {f.get('function', '?')}()")
            lines.append(f"Summary: {f_sum.get('what_changed', 'N/A')} Impact: {f_sum.get('impact', 'N/A')}")
            lines.append("Diff:")
            lines.append(f"```diff\n{f.get('diff', '')}\n```")
            lines.append("Dependencies invoked by this function:")
            lines.append(f"{f.get('dependency_summary', '')}")
            lines.append("")

        lines += [
            "",
            "## Output Format",
            "Return ONLY a JSON array with 2 or 3 items. Each item must be an object with:",
            '  "question_id": <a unique string like "q1", "q2">,',
            '  "question": <the question string>,',
            f'  "type": one of {valid_types},',
            '  "expected_concepts": [<list of concept strings>],',
            '  "evaluation_criteria": [<list of criteria strings>]',
            "",
            'Example: [{"question_id": "q1", "question": "Why is X called before Y?", "type": "Code Logic", "expected_concepts": ["X must be validated before Y executes"], "evaluation_criteria": ["understands the validation order"]}]',
        ]
        return "\n".join(lines)

    def _build_macro_prompt(self, context: dict, summary: dict) -> str:
        """
        Build an architectural prompt for substantial commits (>80-100 lines or multi-file).
        Focuses on data flow, component interactions, failure modes, and system invariants.
        Strictly limits to 2 or 3 high-impact questions.
        """
        stats = context.get("stats", {})
        lines = [
            "You are a principal engineer conducting an architectural code understanding check on a substantial commit.",
            f"Commit Scale: +{stats.get('total_added', 0)} / -{stats.get('total_deleted', 0)} lines across {len(context.get('file_summary', []))} file(s).",
            "",
            "CRITICAL RULES FOR LARGE COMMITS:",
            "1. Generate EXACTLY 2 or 3 questions. NEVER more than 3.",
            "2. Do NOT ask trivia about single lines of code, variable renames, or syntax minutiae.",
            "3. Focus on core engineering logic: data flow, failure modes, or persistence.",
            "4. CRITICAL: Keep questions VERY SHORT, DIRECT, and SIMPLE (under 12-15 words).",
            "5. The question MUST be easily answerable in 30-45 seconds. Do NOT ask compound or essay questions.",
            "",
            "## Architectural Summary",
            f"What Changed: {summary.get('what_changed', 'N/A')}",
            f"Impact: {summary.get('impact', 'N/A')}",
            f"Key Risks: {summary.get('key_risks', 'N/A')}",
            "",
            "## Changed Components",
        ]

        for file_info in context.get("file_summary", []):
            funcs = ", ".join(file_info.get("functions", [])) or "module definitions"
            lines.append(f"- {file_info['file']}: {funcs}")

        lines.append("\n## Key Diffs (Skeleton/Highlights):")
        for f in context.get("structured_changes", [])[:4]:
            lines.append(f"File: {f.get('file')} | Function: {f.get('function')}()")
            lines.append(f"```diff\n{f.get('diff', '')}\n```")
            dep_summary = f.get('dependency_summary', '')
            if dep_summary and dep_summary != "No external dependencies called.":
                lines.append(f"Dependencies: {dep_summary}")
            lines.append("")

        valid_types = ", ".join(f'"{t}"' for t in self.TIME_LIMITS)
        lines += [
            "## Output Format",
            "Return ONLY a JSON array with EXACTLY 2 or 3 items. Each item must have:",
            '  "question_id": "q1", "q2", or "q3",',
            '  "question": <very short, punchy question under 15 words>,',
            f'  "type": one of {valid_types},',
            '  "expected_concepts": [<list of key technical concept strings>],',
            '  "evaluation_criteria": [<list of evaluation criteria strings>]',
            "",
            'Example: [{"question_id": "q1", "question": "Why is stock reserved before payment rather than after?", "type": "System Architecture", "expected_concepts": ["prevents overselling", "ensures availability"], "evaluation_criteria": ["understands reservation order"]}]'
        ]
        return "\n".join(lines)

    def _call_groq(self, api_key: str, prompt: str, is_large: bool = False) -> list:
        """Call Groq API and robustly parse question array from response."""
        payload = {
            "model": "qwen/qwen3.8-27b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 800
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
                                "type": "System Architecture" if is_large else "Code Logic"
                            })
                if parsed_lines:
                    raw = parsed_lines

            if not isinstance(raw, list) or not raw:
                return []

            max_q = 3 if is_large else 3
            raw = raw[:max_q]

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
        if (context or {}).get("is_large_change", False):
            files = [f.get("file") for f in (context or {}).get("file_summary", [])]
            scope = f"across {len(files)} files" if files else "in this commit"
            return [
                {
                    "question_id": "q1",
                    "question": f"How do the modified components {scope} coordinate to ensure data consistency?",
                    "type": "System Architecture",
                    "time_limit": 60,
                    "expected_concepts": ["component coordination", "data flow consistency", "state integrity"],
                    "evaluation_criteria": ["understands cross-component coordination"],
                    "is_fallback": True
                },
                {
                    "question_id": "q2",
                    "question": "What error states or edge cases could cause failures across this multi-part change?",
                    "type": "Invariants",
                    "time_limit": 60,
                    "expected_concepts": ["handles failure states", "avoids inconsistent state", "error recovery"],
                    "evaluation_criteria": ["understands system invariants and failure handling"],
                    "is_fallback": True
                }
            ]

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
