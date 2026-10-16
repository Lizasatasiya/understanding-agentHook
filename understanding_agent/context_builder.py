import subprocess
import ast
import re

from .language_support import (
    language_for,
    extract_functions as generic_extract_functions,
)


class ContextBuilder:

    # Phase 2 budgets: default per-function diff cap (unchanged historical
    # behavior) and the total cap across all function diffs in one context.
    _DEFAULT_DIFF_CAP = 35
    _TOTAL_DIFF_BUDGET = 240

    def build(self, changes: dict, graph) -> dict:
        raw_diffs = []
        changed_code = changes.get("files", [])
        
        # Determine all functions that were modified in the commit
        changed_funcs = set()
        for f in changed_code:
            for func in f.get("changed_functions", []):
                changed_funcs.add(func["name"])
                
        # Build dependency summaries
        dep_summaries = {}
        
        # Extract dependencies discovered by the CodeGraph
        for node_id, data in graph.nodes.items():
            if data["type"] == "function" and data["name"] not in changed_funcs:
                # Find which file it was defined in
                defined_file = None
                for edge in graph.edges:
                    if edge["source"] == node_id and edge["relation"] == "defined_in":
                        defined_file = edge["target"].split(":", 1)[1]
                        break
                
                if defined_file:
                    details = self._get_function_details(defined_file, data["name"])
                    
                    # find what this calls
                    calls = []
                    for edge in graph.edges:
                        if edge["source"] == node_id and edge["relation"] == "calls":
                            calls.append(edge["target"].split(":", 1)[1])
                            
                    dep_summaries[data["name"]] = {
                        "defined_in": defined_file,
                        "details": details,
                        "calls": calls
                    }
        
        structured_changes = []
        for f in changed_code:
            filepath = f["path"]
            language = language_for(filepath)

            # Get old and new source
            try:
                old_source = subprocess.check_output(["git", "show", f"HEAD:{filepath}"], text=True, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                old_source = ""

            try:
                new_source = subprocess.check_output(["git", "show", f":{filepath}"], text=True, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                new_source = ""

            for func in f.get("changed_functions", []):
                func_name = func["name"]
                added_calls = func.get("added_calls", [])

                dep_text = self._build_dependency_summary(func_name, added_calls, dep_summaries)

                # Extract function diff
                if func_name == "<module>":
                    old_func_body = old_source
                    new_func_body = new_source
                else:
                    old_func_body = self._extract_function_source(old_source, func_name, language)
                    new_func_body = self._extract_function_source(new_source, func_name, language)
                
                # Skip if the function body is unchanged (hunk overlap false-positive)
                if old_func_body == new_func_body and old_func_body != "":
                    continue
                
                import difflib
                diff_lines = list(difflib.unified_diff(
                    old_func_body.splitlines(keepends=True),
                    new_func_body.splitlines(keepends=True),
                    fromfile=f"a/{filepath} ({func_name})",
                    tofile=f"b/{filepath} ({func_name})",
                    n=2
                ))
                func_diff = "".join(diff_lines)
                if not func_diff:
                    try:
                        file_diff = subprocess.check_output(
                            ["git", "diff", "--cached", "-U2", "--", filepath],
                            text=True, stderr=subprocess.DEVNULL
                        )
                        func_diff = file_diff if file_diff else f"New code added: {func_name}"
                    except Exception:
                        func_diff = f"New code added: {func_name}"
                
                raw_diffs.append(func_diff)

                structured_changes.append({
                    "file": filepath,
                    "function": func_name,
                    "diff": func_diff,  # replaced by the budgeted selection below
                    "dependency_summary": dep_text
                })

        # Phase 2 (PLAN_V1_COMPLETION.md): token-budgeted context selection.
        # Two levels, deterministic: (1) a TOTAL budget across all function
        # diffs so one giant function cannot starve the rest; (2) within a
        # function, importance-ranked selection instead of positional
        # truncation — the +/- changed lines ARE the change and are kept
        # (head+tail in the extreme), structural context fills next, filler
        # context is dropped first. Diffs within budget pass through
        # untouched (byte-identical to the old behavior for normal sizes).
        total_raw = sum(len(d.splitlines()) for d in raw_diffs)
        if raw_diffs and total_raw > self._TOTAL_DIFF_BUDGET:
            per_fn_cap = max(12, self._TOTAL_DIFF_BUDGET // len(raw_diffs))
        else:
            per_fn_cap = self._DEFAULT_DIFF_CAP
        for entry, raw in zip(structured_changes, raw_diffs):
            entry["diff"] = self._compress_diff(raw, max_lines=per_fn_cap)
        
        stats = changes.get("stats", {})
        is_large_change = stats.get("is_large_change", False) or len(structured_changes) > 3

        file_summary = []
        for f in changed_code:
            file_summary.append({
                "file": f["path"],
                "functions": [fn["name"] for fn in f.get("changed_functions", [])]
            })

        context = {
            "structured_changes": structured_changes,
            "raw_dependencies": dep_summaries,
            "tests": [],
            "documentation": [],
            "graph_relationships": graph.edges,
            "stats": stats,
            "is_large_change": is_large_change,
            "file_summary": file_summary
        }
        
        return context

    def _compress_diff(self, diff_text: str, max_lines: int = 35) -> str:
        """Importance-ranked diff selection under a line budget.

        Rank 0 — the +/- changed lines: they ARE the change; kept first
        (head+tail truncation only in the extreme case where they alone
        exceed the budget).
        Rank 1 — structural context (signatures, returns, raises, control
        flow): the lines that make the surrounding logic readable.
        Rank 2 — filler context: dropped first.

        Diffs within the budget pass through unchanged. Kept lines are
        emitted in their original order; a trailing marker states how many
        lines of each rank were compressed away.
        """
        lines = diff_text.splitlines()
        if len(lines) <= max_lines:
            return diff_text

        headers = [l for l in lines[:3] if l.startswith(("---", "+++", "@@"))]

        def rank(line: str) -> int:
            if line.startswith(("---", "+++", "@@")):
                return -1  # header
            if line.startswith(("+", "-")):
                return 0  # changed — the actual diff
            stripped = line.strip()
            if (stripped.startswith(("def ", "class ", "return ", "raise ", "async def ",
                                     "if ", "for ", "while ", "try:", "except ", "with "))
                    or "try:" in stripped or "except " in stripped):
                return 1  # structural context
            return 2  # filler context

        ranked = [(i, rank(l)) for i, l in enumerate(lines)]
        body_budget = max(8, max_lines - len(headers) - 1)  # 1 slot for the marker

        kept = {i for i, r in ranked if r == -1}
        changed_idx = [i for i, r in ranked if r == 0]
        structural_idx = [i for i, r in ranked if r == 1]
        filler_idx = [i for i, r in ranked if r == 2]

        dropped = {"changed": 0, "structural": 0, "filler": 0}

        if len(changed_idx) > body_budget:
            # Extreme: the change alone overflows — keep head+tail of it
            half = body_budget // 2
            keep_set = set(changed_idx[:half]) | set(changed_idx[-(body_budget - half):])
            dropped["changed"] = len(changed_idx) - body_budget
            dropped["structural"] = len(structural_idx)
            dropped["filler"] = len(filler_idx)
        else:
            slots = body_budget - len(changed_idx)
            keep_set = set(changed_idx)
            take_struct = structural_idx[:slots]
            keep_set |= set(take_struct)
            dropped["structural"] = len(structural_idx) - len(take_struct)
            slots -= len(take_struct)
            take_filler = filler_idx[:slots] if slots > 0 else []
            keep_set |= set(take_filler)
            dropped["filler"] = len(filler_idx) - len(take_filler)

        kept |= keep_set
        out = [lines[i] for i in sorted(kept)]
        compressed_n = sum(dropped.values())
        if compressed_n:
            out.append(f"@@ ... ({compressed_n} lines compressed: "
                       f"{dropped['filler']} filler, {dropped['structural']} structural, "
                       f"{dropped['changed']} changed) ... @@")
        return "\n".join(out)

    def _get_function_details(self, filepath: str, func_name: str) -> dict:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                source = f.read()
            if filepath.endswith('.py'):
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                        args = [arg.arg for arg in node.args.args]
                        returns = "None"
                        if node.returns:
                            if hasattr(ast, 'unparse'):
                                returns = ast.unparse(node.returns)
                            elif hasattr(node.returns, 'id'):
                                returns = node.returns.id
                        docstring = ast.get_docstring(node)
                        is_async = isinstance(node, ast.AsyncFunctionDef)
                        return {
                            "args": args,
                            "returns": returns,
                            "docstring": docstring or "No description.",
                            "is_async": is_async
                        }
            else:
                import re
                func_src = self._extract_function_source(source, func_name)
                is_async = bool(re.search(r'\basync\b', func_src))
                m = re.search(rf'{re.escape(func_name)}\s*\(([^)]*)\)', func_src)
                args = [a.strip().split(':')[0] for a in m.group(1).split(',')] if m and m.group(1).strip() else []
                return {
                    "args": args,
                    "returns": "unknown",
                    "docstring": "No description.",
                    "is_async": is_async
                }
        except Exception:
            pass
        return {"args": [], "returns": "unknown", "docstring": "No description.", "is_async": False}
        
    def _extract_function_source(self, source: str, func_name: str, language: str = "python") -> str:
        if not source:
            return ""
        if language == "python":
            try:
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                        return ast.get_source_segment(source, node) or ""
            except SyntaxError:
                pass
            return ""
        # Non-Python: slice the function's heuristic span from the source
        for fn in generic_extract_functions(source, language):
            if fn["name"] == func_name:
                lines = source.splitlines()
                start = fn["start_line"] - 1
                end = min(fn["end_line"], len(lines))
                # Phase 2 (context eval finding): a NESTED definition (e.g. a
                # React handler inside a component) gets a "next definition
                # wins" span that excludes the enclosing function's preamble
                # — hooks, consts — which the change's behavior depends on.
                # Extend the slice upward to the enclosing definition, but
                # ONLY when the nearest shallower non-blank line above is
                # itself a definition line (function/arrow-const). Class
                # bodies (`export class X {`) are not definition matches, so
                # class methods do NOT over-extend.
                if language in ("javascript", "typescript") and start > 0:
                    def_indent = len(lines[start]) - len(lines[start].lstrip())
                    if def_indent >= 2:
                        for j in range(start - 1, -1, -1):
                            prev = lines[j]
                            if not prev.strip():
                                continue
                            prev_indent = len(prev) - len(prev.lstrip())
                            if prev_indent < def_indent:
                                if re.match(
                                    r"\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*\w+\s*\("
                                    r"|\s*(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>)",
                                    prev,
                                ):
                                    start = j
                                break
                return "\n".join(lines[start:end])
        return ""
        
    def _build_dependency_summary(self, func_name: str, direct_calls: list, dep_summaries: dict) -> str:
        if not direct_calls:
            return "No external dependencies called."
            
        summary_lines = []
        for call in direct_calls:
            if call in dep_summaries:
                info = dep_summaries[call]
                details = info['details']
                async_str = "an async function" if details['is_async'] else "a function"
                ret_str = f"returns {details['returns']}"
                
                doc = details['docstring'].replace('\n', ' ')
                line = f"- `{call}({', '.join(details['args'])})` is {async_str} defined in `{info['defined_in']}`. It {ret_str}. Description: {doc}"
                
                if info['calls']:
                    line += f" It in turn calls: {', '.join(info['calls'])}."
                    
                    for sub_call in info['calls']:
                        if sub_call in dep_summaries:
                            sub_info = dep_summaries[sub_call]
                            sub_details = sub_info['details']
                            sub_async = "an async function" if sub_details['is_async'] else "a function"
                            sub_ret = f"returns {sub_details['returns']}"
                            sub_doc = sub_details['docstring'].replace('\n', ' ')
                            line += f"\n    - `{sub_call}({', '.join(sub_details['args'])})` is {sub_async}. It {sub_ret}. Description: {sub_doc}"
                            
                summary_lines.append(line)
                
        return "\n".join(summary_lines)
