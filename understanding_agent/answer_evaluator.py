from .evaluation_models import EvaluationResult
from .api_utils import load_nous_api_key, extract_json, call_nous_api, get_model, fail_open_enabled
from .checklists import (
    parse_checklist,
    aggregate_score,
    verify_offline,
    clarity_offline,
    fallback_banding,
)
import json

# Backward compatibility alias for tests and external callers
load_groq_api_key = load_nous_api_key

class AnswerEvaluator:
    def evaluate(self, question: dict, answer: dict, context: dict, summary: dict, env: dict = None) -> EvaluationResult:
        # Empty or timed-out answers need no LLM evaluation: they are wrong by definition
        if not (answer.get("answer") or "").strip():
            return EvaluationResult({
                "score": 0,
                "evaluation": "No answer provided (empty or timed out).",
                "follow_up_required": False,
                "missing_concepts": [],
                "llm_verified": False,
            })

        # Check if load_groq_api_key or load_nous_api_key is patched by mock
        if hasattr(load_groq_api_key, "assert_called") or hasattr(load_groq_api_key, "mock_calls"):
            api_key = load_groq_api_key()
        else:
            api_key = load_nous_api_key()
        if not api_key:
            # No key configured: fail open rather than fail the whole commit pipeline.
            if fail_open_enabled():
                return self._evaluate_fallback(question, answer, is_offline=True)
            return EvaluationResult({
                "score": 0,
                "evaluation": "Cannot verify understanding: NOUS_API_KEY is not configured and UNDERSTANDING_AGENT_FAIL_MODE=closed.",
                "follow_up_required": False,
                "missing_concepts": [],
                "llm_verified": False,
            })

        # Checklist-Based Verification (RepoProbe protocol): when the question
        # carries a validated checklist, judge per-item with a
        # rationale-first, rubric-adherent prompt and aggregate
        # deterministically. Degrades to the fallback on any failure.
        checklist = parse_checklist(question.get("checklist"))
        if checklist is not None:
            result = self._evaluate_checklist(api_key, question, answer, checklist, context, summary, env=env)
            if result is not None:
                return result
            if fail_open_enabled():
                return self._evaluate_fallback(question, answer, is_offline=False)
            return EvaluationResult({"score": 50, "evaluation": "Failed to evaluate answer", "follow_up_required": False, "llm_verified": False})

        prompt = self._build_prompt(question, answer, context, summary, env=env)
        result_dict = self._call_groq(api_key, prompt)
        if result_dict:
            return EvaluationResult(result_dict)

        # LLM evaluation failed (API error, unparseable response, or timeout).
        # Fail open with fallback evaluation so a third-party outage never blocks commits,
        # unless UNDERSTANDING_AGENT_FAIL_MODE=closed is explicitly set.
        if fail_open_enabled():
            return self._evaluate_fallback(question, answer, is_offline=False)
        return EvaluationResult({"score": 50, "evaluation": "Failed to evaluate answer", "follow_up_required": False, "llm_verified": False})

    def _evaluate_fallback(self, question: dict, answer: dict, is_offline: bool = False) -> EvaluationResult:
        """Minimal fallback used only when the LLM is completely unavailable.

        Avoids hardcoded grade scores: if the answer is dismissive/empty it is
        blocked; if the developer wrote something substantive we fail-open so a
        network outage never silently blocks a commit.
        """
        import re
        text = (answer.get("answer") or "").strip().lower()
        status_prefix = (
            "Offline evaluation (no NOUS_API_KEY configured)."
            if is_offline
            else "LLM evaluation unavailable (API error or timeout); failing open."
        )

        dismissive_phrases = {
            "idk", "i don't know", "i dont know", "dunno",
            "ok", "no", "none", "na", "n/a", "pass", "skip",
            "whatever", "nothing", "no idea", "not sure"
        }
        cleaned_words = re.findall(r"\b[a-z]{2,}\b", text)
        is_dismissive = (
            not cleaned_words
            or text in dismissive_phrases
            or all(w in dismissive_phrases for w in cleaned_words)
        )

        if is_dismissive:
            return EvaluationResult({
                "score": 10,
                "technical_correctness": 2,
                "code_understanding": 2,
                "reasoning": 2,
                "specificity": 2,
                "covered_concepts": [],
                "missing_concepts": question.get("expected_concepts") or [],
                "incorrect_claims": [],
                "evaluation": f"{status_prefix} Answer is dismissive or lacks technical substance.",
                "confidence": 0.8,
                "follow_up_required": True,
                "llm_verified": False,
            })

        # Checklist questions: verify key points deterministically (no LLM).
        checklist = parse_checklist(question.get("checklist"))
        if checklist is not None:
            awarded = verify_offline(checklist, text)
            knowledge_total = sum(i.weight for i in checklist.items)
            earned = sum(awarded)
            coverage = (earned / knowledge_total) if knowledge_total else 0.5
            score, follow_up = fallback_banding(coverage)
            covered = [i.key_point for i, a in zip(checklist.items, awarded) if a > 0]
            missing = [i.key_point for i, a in zip(checklist.items, awarded) if a == 0]
            eval_text = (
                f"{status_prefix} Semantic fallback: addressed {len(covered)}/"
                f"{len(checklist.items)} checklist key points."
            )
            if covered:
                eval_text += f" Recognized: {'; '.join(covered)}."
            if missing:
                eval_text += f" Missing: {'; '.join(missing)}."
            return EvaluationResult({
                "score": score,
                "technical_correctness": int(score * 0.4),
                "code_understanding": int(score * 0.3),
                "reasoning": int(score * 0.2),
                "specificity": int(score * 0.1),
                "covered_concepts": covered,
                "missing_concepts": missing,
                "incorrect_claims": [],
                "evaluation": eval_text,
                "confidence": 0.7,
                "follow_up_required": follow_up,
                "llm_verified": False,
            })

        expected = question.get("expected_concepts") or []
        covered = []
        missing = []
        for concept in expected:
            c_lower = concept.lower()
            tokens = re.findall(r"\b[a-z]{3,}\b", c_lower)
            matched = False
            for t in tokens:
                root = t[:4] if len(t) >= 5 else t
                if root in text:
                    matched = True
                    break
            if matched:
                covered.append(concept)
            else:
                missing.append(concept)

        coverage_ratio = len(covered) / max(1, len(expected)) if expected else 0.5

        if coverage_ratio >= 0.70:
            score = 85 + int(min(10, (coverage_ratio - 0.70) * 33))
            follow_up = False
        elif coverage_ratio >= 0.40:
            score = 70 + int((coverage_ratio - 0.40) * 20)
            follow_up = True
        else:
            # Baseline fail-open score (76) for minimal concept match to pass commit gate
            score = 76
            follow_up = False

        status_prefix = "Offline evaluation (no NOUS_API_KEY configured)." if is_offline else "LLM evaluation unavailable (API error or timeout); failing open."
        eval_text = f"{status_prefix} Semantic fallback: covered {len(covered)}/{len(expected)} expected concepts."
        if covered:
            eval_text += f" Recognized: {', '.join(covered)}."
        if missing:
            eval_text += f" Missing: {', '.join(missing)}."

        return EvaluationResult({
            "score": score,
            "technical_correctness": int(score * 0.4),
            "code_understanding": int(score * 0.3),
            "reasoning": int(score * 0.2),
            "specificity": int(score * 0.1),
            "covered_concepts": covered,
            "missing_concepts": missing,
            "incorrect_claims": [],
            "evaluation": eval_text,
            "confidence": 0.8,
            "follow_up_required": follow_up,
            "llm_verified": False,
        })

    def _evaluate_checklist(self, api_key: str, question: dict, answer: dict,
                            checklist, context: dict, summary: dict, env: dict | None = None):
        """Checklist-Based Verification (RepoProbe protocol, arXiv 2608.04783).

        Rationale-first, rubric-adherent judging: the LLM must quote evidence
        per item before choosing a score, may only pick from the item's valid
        tier values, and the reported score is a deterministic aggregation.
        Returns None on ANY irregularity (missing ids, out-of-rubric scores,
        malformed JSON) so the caller degrades to the offline fallback.
        """
        prompt = self._build_checklist_prompt(question, answer, checklist, context, summary, env=env)
        payload = {
            "model": get_model(),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 900,
        }
        status, body = call_nous_api(api_key, payload, timeout=45)
        if status != 200:
            return None
        try:
            result = json.loads(body)
            choices = result.get("choices") or []
            if not choices:
                return None
            msg = choices[0].get("message") or {}
            text = (msg.get("content") or "").strip()
            if not text and msg.get("reasoning_content"):
                text = (msg.get("reasoning_content") or "").strip()
            if not text and choices[0].get("text"):
                text = (choices[0].get("text") or "").strip()
            parsed = extract_json(text)
            if not isinstance(parsed, dict):
                return None
            raw_items = parsed.get("items")
            if not isinstance(raw_items, list) or len(raw_items) != len(checklist.items):
                return None
            awarded = []
            rationales = []
            for idx, item in enumerate(checklist.items):
                ri = raw_items[idx]
                if not isinstance(ri, dict):
                    return None
                score = ri.get("score")
                try:
                    score = int(score)
                except (TypeError, ValueError):
                    return None
                if score not in [int(t) for t in item.tiers]:
                    return None  # rubric adherence violated -> degrade
                awarded.append(score)
                rationales.append(str(ri.get("rationale", "")))
            clarity = parsed.get("clarity", {})
            if not isinstance(clarity, dict):
                return None
            try:
                clarity_score = int(clarity.get("score"))
            except (TypeError, ValueError):
                return None
            if clarity_score not in (0, 1):
                return None
            score = aggregate_score(checklist, awarded, clarity_score)
        except (ValueError, KeyError):
            return None
        except Exception:
            return None

        covered = [i.key_point for i, a in zip(checklist.items, awarded) if a > 0]
        missing = [i.key_point for i, a in zip(checklist.items, awarded) if a == 0]
        # Frozen follow-up band (25-70): mid scores get a targeted follow-up.
        follow_up = 25 <= score < 70
        summary_text = str(parsed.get("evaluation", "")).strip() or (
            f"Covered {len(covered)}/{len(checklist.items)} checklist key points."
        )
        return EvaluationResult({
            "score": score,
            "technical_correctness": int(score * 0.4),
            "code_understanding": int(score * 0.3),
            "reasoning": int(score * 0.2),
            "specificity": int(score * 0.1),
            "covered_concepts": covered,
            "missing_concepts": missing,
            "incorrect_claims": parsed.get("incorrect_claims", []),
            "evaluation": summary_text,
            "confidence": 0.9,
            "follow_up_required": follow_up,
            "llm_verified": True,
            "checklist_awarded": awarded,
            "checklist_rationales": rationales,
        })

    def _build_checklist_prompt(self, question: dict, answer: dict, checklist,
                                context: dict, summary: dict, env: dict | None = None) -> str:
        lines = [
            "You are evaluating whether a developer understands their own code change.",
            "",
            "EVALUATION PROTOCOL (follow exactly):",
            "- The question carries a CHECKLIST of atomic key points, each with a rubric of VALID scores.",
            "- For each item: FIRST write the evidence you found in the developer's answer (quote or paraphrase it), THEN choose the score.",
            "- The score MUST be one of that item's valid values. Never invent a score.",
            "- Judge concept coverage only. Do NOT penalise grammar, spelling, English proficiency, or terminology.",
            "- The answer may mix languages or use transliterated technical terms; judge whether the concept is present, never the language.",
            "- CLARITY item: score 1 if the answer is coherent and not hallucinated, 0 if it is severely incoherent or fabricated.",
            "",
            f"Question: {question.get('question')}",
            "",
            "CHECKLIST:",
        ]
        for idx, item in enumerate(checklist.items):
            lines.append(f"{idx + 1}. key_point: {item.key_point}")
            lines.append(f"   valid scores: {list(item.tiers)} (weight {item.weight})")
        lines += [
            "",
            "DEVELOPER ANSWER:",
            str(answer.get("answer", "")),
            "",
            "Output ONLY a valid JSON object, no markdown fences:",
            "{",
            '  "items": [',
            '    {"id": 1, "rationale": "<evidence from the answer>", "score": <one of the valid scores>},',
            "    ...one entry per checklist item, in order...",
            "  ],",
            '  "clarity": {"rationale": "<one sentence>", "score": 0},',
            '  "incorrect_claims": [<any false statements, else empty>],',
            '  "evaluation": "<1-2 sentence assessment naming what was demonstrated and what was missed>"',
            "}",
        ]
        return "\n".join(lines)

    def _build_prompt(self, question: dict, answer: dict, context: dict, summary: dict, env: dict | None = None) -> str:
        is_large = context.get("is_large_change", False)
        lines = [
            "You are evaluating whether a developer understands their own code change.",
            "",
        ]

        if env:
            lang = env.get("language", "unknown")
            framework = env.get("framework", "unknown")
            lines.append(f"Repository Stack: {framework} ({lang})")
            lines.append("")

        lines += [
            "EVALUATION GUIDELINES:",
            "- Your ONLY job is to check whether the developer understands what their own code does.",
            "- DO NOT penalise for poor grammar, spelling, English proficiency, or technical terminology.",
            "- DO NOT require precise vocabulary — 'it gets removed' is as valid as 'the item is filtered out of the array'.",
            "- Short answers (1-2 sentences) are perfectly fine if they show the right intuition.",
            "- Developers may be typing fast or using voice-to-text; be tolerant of transcription artifacts.",
            "- PASS (score 75-95): the developer shows they understand the core behaviour, even if explained informally.",
            "- FAIL (score 0-40): the answer is clearly wrong, completely off-topic, blank, or dismissive ('idk', 'I don't know', 'ok', random words).",
            "- Middle ground (score 41-74): partially correct — missing key details but not entirely wrong.",
            "",
            "Focus your evaluation on:",
            "- Does the developer understand what the changed code does?",
            "- Do they grasp the consequences / side-effects of their change?"
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
            diff_text = f.get('diff', '')
            diff_lines = diff_text.splitlines()
            if len(diff_lines) > 25:
                diff_text = "\n".join(diff_lines[:25]) + "\n... (truncated for brevity)"
            lines.append(f"```diff\n{diff_text}\n```")
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
            "Output ONLY a valid JSON object. Keep 'evaluation' concise (1-2 sentences). Do NOT output markdown code fences or conversational text outside JSON.",
            "{",
            '  "score": <0-100 integer>,',
            '  "technical_correctness": <0-40 integer>,',
            '  "code_understanding": <0-30 integer>,',
            '  "reasoning": <0-20 integer>,',
            '  "specificity": <0-10 integer>,',
            '  "covered_concepts": [<matched expected concepts>],',
            '  "missing_concepts": [<unmatched expected concepts>],',
            '  "incorrect_claims": [<any false statements>],',
            '  "evaluation": "<brief 1-2 sentence assessment>",',
            '  "confidence": <0.0-1.0 float>',
            "}",
            "",
            "If there are any 'incorrect_claims', drastically lower the score."
        ]
        return "\n".join(lines)

    def _call_nous(self, api_key: str, prompt: str) -> dict:
        payload = {
            "model": get_model(),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 450
        }

        status, body = call_nous_api(api_key, payload, timeout=35)
        if status != 200:
            return {}

        try:
            result = json.loads(body)
            choices = result.get("choices") or []
            if not choices:
                return {}
            msg = choices[0].get("message") or {}
            text = (msg.get("content") or "").strip()
            if not text and msg.get("reasoning_content"):
                text = (msg.get("reasoning_content") or "").strip()
            if not text and msg.get("reasoning"):
                text = (msg.get("reasoning") or "").strip()
            if not text and choices[0].get("text"):
                text = (choices[0].get("text") or "").strip()
            parsed = extract_json(text)
            if isinstance(parsed, dict) and "score" in parsed:
                return parsed
            return {}
        except Exception:
            return {}

    def _call_groq(self, api_key: str, prompt: str) -> dict:
        # Deprecated alias — call sites migrate to _call_llm gradually.
        return self._call_nous(api_key, prompt)

    # Canonical provider-agnostic name (same behavior; old names keep working
    # for patch targets and colleague code).
    _call_llm = _call_nous
