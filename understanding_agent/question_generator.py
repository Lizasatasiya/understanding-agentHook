import os
import json
from .api_utils import load_nous_api_key, extract_json, call_nous_api, get_model, load_groq_api_key, call_groq_api


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

    def get_diff_scale(self, context: dict | None) -> tuple[str, int]:
        """
        Classify diff length and return (scale, target_question_count):
        - small change: 2-3 questions (< 100 LOC)
        - medium change: 3-5 questions (100-250 LOC)
        - large change: more than 5 and less than 10 questions (> 250 LOC)
        """
        context = context or {}
        stats = context.get("stats") or {}
        total_loc = stats.get("total_loc")

        if total_loc is None:
            total_loc = 0
            for sc in context.get("structured_changes", []):
                diff = sc.get("diff", "")
                for line in diff.splitlines():
                    if line.startswith("+") or line.startswith("-"):
                        total_loc += 1

        if total_loc < 100:
            scale = "small"
            target_count = 2 if total_loc <= 30 else 3
        elif total_loc <= 250:
            scale = "medium"
            if total_loc <= 150:
                target_count = 3
            elif total_loc <= 200:
                target_count = 4
            else:
                target_count = 5
        else:
            scale = "large"
            # strictly more than 5 and less than 10 (e.g. 6, 7, 8)
            if total_loc <= 350:
                target_count = 6
            elif total_loc <= 500:
                target_count = 7
            else:
                target_count = 8

        return scale, target_count

    def get_standards_and_diff_counts(self, target_count: int, failed_std: list) -> tuple[int, int]:
        """
        Ensure 80% of questions are based on current diff, and at most 20% on coding standards violations.
        """
        if not failed_std:
            return 0, target_count

        max_standards = max(0, int(round(target_count * 0.20)))
        if max_standards == 0 and len(failed_std) > 0 and target_count >= 3:
            max_standards = 1

        standards_count = min(len(failed_std), max_standards)
        diff_count = target_count - standards_count
        return standards_count, diff_count

    def generate(self, context: dict, summary: dict, hints: dict | None = None, env: dict | None = None) -> list:
        std = (hints or {}).get("standards") or []
        failed_std = [s for s in std if not s.get("is_good")]
        scale, target_count = self.get_diff_scale(context)

        api_key = load_nous_api_key()
        if not api_key:
            print("\n  \033[93m[LLM] Warning: NOUS_API_KEY is not set.\033[0m")
            print("  \033[2mSet NOUS_API_KEY in your .env or shell (export NOUS_API_KEY=\"...\") to generate custom questions.\033[0m\n", flush=True)
            questions = self._fallback(context, hints)
            return self._ensure_standards_questions(questions, failed_std, context, api_key=None, hints=hints)

        prompt = self._build_prompt(context, summary, hints, env=env)
        is_large = (scale == "large")
        questions = self._call_groq(api_key, prompt, target_count=target_count, is_large=is_large)
        if not questions:
            questions = self._fallback(context, hints)
        return self._ensure_standards_questions(questions, failed_std, context, api_key=api_key, hints=hints)

    def _build_prompt(self, context: dict, summary: dict, hints: dict | None = None, env: dict | None = None) -> str:
        scale, _ = self.get_diff_scale(context)
        if scale == "large":
            return self._build_macro_prompt(context, summary, hints, env=env)
        return self._build_micro_prompt(context, summary, hints, env=env)

    def _build_micro_prompt(self, context: dict, summary: dict, hints: dict | None = None, env: dict | None = None) -> str:
        valid_types = ", ".join(f'"{t}"' for t in self.TIME_LIMITS)
        scale, target_count = self.get_diff_scale(context)
        std = (hints or {}).get("standards") or []
        failed_std = [s for s in std if not s.get("is_good")]
        standards_count, diff_count = self.get_standards_and_diff_counts(target_count, failed_std)

        lines = [
            "You are a senior developer reviewing a code change.",
            f"Generate EXACTLY {target_count} specific questions to test if the author understands their own change.",
            "CRITICAL RATIO RULES:",
            f"- At least {diff_count} question(s) (~80%) MUST be based directly on the CURRENT CODE DIFF: logic, data flow, function interactions, state mutations, and edge cases.",
        ]
        if standards_count > 0:
            std_summary = ", ".join(f"{re.sub(r'\(changed\)', '', s['name']).strip()}: {s.get('details', '')}" for s in failed_std[:standards_count])
            lines.append(
                f"- Exactly {standards_count} question MUST address the flagged coding standard: {std_summary}. "
                "Ask a natural, thoughtful code-review question in the context of the changed function (e.g. asking how to refactor with early returns/helpers or why this pattern was used). "
                "DO NOT use robotic prefixes like 'Coding audit flagged' or '(changed)'."
            )
        else:
            lines.append("- 100% of questions MUST be based on the current code diff.")
        lines += [
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
        if len(changes) > 6:
            changes = sorted(
                changes,
                key=lambda c: (
                    len(c.get('dependency_summary', '')),
                    len(c.get('diff', ''))
                ),
                reverse=True
            )[:6]

        for f in changes:
            f_sum = f.get('summary', {})
            lines.append(f"File: {f.get('file', '?')} | Function: {f.get('function', '?')}()")
            lines.append(f"Summary: {f_sum.get('what_changed', 'N/A')} Impact: {f_sum.get('impact', 'N/A')}")
            lines.append("Diff:")
            lines.append(f"```diff\n{f.get('diff', '')}\n```")
            lines.append("Dependencies invoked by this function:")
            lines.append(f"{f.get('dependency_summary', '')}")
            lines.append("")

        if hints:
            lines += self._hint_lines(hints, target_count=target_count)

        lines += [
            "",
            "## Output Format",
            f"Return ONLY a JSON array with EXACTLY {target_count} items. Each item must be an object with:",
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
        stats = context.get("stats", {})
        scale, target_count = self.get_diff_scale(context)
        std = (hints or {}).get("standards") or []
        failed_std = [s for s in std if not s.get("is_good")]
        standards_count, diff_count = self.get_standards_and_diff_counts(target_count, failed_std)

        lines = [
            "You are a principal engineer conducting an architectural code understanding check on a substantial commit.",
            f"Commit Scale: {scale.upper()} (+{stats.get('total_added', 0)} / -{stats.get('total_deleted', 0)} lines across {len(context.get('file_summary', []))} file(s)).",
            "",
            "CRITICAL RULES FOR COMMITS:",
            f"1. Generate EXACTLY {target_count} questions.",
            f"2. RATIO RULE: At least {diff_count} questions (~80%) MUST be based directly on the actual DIFF (data flow, failure modes, component interactions).",
        ]
        if standards_count > 0:
            std_summary = ", ".join(f"{re.sub(r'\(changed\)', '', s['name']).strip()}: {s.get('details', '')}" for s in failed_std[:standards_count])
            lines.append(
                f"3. Exactly {standards_count} question MUST address the flagged coding standard: {std_summary}. "
                "Ask a natural, thoughtful code-review question in the context of the changed component/architecture (e.g. asking how to refactor or why this pattern was chosen). "
                "DO NOT use robotic prefixes like 'Coding audit flagged' or '(changed)'."
            )
        else:
            lines.append("3. 100% of questions must focus on the actual diff.")
        lines += [
            "4. Do NOT ask trivia about single lines of code, variable renames, or syntax minutiae.",
            "5. Focus on core engineering logic: data flow, failure modes, or persistence.",
            "6. CRITICAL: Keep questions VERY SHORT, DIRECT, and SIMPLE (under 20 - 25 words).",
            "7. The question MUST be easily answerable in 30-45 seconds. Do NOT ask compound or essay questions.",
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
        for f in context.get("structured_changes", [])[:6]:
            lines.append(f"File: {f.get('file')} | Function: {f.get('function')}()")
            lines.append(f"```diff\n{f.get('diff', '')}\n```")
            dep_summary = f.get('dependency_summary', '')
            if dep_summary and dep_summary != "No external dependencies called.":
                lines.append(f"Dependencies: {dep_summary}")
            lines.append("")

        if hints:
            lines += self._hint_lines(hints, target_count=target_count)

        valid_types = ", ".join(f'"{t}"' for t in self.TIME_LIMITS)
        lines += [
            "## Output Format",
            f"Return ONLY a JSON array with EXACTLY {target_count} items. Each item must have:",
            '  "question_id": <unique string like "q1", "q2">,',
            '  "question": <very short, punchy question under 25 words>,',
            f'  "type": one of {valid_types},',
            '  "expected_concepts": [<list of key technical concept strings>],',
            '  "evaluation_criteria": [<list of evaluation criteria strings>]',
            "",
            'Example: [{"question_id": "q1", "question": "Why is stock reserved before payment rather than after?", "type": "System Architecture", "expected_concepts": ["prevents overselling", "ensures availability"], "evaluation_criteria": ["understands reservation order"]}]'
        ]
        return "\n".join(lines)

    def _hint_lines(self, hints: dict, target_count: int = 3) -> list:
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
            standards_count, diff_count = self.get_standards_and_diff_counts(target_count, failed_std)
            if standards_count > 0:
                out.append(f"CODING STANDARDS VIOLATIONS (EXACTLY {standards_count} QUESTION(S), ~20%):")
                out.append("The author elected to proceed despite failing coding standards audits.")
                for fs in failed_std[:standards_count]:
                    clean_name = re.sub(r'\(changed\)', '', fs['name']).strip()
                    out.append(f"  - Flagged Standard: [{clean_name}]: {fs.get('details', '')}")
                out.append("Formulate a natural, thoughtful code-review question in the context of the changed code without robotic prefixes.")
                out.append(f"The remaining {diff_count} questions (~80%) MUST directly address the diff.")
            else:
                out.append("Do not generate standards questions; focus 100% on the diff.")

        out.append("")
        return out

    def _call_nous(self, api_key: str, prompt: str, target_count: int = 3, is_large: bool = False) -> list:
        """Call Nous Research API and robustly parse question array from response."""
        payload = {
            "model": get_model(),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 1200
        }

        status, body = call_nous_api(api_key, payload, timeout=30)
        if status != 200:
            if status != 0:
                print(f"\n  \033[93m[Nous API returned HTTP {status}: {body[:200]}]\033[0m\n", flush=True)
            return []

        try:
            result = json.loads(body)
            choices = result.get("choices") or []
            if not choices:
                return []
            msg = choices[0].get("message") or {}
            text = (msg.get("content") or "").strip()
            if not text and msg.get("reasoning"):
                text = (msg.get("reasoning") or "").strip()
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

            raw = raw[:target_count]

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
        except Exception:
            return []

    def _call_groq(self, api_key: str, prompt: str, target_count: int = 3, is_large: bool = False) -> list:
        return self._call_nous(api_key, prompt, target_count=target_count, is_large=is_large)

    def _build_proper_standard_question(self, fs: dict, context: dict | None = None) -> dict:
        name = fs.get("name", "")
        clean_name = re.sub(r'\(changed\)', '', name).strip()
        details = fs.get("details", "")

        # Target function or file
        target = ""
        fn_match = re.search(r'([a-zA-Z0-9_$]+)\(\)', details)
        if fn_match:
            target = f"{fn_match.group(1)}()"
        else:
            file_match = re.search(r'([\w/.-]+\.(?:jsx?|tsx?|py|go|rs|java|vue|svelte))', details)
            if file_match:
                target = os.path.basename(file_match.group(1))

        if not target and context:
            changes = context.get("structured_changes", [])
            if changes and changes[0].get("function"):
                target = f"{changes[0]['function']}()"
            elif changes and changes[0].get("file"):
                target = os.path.basename(changes[0]["file"])

        target_phrase = f"In {target}, " if target else ""
        name_lower = clean_name.lower()

        if "nesting" in name_lower or "depth" in name_lower:
            q_text = f"{target_phrase}how could the control flow be refactored to reduce nesting depth using early returns or helper functions?"
            concepts = ["reduce nesting depth", "early returns / guard clauses", "extract helper functions", "flatten control flow"]
            criteria = ["understands flattening control flow and reducing nesting"]
            q_type = "Code Logic"
        elif "length" in name_lower:
            q_text = f"{target_phrase}the function exceeds standard length guidelines. How would you decompose it into smaller, focused units?"
            concepts = ["modular decomposition", "single responsibility", "extract helper functions"]
            criteria = ["understands decomposing long functions into focused units"]
            q_type = "System Architecture"
        elif "immutability" in name_lower or "prop" in name_lower or "state" in name_lower:
            q_text = f"{target_phrase}how does directly mutating props or state compromise component re-renders and unidirectional data flow?"
            concepts = ["violates immutability", "causes missed re-renders", "unidirectional data flow"]
            criteria = ["understands immutability risks"]
            q_type = "Invariants"
        elif "hook" in name_lower or "closure" in name_lower:
            q_text = f"{target_phrase}what runtime issues can arise from stale closures or omitted hook dependencies?"
            concepts = ["stale closures", "outdated state captured", "hook dependency array"]
            criteria = ["understands hook dependencies and stale closures"]
            q_type = "Invariants"
        elif "cleanup" in name_lower or "timer" in name_lower or "resource" in name_lower:
            q_text = f"{target_phrase}why must timer intervals or subscriptions be cleared upon component unmount, and what happens if teardown is omitted?"
            concepts = ["memory leaks", "zombie timers", "teardown in useEffect"]
            criteria = ["understands resource cleanup in lifecycle"]
            q_type = "Invariants"
        elif "dom" in name_lower or "virtual" in name_lower or "key" in name_lower:
            q_text = f"{target_phrase}how does direct DOM manipulation or unstable keys disrupt Virtual DOM reconciliation?"
            concepts = ["bypasses virtual DOM", "breaks diffing algorithm", "causes unnecessary re-mounts"]
            criteria = ["understands virtual DOM reconciliation"]
            q_type = "Invariants"
        elif "scoping" in name_lower or "var" in name_lower:
            q_text = f"{target_phrase}why should block-scoped const/let declarations be preferred over legacy var?"
            concepts = ["hoisting pitfalls", "block scope vs function scope", "variable shadowing"]
            criteria = ["understands modern variable scoping"]
            q_type = "Code Logic"
        elif "equality" in name_lower:
            q_text = f"{target_phrase}what subtle bugs can arise from implicit type coercion when using loose equality (==)?"
            concepts = ["implicit type coercion", "truthy/falsy confusion", "strict equality avoids coercion"]
            criteria = ["understands strict equality and type coercion"]
            q_type = "Edge Cases"
        elif "error" in name_lower or "catch" in name_lower:
            q_text = f"{target_phrase}what is the risk of having an empty catch block that swallows errors silently?"
            concepts = ["silent error masking", "diagnostics loss", "proper error propagation"]
            criteria = ["understands error handling and resilience"]
            q_type = "Invariants"
        elif clean_name in STANDARDS_QUESTIONS:
            tmpl = STANDARDS_QUESTIONS[clean_name]
            q_text = tmpl["question"]
            concepts = tmpl["concepts"]
            criteria = tmpl["criteria"]
            q_type = "Invariants"
        else:
            advice_match = re.search(r'[—-]\s*(.+)', details)
            advice = advice_match.group(1).strip() if advice_match else "refactoring with cleaner patterns"
            q_text = f"{target_phrase}how should this implementation be refactored to address {clean_name.lower()} via {advice}?"
            concepts = ["understands violation risk", "knows safer alternative", "refactoring best practices"]
            criteria = ["can explain coding standard trade-off"]
            q_type = "Invariants"

        q_text = q_text[0].upper() + q_text[1:]
        return {
            "question": q_text,
            "type": q_type,
            "time_limit": 60,
            "expected_concepts": concepts,
            "evaluation_criteria": criteria,
            "is_fallback": True,
            "is_standards_violation": True
        }

    def _generate_llm_standard_question(self, fs: dict, context: dict | None, api_key: str) -> dict | None:
        """Dynamically generate a natural, context-aware code-review question for a violation using LLM."""
        name = fs.get("name", "")
        clean_name = re.sub(r'\(changed\)', '', name).strip()
        details = fs.get("details", "")

        target = ""
        fn_match = re.search(r'([a-zA-Z0-9_$]+)\(\)', details)
        if fn_match:
            target = f"{fn_match.group(1)}()"
        else:
            file_match = re.search(r'([\w/.-]+\.(?:jsx?|tsx?|py|go|rs|java|vue|svelte))', details)
            if file_match:
                target = os.path.basename(file_match.group(1))

        if not target and context:
            changes = context.get("structured_changes", [])
            if changes and changes[0].get("function"):
                target = f"{changes[0]['function']}()"
            elif changes and changes[0].get("file"):
                target = os.path.basename(changes[0]["file"])

        diff_snippet = ""
        if context:
            changes = context.get("structured_changes", [])
            for c in changes:
                if target and target.strip("()") in c.get("function", ""):
                    diff_snippet = c.get("diff", "")[:400]
                    break
            if not diff_snippet and changes:
                diff_snippet = changes[0].get("diff", "")[:400]

        prompt = (
            "You are a principal engineer conducting a code review.\n"
            f"A coding standard was flagged: '{clean_name}'.\n"
            f"Details: {details}\n"
            f"Target: {target or 'the changed code'}\n"
        )
        if diff_snippet:
            prompt += f"Relevant diff:\n```\n{diff_snippet}\n```\n"
        prompt += (
            "Generate EXACTLY 1 natural, thoughtful, peer-to-peer code review question (under 25 words) asking how to refactor or address this issue.\n"
            "CRITICAL:\n"
            "- Do NOT mention 'Coding audit flagged', 'violation', 'linter', or rule names.\n"
            "- Ask a practical question about control flow, maintainability, architectural invariants, or error handling in the target code.\n"
            "- Keep it direct, specific, and easy to answer in 30-45 seconds.\n"
            "\nOutput format: JSON object with keys:\n"
            '{"question": "...", "type": "Code Logic", "expected_concepts": ["concept1", "concept2"], "evaluation_criteria": ["criteria1"]}'
        )

        try:
            payload = {
                "model": get_model(),
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 300
            }
            status, body = call_nous_api(api_key, payload, timeout=10)
            if status == 200:
                res = json.loads(body)
                choices = res.get("choices") or []
                if choices:
                    text = (choices[0].get("message", {}).get("content") or "").strip()
                    parsed = extract_json(text)
                    if isinstance(parsed, list) and parsed:
                        parsed = parsed[0]
                    if isinstance(parsed, dict) and parsed.get("question"):
                        q = parsed["question"].strip()
                        if "?" in q and len(q) > 15:
                            q_type = parsed.get("type", "Code Logic")
                            if q_type not in self._VALID_TYPES:
                                q_type = "Code Logic"
                            return {
                                "question": q,
                                "type": q_type,
                                "time_limit": self.TIME_LIMITS.get(q_type, 60),
                                "expected_concepts": parsed.get("expected_concepts") or ["code maintainability", "refactoring best practices"],
                                "evaluation_criteria": parsed.get("evaluation_criteria") or ["can explain code design trade-offs"],
                                "is_fallback": False,
                                "is_standards_violation": True
                            }
        except Exception:
            pass
        return None

    def _ensure_standards_questions(self, questions: list, failed_std: list, context: dict | None = None, api_key: str | None = None, hints: dict | None = None) -> list:
        """Enforce target question count and the 80% diff / 20% standards ratio."""
        scale, target_count = self.get_diff_scale(context)
        allowed_standards, required_diff = self.get_standards_and_diff_counts(target_count, failed_std)

        def is_std(q: dict) -> bool:
            if q.get("is_standards_violation"):
                return True
            q_text = q.get("question", "").lower()
            if "coding audit flagged" in q_text or "coding standard" in q_text:
                return True
            for fs in failed_std:
                clean_name = re.sub(r'\(changed\)', '', fs.get("name", "")).strip().lower()
                words = [w for w in re.findall(r'\b\w{4,}\b', clean_name) if w not in ("with", "from", "that")]
                if any(w in q_text for w in words):
                    return True
            for phrase in (
                "mutating props", "mutate props", "in-place array mutation",
                "stale closure", "empty dependency array", "clearinterval",
                "virtual dom reconciliation", "random list keys", "loose equality",
                "empty catch block", "swallowing exceptions", "grew to >80 lines",
                "legacy var keyword", "dangerouslysetinnerhtml",
                "nesting depth", "nested", "nesting", "deeply nested", "flatten",
                "function length", "early return", "guard clause"
            ):
                if phrase in q_text:
                    return True
            return False

        matched_std = [q for q in questions if is_std(q)]
        matched_diff = [q for q in questions if not is_std(q)]

        # Enforce at most allowed_standards
        final_std = matched_std[:allowed_standards]

        # If we need standards questions to reach allowed_standards
        needed_std = allowed_standards - len(final_std)
        if needed_std > 0:
            for i, fs in enumerate(failed_std[:needed_std], len(final_std) + 1):
                q_obj = None
                if api_key:
                    q_obj = self._generate_llm_standard_question(fs, context, api_key)
                if not q_obj:
                    q_obj = self._build_proper_standard_question(fs, context)
                q_obj["question_id"] = f"q_std_{i}"
                q_obj["is_fallback"] = False
                final_std.append(q_obj)

        # Fill diff questions to reach required_diff
        final_diff = matched_diff[:required_diff]
        if len(final_diff) < required_diff:
            fb_diff = self._fallback_diff_questions(context, hints, count=target_count)
            for fb in fb_diff:
                if len(final_diff) >= required_diff:
                    break
                if not any(fb["question"] == q["question"] for q in final_diff):
                    final_diff.append(fb)

        # Combine: diff questions first (80%), then standards questions (20%)
        combined = final_diff + final_std
        combined = combined[:target_count]

        for idx, q in enumerate(combined, 1):
            q["question_id"] = f"q{idx}"
        return combined

    def _fallback_diff_questions(self, context: dict | None = None, hints: dict | None = None, count: int = 5) -> list:
        """Generate high-quality fallback questions purely based on the diff and context."""
        context = context or {}
        hints = hints or {}
        sec = hints.get("security") or {}
        ev = hints.get("evidence") or {}

        pool = []

        if sec.get("include_security_question"):
            focus = ", ".join(sec.get("focus_areas", [])[:2]) or "the security implications"
            target = sec.get("top_file") or "this change"
            pool.append({
                "question": f"Your change touches {focus} in {target}. What are the security risks and how does your code handle them?",
                "type": "Invariants",
                "time_limit": 60,
                "expected_concepts": sec.get("focus_areas", []),
                "evaluation_criteria": ["understands security implications of the change"],
                "is_fallback": True
            })

        findings = ev.get("evidence_findings") or []
        for f in findings:
            pool.append({
                "question": f"A scan flagged: {f['detail']}. Can you explain and address this finding?",
                "type": "Edge Cases",
                "time_limit": 60,
                "expected_concepts": ["understands the flagged finding", "knows the mitigation"],
                "evaluation_criteria": ["can address tool findings"],
                "is_fallback": True
            })

        changes = context.get("structured_changes", [])
        files = [f.get("file") for f in context.get("file_summary", [])] or [c.get("file") for c in changes if c.get("file")]
        scope = f"across {len(files)} files" if files else "in this commit"

        candidates = []
        if changes:
            for sc in changes:
                fn = sc.get("function")
                fl = sc.get("file", "the file")
                fn_desc = f"{fn}() in {fl}" if fn else fl
                candidates.extend([
                    {
                        "question": f"What behavior does the change in {fn_desc} introduce?",
                        "type": "Change Impact",
                        "time_limit": 60,
                        "expected_concepts": ["modifies behavior", "implements requirements"],
                        "evaluation_criteria": ["understands impact"],
                        "is_fallback": True
                    },
                    {
                        "question": f"How does data or state flow through {fn_desc} during execution?",
                        "type": "Data Flow",
                        "time_limit": 45,
                        "expected_concepts": ["data flow", "state flow", "inputs and outputs"],
                        "evaluation_criteria": ["understands data flow"],
                        "is_fallback": True
                    },
                    {
                        "question": f"What edge cases did you consider while updating {fn_desc}?",
                        "type": "Edge Cases",
                        "time_limit": 60,
                        "expected_concepts": ["handles error states", "validates input"],
                        "evaluation_criteria": ["understands edge cases"],
                        "is_fallback": True
                    },
                    {
                        "question": f"Why is logic structured in this sequence within {fn_desc}?",
                        "type": "Code Logic",
                        "time_limit": 30,
                        "expected_concepts": ["execution sequence", "logic order"],
                        "evaluation_criteria": ["understands execution sequence"],
                        "is_fallback": True
                    },
                ])

        candidates.extend([
            {
                "question": f"How do the modified components {scope} coordinate to ensure data consistency?",
                "type": "System Architecture",
                "time_limit": 60,
                "expected_concepts": ["component coordination", "data flow consistency", "state integrity"],
                "evaluation_criteria": ["understands cross-component coordination"],
                "is_fallback": True
            },
            {
                "question": "What error states or edge cases could cause failures across this change?",
                "type": "Invariants",
                "time_limit": 60,
                "expected_concepts": ["handles failure states", "avoids inconsistent state", "error recovery"],
                "evaluation_criteria": ["understands system invariants and failure handling"],
                "is_fallback": True
            },
            {
                "question": "What happens if downstream dependencies or network calls fail during execution?",
                "type": "Dependencies",
                "time_limit": 45,
                "expected_concepts": ["dependency handling", "graceful failure", "error propagation"],
                "evaluation_criteria": ["understands downstream failure modes"],
                "is_fallback": True
            },
            {
                "question": "What invariants must hold true for state consistency after this change?",
                "type": "Invariants",
                "time_limit": 60,
                "expected_concepts": ["state invariants", "consistency guarantees"],
                "evaluation_criteria": ["understands state invariants"],
                "is_fallback": True
            },
            {
                "question": "What input boundary values could cause this logic to behave unexpectedly?",
                "type": "Edge Cases",
                "time_limit": 60,
                "expected_concepts": ["boundary guards", "input validation"],
                "evaluation_criteria": ["understands input boundaries"],
                "is_fallback": True
            },
            {
                "question": "How does this change impact callers and consuming modules across the application?",
                "type": "Change Impact",
                "time_limit": 60,
                "expected_concepts": ["caller impact", "contract stability"],
                "evaluation_criteria": ["understands contract stability"],
                "is_fallback": True
            },
            {
                "question": "What should happen if the primary condition or check in this update fails?",
                "type": "Code Logic",
                "time_limit": 30,
                "expected_concepts": ["fallback logic", "error branch"],
                "evaluation_criteria": ["understands failure path"],
                "is_fallback": True
            },
        ])

        for c in candidates:
            if len(pool) >= count:
                break
            if not any(c["question"] == p["question"] for p in pool):
                pool.append(c)

        return pool[:count]

    def _fallback(self, context: dict | None = None, hints: dict | None = None) -> list:
        context = context or {}
        hints = hints or {}
        scale, target_count = self.get_diff_scale(context)
        std = (hints or {}).get("standards") or []
        failed_std = [s for s in std if not s.get("is_good")]
        standards_count, diff_count = self.get_standards_and_diff_counts(target_count, failed_std)

        diff_questions = self._fallback_diff_questions(context, hints, count=diff_count)

        std_questions = []
        for i, fs in enumerate(failed_std[:standards_count], 1):
            q_obj = self._build_proper_standard_question(fs, context)
            q_obj["question_id"] = f"q_std_{i}"
            std_questions.append(q_obj)

        questions = diff_questions + std_questions
        for idx, q in enumerate(questions[:target_count], 1):
            q["question_id"] = f"q{idx}"
        return questions[:target_count]

    def validate(self, questions: list) -> list:
        """Accept question dicts; skip malformed entries."""
        valid = []
        for q in questions:
            if isinstance(q, dict) and isinstance(q.get("question"), str) and len(q["question"]) > 10:
                valid.append(q)
        return valid
