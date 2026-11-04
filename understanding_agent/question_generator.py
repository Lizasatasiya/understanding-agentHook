import json
from .api_utils import load_nous_api_key, extract_json, call_nous_api, get_model, log_llm_error
from .checklists import parse_checklist
from .change_kind import classification_hint_lines


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
        scale, target_count = self.get_diff_scale(context)

        api_key = load_nous_api_key()
        if not api_key:
            log_llm_error(
                "No API key configured. Set UNDERSTANDING_AGENT_API_KEY. "
                "No questions will be asked."
            )
            return []

        prompt = self._build_prompt(context, summary, hints, env=env)
        is_large = (scale == "large")
        questions = self._call_groq(api_key, prompt, target_count=target_count, is_large=is_large)
        if not questions:
            return []
        return self._finalize_checklists(questions[:target_count])

    def _derive_checklist(self, question: dict) -> dict | None:
        """Deterministic checklist for questions without an LLM-authored one.

        Built from the question's own expected_concepts / evaluation_criteria
        so the offline fallback verifier has atomic key points to check
        instead of generic root-word matching. Returns the RAW dict form
        (validated through parse_checklist) or None.
        """
        concepts = [c for c in (question.get("expected_concepts") or [])
                    if isinstance(c, str) and c.strip()][:3]
        criteria = [c for c in (question.get("evaluation_criteria") or [])
                    if isinstance(c, str) and c.strip()][:2]
        items = []
        for c in concepts:
            items.append({"key_point": f"addresses {c}", "weight": 2, "tiers": [2, 0]})
        for c in criteria:
            if len(items) >= 4:
                break
            items.append({"key_point": c, "weight": 1, "tiers": [1, 0]})
        if len(items) < 2:
            items = [
                {"key_point": "explains what the change does", "weight": 2, "tiers": [2, 0]},
                {"key_point": "gives a technically substantive answer", "weight": 1, "tiers": [1, 0]},
            ]
        raw = {"items": items[:4]}
        return raw if parse_checklist(raw) is not None else None

    def _finalize_checklists(self, questions: list) -> list:
        """Ensure every generated question carries a valid checklist.

        LLM-authored checklists are kept only when schema-valid; otherwise a
        deterministic checklist is derived from the question's own concepts.
        Cached/legacy questions without checklists are untouched by this pass
        (the evaluator keeps its legacy scalar path for them).
        """
        for q in questions:
            if not isinstance(q, dict):
                continue
            if q.get("checklist") and parse_checklist(q.get("checklist")) is not None:
                continue
            derived = self._derive_checklist(q)
            if derived is not None:
                q["checklist"] = derived
            elif q.get("checklist"):
                del q["checklist"]  # invalid LLM checklist: drop, use scalar path
        return questions

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

        ck_lines = classification_hint_lines((context or {}).get("change_kind"))
        lines += [
            "## Changed Functions",
        ]

        if ck_lines:
            lines += ["", *ck_lines]

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
            '  "evaluation_criteria": [<list of criteria strings>],',
            '  "checklist": {',
            '    "items": [ 2 to 4 atomic key points a correct answer must cover, each with:',
            '               {"key_point": "<one verifiable fact or concept>",',
            '                "weight": <1-3 integer points>,',
            '                "tiers": <descending valid scores incl. 0, e.g. [3, 2, 0] or [2, 0]>}',
            '    ]',
            '  }',
            "",
            'Example: [{"question_id": "q1", "question": "Why is X called before Y?", "type": "Code Logic", "expected_concepts": ["X must be validated before Y executes"], "evaluation_criteria": ["understands the validation order"], "checklist": {"items": [{"key_point": "explains X validates input before Y runs", "weight": 2, "tiers": [2, 0]}, {"key_point": "identifies the consequence of skipping validation", "weight": 2, "tiers": [2, 0]}]}}]',
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

        ck_lines = classification_hint_lines((context or {}).get("change_kind"))
        if ck_lines:
            lines += ["", *ck_lines]

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
            '  "evaluation_criteria": [<list of evaluation criteria strings>],',
            '  "checklist": {"items": [2-4 atomic key points, each {"key_point": "<one verifiable fact>", "weight": <1-3>, "tiers": [descending valid scores incl. 0]}]}',
            "",
            'Example: [{"question_id": "q1", "question": "Why is stock reserved before payment rather than after?", "type": "System Architecture", "expected_concepts": ["prevents overselling", "ensures availability"], "evaluation_criteria": ["understands reservation order"], "checklist": {"items": [{"key_point": "explains reservation prevents overselling", "weight": 2, "tiers": [2, 0]}, {"key_point": "identifies the failure if payment precedes reservation", "weight": 2, "tiers": [2, 0]}]}}]'
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
            "max_tokens": max(1500, target_count * 350)
        }

        status, body = call_nous_api(api_key, payload, timeout=30)
        if status != 200:
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
                if text:
                    snippet = text.strip()
                    if len(snippet) > 500:
                        snippet = snippet[:500] + "...(truncated)"
                    log_llm_error(
                        "The model replied, but the reply was not a list of questions. "
                        f"Response: {snippet}"
                    )
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
        except Exception as exc:
            log_llm_error(f"Could not read the model response while generating questions: {exc}")
            return []

    def _call_groq(self, api_key: str, prompt: str, target_count: int = 3, is_large: bool = False) -> list:
        # Deprecated alias — call sites migrate to _call_llm gradually.
        return self._call_nous(api_key, prompt, target_count=target_count, is_large=is_large)

    # Canonical provider-agnostic name (same behavior; old names keep working
    # for patch targets and colleague code).
    _call_llm = _call_nous

    def validate(self, questions: list) -> list:
        """Accept question dicts; skip malformed entries."""
        valid = []
        for q in questions:
            if isinstance(q, dict) and isinstance(q.get("question"), str) and len(q["question"]) > 10:
                valid.append(q)
        return valid
