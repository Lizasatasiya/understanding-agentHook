import os
import json
from .api_utils import load_nous_api_key, extract_json, call_nous_api, get_model, log_llm_error, format_token_usage


class ChangeSummary:
    def generate(self, context: dict) -> dict:
        api_key = load_nous_api_key()
        changes = context.get("structured_changes", [])
        is_large = context.get("is_large_change", False) or len(changes) > 1

        if not changes:
            return {}

        # For multi-component or large changes, perform a single unified summary call
        if is_large or len(changes) > 1:
            if not api_key:
                log_llm_error(
                    "No API key configured. Set UNDERSTANDING_AGENT_API_KEY. "
                    "The change summary was not written by the model."
                )
                summary = self._unified_fallback(context)
            else:
                prompt = self._build_unified_prompt(context)
                summary = self._call_groq(api_key, prompt, max_tokens=800)
                if not summary:
                    summary = self._unified_fallback(context)

            # Assign summary to context and all changed components
            context["summary"] = summary
            for f in changes:
                f["summary"] = summary
            return summary

        # Single small change
        f = changes[0]
        if not api_key:
            log_llm_error(
                "No API key configured. Set UNDERSTANDING_AGENT_API_KEY. "
                "The change summary was not written by the model."
            )
            single_sum = self._fallback(f)
        else:
            prompt = self._build_prompt_for_single(f)
            single_sum = self._call_groq(api_key, prompt, max_tokens=800)
            if not single_sum:
                single_sum = self._fallback(f)

        f["summary"] = single_sum
        context["summary"] = single_sum
        return single_sum

    def _build_unified_prompt(self, context: dict) -> str:
        lines = [
            "You are a senior developer analyzing a substantial git commit.",
            "Based on the diffs and code context across all changed components, generate a concise architectural summary.",
            "Return ONLY a valid JSON object with EXACTLY these four string keys: 'what_changed', 'impact', 'why_it_matters', 'key_risks'.",
            "",
            "## Commit Scale & Changed Files"
        ]
        stats = context.get("stats", {})
        if stats:
            lines.append(f"Total Lines Changed: +{stats.get('total_added', 0)} / -{stats.get('total_deleted', 0)}")
        
        for file_info in context.get("file_summary", []):
            funcs = ", ".join(file_info.get("functions", [])) or "module-level code"
            lines.append(f"- {file_info['file']} (Modified: {funcs})")
            
        lines.append("\n## Component Diffs (Skeleton/Highlights):")
        for f in context.get("structured_changes", [])[:6]:
            lines.append(f"### {f.get('file')} | {f.get('function')}()")
            lines.append(f"```diff\n{f.get('diff', '')}\n```")
            dep_summary = f.get('dependency_summary', '')
            if dep_summary and dep_summary != "No external dependencies called.":
                lines.append(f"Dependencies: {dep_summary}")
            lines.append("")

        lines.append("Return ONLY the JSON object. Example: {\"what_changed\": \"...\", \"impact\": \"...\", \"why_it_matters\": \"...\", \"key_risks\": \"...\"}")
        return "\n".join(lines)

    def _build_prompt_for_single(self, f: dict) -> str:
        lines = [
            "You are a senior developer analyzing a git commit.",
            "Based on the diffs and code context provided, generate a short summary.",
            "Return ONLY a valid JSON object with EXACTLY these three string keys: 'what_changed', 'impact', 'why_it_matters'.",
            "",
            "## Change in this commit",
            f"File: {f.get('file', '?')} | Function: {f.get('function', '?')}()",
            "Diff:",
            f"```diff\n{f.get('diff', '')}\n```",
            "",
            "Return ONLY a JSON object. Example: {\"what_changed\": \"Added X\", \"impact\": \"Y will now Z\", \"why_it_matters\": \"Fixes bug A\"}"
        ]
        return "\n".join(lines)

    def _call_nous(self, api_key: str, prompt: str, max_tokens: int = 800) -> dict:
        payload = {
            "model": get_model(),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "_purpose": "Change Summary",
        }

        status, body = call_nous_api(api_key, payload, timeout=30)
        if status != 200:
            return {}

        try:
            result = json.loads(body)
            usage_str = format_token_usage(result)
            if usage_str:
                print(f"  [Change Summary Tokens] {usage_str}", flush=True)

            choices = result.get("choices") or []
            if not choices:
                return {}
            msg = choices[0].get("message") or {}
            text = (msg.get("content") or "").strip()
            if not text and msg.get("reasoning"):
                text = (msg.get("reasoning") or "").strip()
            if not text and msg.get("reasoning_content"):
                text = (msg.get("reasoning_content") or "").strip()

            parsed = extract_json(text)
            if isinstance(parsed, dict):
                what_changed = (
                    parsed.get("what_changed")
                    or parsed.get("whatChanged")
                    or parsed.get("summary")
                    or parsed.get("description")
                    or parsed.get("changes")
                )
                if what_changed:
                    return {
                        "what_changed": str(what_changed),
                        "impact": str(parsed.get("impact") or parsed.get("why_it_matters") or "Updates component logic."),
                        "why_it_matters": str(parsed.get("why_it_matters") or parsed.get("impact") or "Implements required functionality."),
                        "key_risks": str(parsed.get("key_risks") or "")
                    }

            # If the model replied with a direct text summary instead of a JSON object
            if text and len(text) > 15 and not text.lower().startswith("error"):
                return {
                    "what_changed": text,
                    "impact": "Updates staged component code.",
                    "why_it_matters": "Implements required functionality.",
                    "key_risks": ""
                }

            if text:
                log_llm_error("Change summary failed: the model reply was not a summary object.")
            return {}
        except Exception as exc:
            log_llm_error(f"Change summary failed: could not read the model reply ({exc}).")
            return {}

    def _call_groq(self, api_key: str, prompt: str, max_tokens: int = 300) -> dict:
        return self._call_nous(api_key, prompt, max_tokens=max_tokens)

    def _unified_fallback(self, context: dict) -> dict:
        stats = context.get("stats", {})
        loc = stats.get("total_loc", 0)
        files = [f.get("file") for f in context.get("file_summary", [])]
        files_str = ", ".join(files) if files else "staged files"
        return {
            "what_changed": f"Updated {len(files)} file(s) ({files_str}) with {loc} total lines changed.",
            "impact": "Introduces multi-component updates across the codebase.",
            "why_it_matters": "Implements required functional logic.",
            "key_risks": "Verify cross-component contracts and state handling under edge cases."
        }

    def _fallback(self, f: dict = None) -> dict:
        func = f.get('function', '') if f else ''
        file = f.get('file', '') if f else ''
        target = f"{func}() in {file}" if func and file else file or "staged code"
        return {
            "what_changed": f"Updated logic in {target}.",
            "why_it_matters": "Modifies code behavior for this commit.",
            "impact": "Refer to diff above for detailed line changes."
        }
