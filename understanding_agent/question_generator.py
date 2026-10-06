import os
import json
from .api_utils import load_groq_api_key, extract_json, call_groq_api, get_model


import re

STANDARDS_QUESTIONS = {
    "State & Prop Immutability": {
        "question": "How does directly mutating props or state in this change compromise React re-renders and unidirectional data flow?",
        "concepts": ["violates immutability", "causes missed re-renders", "state inconsistency"],
        "criteria": ["understands immutability risks"]
    },
    "React Hooks & Closure Safety": {
        "question": "What is the runtime consequence of passing an empty dependency array to a hook that references outer scope variables?",
        "concepts": ["stale closures", "outdated state/props captured", "closure memory retention"],
        "criteria": ["understands stale closure mechanics"]
    },
    "Resource & Timer Cleanup": {
        "question": "Why must timer intervals or event listeners in useEffect be cleared, and what happens if teardown is omitted?",
        "concepts": ["memory leak", "zombie timers", "unmounted component updates"],
        "criteria": ["understands resource teardown in lifecycle"]
    },
    "Virtual DOM & Reconciliation Safety": {
        "question": "How does direct DOM manipulation or using random list keys disrupt React Virtual DOM reconciliation?",
        "concepts": ["bypasses virtual DOM", "breaks diffing algorithm", "causes unnecessary re-mounts"],
        "criteria": ["understands virtual DOM diffing"]
    },
    "XSS & Script Execution Safety": {
        "question": "What security vulnerability is introduced by dangerouslySetInnerHTML or dynamic code evaluation in this change?",
        "concepts": ["cross-site scripting", "malicious script injection", "untrusted HTML execution"],
        "criteria": ["understands XSS injection risks"]
    },
    "Modern Variable Scoping": {
        "question": "Why is using var or leaking variables to global scope problematic compared to block-scoped const/let?",
        "concepts": ["hoisting pitfalls", "function scope vs block scope", "variable shadowing"],
        "criteria": ["understands modern variable scoping"]
    },
    "Strict Equality & Type Safety": {
        "question": "What edge cases or coercion bugs can arise from loose equality (==) comparisons in this logic?",
        "concepts": ["implicit type coercion", "truthy/falsy confusion", "strict equality avoids coercion"],
        "criteria": ["understands strict equality and type coercion"]
    },
    "Error Handling & Resilience": {
        "question": "What is the risk of having an empty catch block that swallows exceptions silently without logging?",
        "concepts": ["silent failure", "difficult debugging", "unhandled error propagation"],
        "criteria": ["understands exception resilience"]
    },
    "Secret & Credential Protection": {
        "question": "How should sensitive credentials or private tokens be managed instead of hardcoding them into source code?",
        "concepts": ["environment variables", "secrets manager", "credential leakage prevention"],
        "criteria": ["understands secrets management"]
    },
    "Secure Transport Protocols": {
        "question": "Why must endpoints use encrypted HTTPS rather than unencrypted cleartext HTTP?",
        "concepts": ["man-in-the-middle attacks", "eavesdropping", "data integrity and encryption"],
        "criteria": ["understands transport layer security"]
    },
    "Input Validation & Boundary Guards": {
        "question": "What unexpected inputs or boundary values could cause this component to fail without defensive guards?",
        "concepts": ["undefined or null checks", "boundary edge cases", "defensive validation"],
        "criteria": ["understands defensive programming"]
    },
    "Component & Function Modularity": {
        "question": "How would you refactor this logic to improve modularity and single responsibility?",
        "concepts": ["single responsibility", "component decomposition", "reusability"],
        "criteria": ["understands modular design"]
    }
}


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

    def generate(self, context: dict, summary: dict, hints: dict | None = None, env: dict | None = None) -> list:
        std = (hints or {}).get("standards") or []
        failed_std = [s for s in std if not s.get("is_good")]

        api_key = load_groq_api_key()
        if not api_key:
            print("\n  \033[93m⚠️  [LLM] Warning: GROQ_API_KEY is not set.\033[0m")
            print("  \033[2mSet GROQ_API_KEY in your .env or shell (export GROQ_API_KEY=\"gsk_...\") to generate custom questions.\033[0m\n", flush=True)
            questions = self._fallback(context, hints)
            return self._ensure_standards_questions(questions, failed_std)

        prompt = self._build_prompt(context, summary, hints, env=env)
        is_large = context.get("is_large_change", False)
        questions = self._call_groq(api_key, prompt, is_large=is_large)
        if not questions:
            questions = self._fallback(context, hints)
        return self._ensure_standards_questions(questions, failed_std)

    def _build_prompt(self, context: dict, summary: dict, hints: dict | None = None, env: dict | None = None) -> str:
        if context.get("is_large_change", False):
            return self._build_macro_prompt(context, summary, hints, env=env)
        return self._build_micro_prompt(context, summary, hints, env=env)

    def _build_micro_prompt(self, context: dict, summary: dict, hints: dict | None = None, env: dict | None = None) -> str:
        valid_types = ", ".join(f'"{t}"' for t in self.TIME_LIMITS)
        lines = [
            "You are a senior developer reviewing a code change.",
            "Generate between 2 and 3 specific questions to test if the author understands their own change.",
            "CRITICAL: Keep the questions short and simple (under 25 words). Ask one basic thing per question.",
            "Keep it simple but focus on logic and data flow. No generic questions.",
            "",
        ]

        if env:
            lang = env.get("language", "unknown")
            framework = env.get("framework", "unknown")
            lines.append(f"Repository Stack: {framework} ({lang})")
            if framework != "unknown":
                lines.append(f"Tailor questions to idiomatic {framework} and {lang} paradigms (e.g. async/await patterns, lifecycle, component re-renders, state management, dependency injection, service isolation, error handling).")
            lines.append("")

        lines += [
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

        # Domain / security / evidence hints (multi-language & stack-aware)
        if hints:
            lines += self._hint_lines(hints)

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

    def _build_macro_prompt(self, context: dict, summary: dict, hints: dict | None = None, env: dict | None = None) -> str:
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
            "4. CRITICAL: Keep questions VERY SHORT, DIRECT, and SIMPLE (under 20 - 25 words).",
            "5. The question MUST be easily answerable in 30-45 seconds. Do NOT ask compound or essay questions.",
            "",
        ]

        if env:
            lang = env.get("language", "unknown")
            framework = env.get("framework", "unknown")
            lines.append(f"Repository Stack: {framework} ({lang})")
            if framework != "unknown":
                lines.append(f"Tailor architectural questions specifically to {framework} architecture and {lang} paradigms (e.g., component state/lifecycle, hook dependencies, service boundaries, dependency injection, async flow, error boundaries).")
            lines.append("")

        lines += [
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

        # Domain / security / evidence hints (multi-language & stack-aware)
        if hints:
            lines += self._hint_lines(hints)

        valid_types = ", ".join(f'"{t}"' for t in self.TIME_LIMITS)
        lines += [
            "## Output Format",
            "Return ONLY a JSON array with EXACTLY 2 or 3 items. Each item must have:",
            '  "question_id": "q1", "q2", or "q3",',
            '  "question": <very short, punchy question under 25 words>,',
            f'  "type": one of {valid_types},',
            '  "expected_concepts": [<list of key technical concept strings>],',
            '  "evaluation_criteria": [<list of evaluation criteria strings>]',
            "",
            'Example: [{"question_id": "q1", "question": "Why is stock reserved before payment rather than after?", "type": "System Architecture", "expected_concepts": ["prevents overselling", "ensures availability"], "evaluation_criteria": ["understands reservation order"]}]'
        ]
        return "\n".join(lines)

    def _hint_lines(self, hints: dict) -> list:
        """Render stack/security/evidence/practice hints into prompt lines."""
        out = ["\n## Question Guidance (detected stack & findings)"]

        stack = (hints or {}).get("stack") or {}
        if stack.get("frameworks"):
            out.append(f"Detected stack: {', '.join(stack['frameworks'])} ({', '.join(stack.get('languages', [])[:3])}); domains: {', '.join(stack.get('domains', []) or ['general'])}")

        sec = (hints or {}).get("security") or {}
        if sec.get("include_security_question"):
            out.append("MANDATORY: include exactly ONE security question. It must be answerable by the author and target:")
            for ask in (sec.get("ask_about") or [])[:3]:
                out.append(f"  - {ask}")
            if sec.get("top_file"):
                out.append(f"  Focus file: {sec['top_file']}")

        ev = (hints or {}).get("evidence") or {}
        for f in (ev.get("evidence_findings") or [])[:3]:
            out.append(f"Tool finding ({f['source']}): {f['detail']}. Consider asking the author to address it.")
        if ev.get("has_high_severity_evidence"):
            out.append("  Prioritize asking about these tool findings over generic topics.")

        pr = (hints or {}).get("practices") or {}
        if pr.get("prompt_hints"):
            out.append("Domain best-practice topics to draw from (pick the most relevant 1-2):")
            for ph in (pr.get("prompt_hints") or [])[:8]:
                out.append(f"  - {ph}")

        std = (hints or {}).get("standards") or []
        failed_std = [s for s in std if not s.get("is_good")]
        if failed_std:
            out.append("CRITICAL REQUIREMENT - CODING STANDARDS VIOLATIONS:")
            out.append("The author elected to proceed despite failing coding standards audits.")
            out.append("You MUST generate between 1 and 2 questions specifically targeting these flagged violations, asking about their runtime risks, side-effects, or alternatives:")
            for fs in failed_std[:3]:
                out.append(f"  - Violation [{fs['name']}]: {fs['details']}")
            out.append("At least 1 (and up to 2) of your output questions MUST directly address these violation(s).")

        out.append("")
        return out

    def _call_groq(self, api_key: str, prompt: str, is_large: bool = False) -> list:
        """Call Groq API and robustly parse question array from response."""
        payload = {
            "model": get_model(),
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

    def _ensure_standards_questions(self, questions: list, failed_std: list) -> list:
        """Ensure 1-2 questions directly address failed coding standards violations."""
        if not failed_std:
            return questions

        std_keywords = set()
        for fs in failed_std:
            std_keywords.update(re.findall(r'\b[a-zA-Z]{4,}\b', fs.get("name", "").lower()))

        matched_existing = [
            q for q in questions
            if any(kw in q.get("question", "").lower() for kw in std_keywords)
        ]

        needed = max(0, min(2, len(failed_std)) - len(matched_existing))
        if needed > 0:
            injected = []
            for i, fs in enumerate(failed_std[:needed], len(matched_existing) + 1):
                name = fs.get("name", "")
                template = STANDARDS_QUESTIONS.get(name)
                if template:
                    q_text = template["question"]
                    concepts = template["concepts"]
                    criteria = template["criteria"]
                else:
                    details = fs.get("details", "")
                    q_text = f"Coding audit flagged '{name}' ({details[:60]}). What are the runtime risks of this approach?"
                    concepts = ["understands violation risk", "knows safer alternative"]
                    criteria = ["can explain coding standard trade-off"]

                injected.append({
                    "question_id": f"q_std_{i}",
                    "question": q_text,
                    "type": "Invariants",
                    "time_limit": 60,
                    "expected_concepts": concepts,
                    "evaluation_criteria": criteria,
                    "is_fallback": False
                })

            remaining_slots = max(1, 3 - len(injected))
            other_questions = [q for q in questions if q not in matched_existing][:remaining_slots]
            questions = matched_existing + injected + other_questions
            questions = questions[:3]

        for idx, q in enumerate(questions, 1):
            q["question_id"] = f"q{idx}"
        return questions

    def _fallback(self, context: dict | None = None, hints: dict | None = None) -> list:
        context = context or {}
        std = (hints or {}).get("standards") or []
        failed_std = [s for s in std if not s.get("is_good")]
        if failed_std:
            base = []
            for i, fs in enumerate(failed_std[:2], 1):
                name = fs.get("name", "")
                template = STANDARDS_QUESTIONS.get(name)
                if template:
                    q_text = template["question"]
                    concepts = template["concepts"]
                    criteria = template["criteria"]
                else:
                    details = fs.get("details", "")
                    q_text = f"Coding audit flagged '{name}' ({details[:60]}). What are the runtime risks of this implementation?"
                    concepts = ["understands violation risk", "knows safer alternative"]
                    criteria = ["can explain coding standard trade-off"]
                base.append({
                    "question_id": f"q{i}",
                    "question": q_text,
                    "type": "Invariants",
                    "time_limit": 60,
                    "expected_concepts": concepts,
                    "evaluation_criteria": criteria,
                    "is_fallback": True
                })
            if len(base) < 2:
                base.append({
                    "question_id": "q2",
                    "question": "What edge cases did you consider while modifying this component?",
                    "type": "Edge Cases",
                    "time_limit": 60,
                    "expected_concepts": ["handles error states", "validates input"],
                    "evaluation_criteria": ["understands edge cases"],
                    "is_fallback": True
                })
            return base

        sec = (hints or {}).get("security") or {}
        ev = (hints or {}).get("evidence") or {}

        # Security-aware fallback: if the lens flagged risk, ask about it directly
        if sec.get("include_security_question"):
            focus = ", ".join(sec.get("focus_areas", [])[:2]) or "the security implications"
            target = sec.get("top_file") or "this change"
            base = [
                {
                    "question_id": "q1",
                    "question": f"Your change touches {focus} in {target}. What are the security risks and how does your code handle them?",
                    "type": "Invariants",
                    "time_limit": 60,
                    "expected_concepts": sec.get("focus_areas", []),
                    "evaluation_criteria": ["understands security implications of the change"],
                    "is_fallback": True
                },
            ]
            # Evidence finding as a second question when present
            findings = ev.get("evidence_findings") or []
            if findings:
                f0 = findings[0]
                base.append({
                    "question_id": "q2",
                    "question": f"A scan flagged: {f0['detail']}. Can you explain and address this finding?",
                    "type": "Edge Cases",
                    "time_limit": 60,
                    "expected_concepts": ["understands the flagged finding", "knows the mitigation"],
                    "evaluation_criteria": ["can address tool findings"],
                    "is_fallback": True
                })
            else:
                base.append({
                    "question_id": "q2",
                    "question": "What input or failure case could make this code behave incorrectly, and what happens then?",
                    "type": "Edge Cases",
                    "time_limit": 60,
                    "expected_concepts": ["handles error states", "validates input"],
                    "evaluation_criteria": ["understands edge cases"],
                    "is_fallback": True
                })
            return base

        if context.get("is_large_change", False):
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
