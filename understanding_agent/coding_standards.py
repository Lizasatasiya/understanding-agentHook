"""Deterministic Coding Standards Analyzer.

Audits staged changes against established coding standards — scoped to what
this commit ADDED (added diff lines, changed functions), never whole-file
history:
- React & Frontend Standards (Immutability, Hooks, Cleanups, Virtual DOM)
- Modern JavaScript/TypeScript Standards (Variable Scoping, Strict Equality, Error Handling)
- Python Standards (Exception specificity, Resource management, Mutable defaults)
- Go Standards (Error handling)
- Complexity (changed-function length, nesting depth)
- Input Validation (changed functions accepting external input)

Security checks live in security_lens.SecurityLens, not here: the credential
gate hard-blocks stealable secrets, and the rest become severity-weighted
security questions. One module owns security.

Produces a clear terminal report displaying which standards PASSED (Good)
and which standards FAILED (Violations) before questions are presented.
"""

import os
import re
import subprocess
from typing import List, Dict, Any


class CodingStandardsChecker:
    """Analyzes staged git diffs for compliance with software engineering standards."""

    def __init__(self, repo_root: str | None = None):
        self.repo_root = repo_root or os.getcwd()

    def check(self, changes: Dict[str, Any], context: Dict[str, Any] | None = None, commit_hash: str | None = None) -> List[Dict[str, Any]]:
        """Run all coding standard checks across the staged or commit files."""
        staged_files = changes.get("files", [])
        if not staged_files:
            return []

        results: List[Dict[str, Any]] = []

        # Collect raw diff and added lines per file
        file_diffs = {}
        file_contents = {}
        for f in staged_files:
            path = f.get("path", "")
            if not path or f.get("status") == "deleted":
                continue
            try:
                if commit_hash:
                    diff_out = subprocess.check_output(
                        ["git", "show", "-U3", commit_hash, "--", path],
                        text=True, stderr=subprocess.DEVNULL, cwd=self.repo_root
                    )
                else:
                    diff_out = subprocess.check_output(
                        ["git", "diff", "--cached", "-U3", path],
                        text=True, stderr=subprocess.DEVNULL, cwd=self.repo_root
                    )
                file_diffs[path] = diff_out
            except Exception:
                file_diffs[path] = ""

            try:
                if commit_hash:
                    content_out = subprocess.check_output(
                        ["git", "show", f"{commit_hash}:{path}"],
                        text=True, stderr=subprocess.DEVNULL, cwd=self.repo_root
                    )
                else:
                    content_out = subprocess.check_output(
                        ["git", "show", f":{path}"],
                        text=True, stderr=subprocess.DEVNULL, cwd=self.repo_root
                    )
                file_contents[path] = content_out
            except Exception:
                file_contents[path] = ""

        # Detect stacks present in staged changes
        has_react = any(
            p.endswith(('.jsx', '.tsx')) or 'react' in file_contents.get(p, '').lower()
            for p in file_diffs
        )
        has_js_ts = any(
            p.endswith(('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'))
            for p in file_diffs
        )
        has_python = any(p.endswith('.py') for p in file_diffs)
        has_go = any(p.endswith('.go') for p in file_diffs)

        # Security checks (secrets, transport, XSS/eval) are NOT run here —
        # they moved to security_lens.SecurityLens (one module owns security:
        # the credential gate hard-blocks, the rest become severity-weighted
        # questions via security_question_hint). See PLAN.md A0.1.

        # 1. State & Prop Immutability (React / Frontend)
        if has_react:
            results.append(self._check_react_immutability(file_diffs, file_contents))
            results.append(self._check_react_hooks_integrity(file_diffs, file_contents))
            results.append(self._check_resource_cleanup(file_diffs, file_contents))
            results.append(self._check_virtual_dom_safety(file_diffs, file_contents))

        # Modern JavaScript/TypeScript standards
        if has_js_ts:
            results.append(self._check_variable_scoping(file_diffs, file_contents))
            results.append(self._check_strict_equality(file_diffs, file_contents))

        # 10. Error Handling & Resilience
        results.append(self._check_error_handling(file_diffs, file_contents))

        # Python specific standards
        if has_python:
            results.append(self._check_python_exception_handling(file_diffs, file_contents))
            results.append(self._check_python_resource_management(file_diffs, file_contents))
            # A2.1: mutable default arguments
            results.append(self._check_python_mutable_defaults(file_diffs, file_contents))

        # Go specific standards
        if has_go:
            results.append(self._check_go_error_handling(file_diffs, file_contents))

        # A3: complexity — scoped to functions this commit touched, never whole-file
        results.append(self._check_function_length(file_diffs, file_contents))
        results.append(self._check_nesting_depth(file_diffs, file_contents))

        # 13. Input Validation & Defensive Guardrails — changed-function scoped
        results.append(self._check_input_validation(file_diffs, file_contents))

        return results

    # -------------------------------------------------------------------------
    # Helper utilities for Line Number & Diff Parsing
    # -------------------------------------------------------------------------

    @staticmethod
    def _get_diff_additions(diff: str) -> List[tuple[int, str]]:
        """Extract added lines with their 1-indexed target line numbers from unified diff."""
        results: List[tuple[int, str]] = []
        current_line = 1
        has_hunk = False

        for raw_line in diff.splitlines():
            if raw_line.startswith('@@ '):
                m = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@', raw_line)
                if m:
                    current_line = int(m.group(1))
                    has_hunk = True
                continue

            if raw_line.startswith('+++') or raw_line.startswith('---'):
                continue

            if raw_line.startswith('+'):
                results.append((current_line, raw_line[1:]))
                current_line += 1
            elif raw_line.startswith('-'):
                pass
            elif raw_line.startswith(' '):
                current_line += 1
            elif not has_hunk:
                results.append((current_line, raw_line))
                current_line += 1

        return results

    @staticmethod
    def _offset_to_line(content: str, offset: int) -> int:
        """Convert a character offset in content into a 1-indexed line number."""
        return content[:offset].count('\n') + 1

    # -------------------------------------------------------------------------
    # Individual Coding Standard Checkers
    # -------------------------------------------------------------------------

    def _check_react_immutability(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        violations = []
        for path, diff in diffs.items():
            if not path.endswith(('.jsx', '.tsx', '.js', '.ts')):
                continue
            for line_no, raw_code in self._get_diff_additions(diff):
                code = raw_code.strip()
                # Check for direct prop mutation: props.foo = ... or props.foo.bar = ...
                if re.search(r'\bprops\.[a-zA-Z0-9_$]+(?:\.[a-zA-Z0-9_$]+)?\s*[\+\-\*\/]?=\s*[^=]', code):
                    violations.append(f"{path}:{line_no}: Direct mutation of props (`{code[:50]}`)")
                # In-place array mutation on state-like names (setX / setState convention)
                if re.search(r'\b(?:set[A-Z]\w*|state|this\.state|this\.props)\.[a-zA-Z0-9_$]*\.(?:push|splice|shift|unshift|pop)\(', code):
                    violations.append(f"{path}:{line_no}: In-place array mutation of state (`{code[:50]}`)")

        if violations:
            return {
                "name": "State & Prop Immutability",
                "status": "FAILED",
                "is_good": False,
                "category": "React / State",
                "violations": violations[:3],
                "details": "; ".join(violations[:3])
            }
        return {
            "name": "State & Prop Immutability",
            "status": "PASSED",
            "is_good": True,
            "category": "React / State",
            "details": "Immutable data flow maintained; no direct prop or state mutations."
        }

    def _check_react_hooks_integrity(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        violations = []
        for path, content in contents.items():
            if not path.endswith(('.jsx', '.tsx', '.js', '.ts')):
                continue
            # Look for useCallback/useMemo with empty dependency array referencing
            # identifiers declared OUTSIDE the hook body (stale closure risk).
            for m in re.finditer(r'use(?:Callback|Memo)\s*\(\s*(?:\([^)]*\)|[a-zA-Z0-9_$]+)\s*=>?\s*\{?([\s\S]*?)\}?\s*,\s*\[\s*\]\s*\)', content):
                body = m.group(1)
                # `props` is always outer scope; other identifiers are checked
                # only when they are not parameters of the callback itself.
                if re.search(r'\bprops\b', body):
                    line_no = self._offset_to_line(content, m.start())
                    violations.append(f"{path}:{line_no}: Stale closure in useCallback (empty `[]` dependencies while referencing outer scope)")
                    break
                # Generic heuristic: body references a camelCase identifier that
                # is assigned somewhere in the file but never passed through a
                # dependency array or the callback's own parameter list.
                for ident_m in re.finditer(r'\b([a-z][a-zA-Z0-9]{2,})\b', body):
                    ident = ident_m.group(1)
                    if ident in ("const", "let", "return", "console", "JSON", "Math", "window", "document", "true", "false", "null", "undefined"):
                        continue
                    # Declared at file/function scope and used bare in the body → stale-closure candidate
                    if re.search(rf'\b(?:const|let|var)\s+{re.escape(ident)}\b', content[:m.start()]):
                        line_no = self._offset_to_line(content, m.start())
                        violations.append(f"{path}:{line_no}: Stale closure risk in useCallback (empty `[]` while referencing `{ident}` from outer scope)")
                        break
                if violations and violations[-1].startswith(f"{path}:"):
                    break

        if violations:
            return {
                "name": "React Hooks & Closure Safety",
                "status": "FAILED",
                "is_good": False,
                "category": "React / Hooks",
                "violations": violations[:3],
                "details": "; ".join(violations[:3])
            }
        return {
            "name": "React Hooks & Closure Safety",
            "status": "PASSED",
            "is_good": True,
            "category": "React / Hooks",
            "details": "Hook dependencies are correctly declared; no stale closures."
        }

    def _check_resource_cleanup(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        violations = []
        for path, content in contents.items():
            if not path.endswith(('.jsx', '.tsx', '.js', '.ts')):
                continue
            # Look for useEffect containing setInterval/addEventListener without clearInterval/removeEventListener
            for m in re.finditer(r'useEffect\s*\(\s*\(\s*\)\s*=>\s*\{([\s\S]*?)\}\s*,\s*\[.*?\]\s*\)', content):
                body = m.group(1)
                line_no = self._offset_to_line(content, m.start())
                if 'setInterval' in body and 'clearInterval' not in body:
                    violations.append(f"{path}:{line_no}: `setInterval` in `useEffect` missing `clearInterval` cleanup return")
                if 'addEventListener' in body and 'removeEventListener' not in body:
                    violations.append(f"{path}:{line_no}: `addEventListener` missing corresponding `removeEventListener` cleanup")

        if violations:
            return {
                "name": "Resource & Timer Cleanup",
                "status": "FAILED",
                "is_good": False,
                "category": "React / Lifecycle",
                "violations": violations[:3],
                "details": "; ".join(violations[:3])
            }
        return {
            "name": "Resource & Timer Cleanup",
            "status": "PASSED",
            "is_good": True,
            "category": "React / Lifecycle",
            "details": "Side-effects and subscriptions clean up properly to avoid memory leaks."
        }

    def _check_virtual_dom_safety(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        violations = []
        for path, diff in diffs.items():
            if not path.endswith(('.jsx', '.tsx')):
                continue
            for line_no, raw_code in self._get_diff_additions(diff):
                code = raw_code.strip()
                # Direct DOM queries
                if re.search(r'\bdocument\.(?:getElementById|querySelector|getElementsByClassName)\b', code):
                    violations.append(f"{path}:{line_no}: Direct DOM access via `document.*` bypasses React Virtual DOM")
                # Random keys
                if 'Math.random()' in code and 'key=' in code:
                    violations.append(f"{path}:{line_no}: Unstable key generator `key={{...Math.random()}}` degrades reconciliation")

        if violations:
            return {
                "name": "Virtual DOM & Reconciliation Safety",
                "status": "FAILED",
                "is_good": False,
                "category": "React / Architecture",
                "violations": violations[:3],
                "details": "; ".join(violations[:3])
            }
        return {
            "name": "Virtual DOM & Reconciliation Safety",
            "status": "PASSED",
            "is_good": True,
            "category": "React / Architecture",
            "details": "Rendering respects React Virtual DOM; uses stable reconciliation keys."
        }

    def _check_python_mutable_defaults(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        """A2.1: `def f(x=[])` / `def f(x={})` — the classic shared-state foot-gun."""
        violations = []
        for path, diff in diffs.items():
            if not path.endswith('.py'):
                continue
            for line_no, raw_code in self._get_diff_additions(diff):
                code = raw_code.strip()
                if re.search(r'\bdef\s+\w+\s*\([^)]*=\s*(?:\[\s*\]|\{\s*\})', code):
                    violations.append(f"{path}:{line_no}: Mutable default argument (`[]`/`{{}}`) is shared across calls; use `None` + create inside")

        return self._result("Python Mutable Default Arguments", violations,
                            "Python / Errors", "Default arguments are immutable; no shared mutable state across calls.")

    def _check_go_error_handling(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        """A2.2: assignments that drop the error (`_ = err`, or `v, _ := ...`)."""
        violations = []
        for path, diff in diffs.items():
            if not path.endswith('.go'):
                continue
            for line_no, raw_code in self._get_diff_additions(diff):
                code = raw_code.strip()
                # `_ = err` / `_, _ = f()` explicit discards of an error variable
                if re.search(r'^\s*(?:_\s*,\s*)?_\s*(?::?=)\s*\w*(?:[Ee]rr|error)\w*\s*$', code):
                    violations.append(f"{path}:{line_no}: Explicitly discarded error variable (`_ = err`) — handle or document why safe")
                # `x, _ := f(...)` where f conventionally returns (T, error)
                if re.search(r'^\s*[\w\.\[\]]+\s*,\s*_\s*:?=\s*\w+\(', code):
                    violations.append(f"{path}:{line_no}: Error return value discarded (`{code[:40]}`) — check it or wrap with a comment")

        return self._result("Go Error Handling", violations,
                            "Go / Errors", "Error values are checked, not discarded.")

    # -------------------------------------------------------------------------
    # Complexity (A3) — scoped to functions this commit touched
    # -------------------------------------------------------------------------

    def _changed_functions(self, diffs: Dict[str, str], contents: Dict[str, str]):
        """Yield (path, name, [body lines]) for each function this commit touched.

        Uses the Python `ast` module for Python (exact spans) and
        language_support's line-anchored definition matching for the
        JS/TS-family via each path's detected language. Only functions whose
        span overlaps an added line are included — a 1-line edit to a legacy
        file never flags untouched functions.
        """
        from .language_support import extract_functions, language_for
        for path, content in contents.items():
            if not content.strip():
                continue
            diff = diffs.get(path, "")
            added_set = {line_no for line_no, _ in self._get_diff_additions(diff)}
            if not added_set:
                continue
            language = language_for(path)
            if language == "python":
                import ast as _ast
                try:
                    tree = _ast.parse(content)
                except Exception:
                    continue
                lines = content.splitlines()
                for node in _ast.walk(tree):
                    if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                        end = getattr(node, "end_lineno", node.lineno)
                        if set(range(node.lineno, end + 1)) & added_set:
                            yield path, node.name, lines[node.lineno - 1:end]
            elif language:
                try:
                    funcs = extract_functions(content, language)
                except Exception:
                    continue
                lines = content.splitlines()
                for fn in funcs:
                    start, end = fn.get("start_line", 0), fn.get("end_line", 0)
                    if not start or not end:
                        continue
                    if set(range(start, end + 1)) & added_set:
                        yield path, fn.get("name", "?"), lines[start - 1:end]

    def _check_function_length(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        """A3.2: changed functions exceeding ~80 LoC. Never whole-file LOC."""
        violations = []
        for path, name, body_lines in self._changed_functions(diffs, contents):
            if len(body_lines) > 80:
                violations.append(f"{path}: {name}() grew to {len(body_lines)} lines (>80) — split into focused units")

        return self._result("Function Length (changed)", violations,
                            "Complexity", "Changed functions stay within a reviewable length (≤80 LoC).")

    def _check_nesting_depth(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        """A3.1: added lines nested deeper than 4 levels inside changed functions."""
        violations = []
        for path, name, body_lines in self._changed_functions(diffs, contents):
            diff = diffs.get(path, "")
            added_line_nos = {line_no for line_no, _ in self._get_diff_additions(diff)}
            deepest = 0
            for raw in body_lines:
                stripped = raw.lstrip()
                if not stripped or stripped.startswith(("#", "//", "*", "/*")):
                    continue
                # Indentation levels: 4 spaces or 1 tab per level; partial
                # indents (>=2 spaces) count as one more level.
                levels = 0
                for ch in raw:
                    if ch == ' ':
                        levels += 1
                    elif ch == '\t':
                        levels += 4
                    else:
                        break
                level = levels // 4 + (1 if levels % 4 >= 2 else 0)
                deepest = max(deepest, level)
            if deepest > 4:
                violations.append(f"{path}: {name}() nests {deepest} levels deep (>4) — flatten with early returns or extract helpers")

        return self._result("Nesting Depth (changed)", violations,
                            "Complexity", "Control flow stays flat (≤4 nesting levels) in changed code.")

    # -------------------------------------------------------------------------
    # Input validation — changed-function scoped (A0.2 fix)
    # -------------------------------------------------------------------------

    _INPUT_PARAM_PATTERN = re.compile(
        r'\b(?:user|request|req|params|query|body|payload|input|data|id|username|email)\b')
    _GUARD_PATTERN = re.compile(
        r'\b(?:if\s|guard\s|assert\s|zod|schema|validate|sanitize|escape|typeof|isinstance|\?\s*$|'
        r'if\s+not\s|raise\s|throw\s|return\s+(?:null|undefined|None|false|early))', re.IGNORECASE)

    def _check_input_validation(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        """A0.2: only changed functions that take external-looking input without guards fail.

        The old whole-file version passed on any `if`-statement anywhere and
        failed every non-trivial file; this one only flags functions this
        commit touched whose signature exposes a trust-boundary parameter and
        whose changed lines contain no guard/validation.
        """
        violations = []
        for path, name, body_lines in self._changed_functions(diffs, contents):
            signature = body_lines[0] if body_lines else ""
            if not self._INPUT_PARAM_PATTERN.search(signature):
                continue  # no trust-boundary parameter: nothing to demand
            diff = diffs.get(path, "")
            changed_code = "\n".join(
                raw for line_no, raw in self._get_diff_additions(diff))
            if self._GUARD_PATTERN.search(changed_code):
                continue  # a guard exists in the changed lines
            violations.append(
                f"{path}: {name}() takes external input (`{signature.strip()[:60]}`) with no validation/guard in the changed lines")

        return self._result("Input Validation & Boundary Guards", violations,
                            "Reliability",
                            "Changed functions that accept external input validate it at the boundary.")

    def _result(self, name: str, violations: List[str], category: str, pass_details: str) -> Dict[str, Any]:
        """Uniform check result: FAILED with violations, else PASSED with details."""
        if violations:
            return {
                "name": name,
                "status": "FAILED",
                "is_good": False,
                "category": category,
                "violations": violations[:3],
                "details": "; ".join(violations[:3]),
            }
        return {
            "name": name,
            "status": "PASSED",
            "is_good": True,
            "category": category,
            "details": pass_details,
        }

    def _check_variable_scoping(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        violations = []
        for path, diff in diffs.items():
            if not path.endswith(('.js', '.jsx', '.ts', '.tsx')):
                continue
            for line_no, raw_code in self._get_diff_additions(diff):
                code = raw_code.strip()
                if re.search(r'^\s*var\s+[a-zA-Z0-9_$]+', code):
                    violations.append(f"{path}:{line_no}: Legacy `var` keyword used; prefer block-scoped `const` or `let`")
                if 'window.' in code and '=' in code and not '==' in code:
                    violations.append(f"{path}:{line_no}: Global window pollution (`{code[:50]}`)")

        if violations:
            return {
                "name": "Modern Variable Scoping",
                "status": "FAILED",
                "is_good": False,
                "category": "JS / Code Quality",
                "violations": violations[:3],
                "details": "; ".join(violations[:3])
            }
        return {
            "name": "Modern Variable Scoping",
            "status": "PASSED",
            "is_good": True,
            "category": "JS / Code Quality",
            "details": "Block-scoped variables (`const`/`let`) cleanly managed without global leaks."
        }

    def _check_strict_equality(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        violations = []
        for path, diff in diffs.items():
            if not path.endswith(('.js', '.jsx', '.ts', '.tsx')):
                continue
            for line_no, raw_code in self._get_diff_additions(diff):
                code = raw_code.strip()
                # Check for loose equality == or != (not ===, !==, == null if intentional)
                if re.search(r'(?<![!=])==(?!=)', code) or re.search(r'(?<![!=])!=(?!=)', code):
                    violations.append(f"{path}:{line_no}: Loose equality operator (`==` or `!=`) used; prefer `===` and `!==`")

        if violations:
            return {
                "name": "Strict Equality & Type Safety",
                "status": "FAILED",
                "is_good": False,
                "category": "JS / Types",
                "violations": violations[:3],
                "details": "; ".join(violations[:3])
            }
        return {
            "name": "Strict Equality & Type Safety",
            "status": "PASSED",
            "is_good": True,
            "category": "JS / Types",
            "details": "Strict equality comparisons (`===`/`!==`) avoid coercion pitfalls."
        }

    def _check_error_handling(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        violations = []
        for path, content in contents.items():
            # Check for empty catch block in JS/TS (even if containing comments only)
            pat = r'(?:\.catch\s*\(\s*(?:\([^)]*\)|[a-zA-Z0-9_$]+)?\s*=>?\s*\{([\s\S]*?)\}\s*\)|catch\s*\([^)]*\)\s*\{([\s\S]*?)\})'
            for m in re.finditer(pat, content):
                body = m.group(1) if m.group(1) is not None else m.group(2)
                clean_body = re.sub(r'//.*', '', body)
                clean_body = re.sub(r'/\*[\s\S]*?\*/', '', clean_body).strip()
                if not clean_body:
                    line_no = self._offset_to_line(content, m.start())
                    violations.append(f"{path}:{line_no}: Empty `catch` block swallows errors silently without logging or propagation")
                    break

        if violations:
            return {
                "name": "Error Handling & Resilience",
                "status": "FAILED",
                "is_good": False,
                "category": "Reliability",
                "violations": violations[:3],
                "details": "; ".join(violations[:3])
            }
        return {
            "name": "Error Handling & Resilience",
            "status": "PASSED",
            "is_good": True,
            "category": "Reliability",
            "details": "Asynchronous operations and exceptions are explicitly handled."
        }

    def _check_python_exception_handling(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        violations = []
        for path, diff in diffs.items():
            if not path.endswith('.py'):
                continue
            for line_no, raw_code in self._get_diff_additions(diff):
                code = raw_code.strip()
                if re.match(r'except\s*:', code):
                    violations.append(f"{path}:{line_no}: Bare `except:` catches SystemExit and KeyboardInterrupt")

        if violations:
            return {
                "name": "Python Exception Specificity",
                "status": "FAILED",
                "is_good": False,
                "category": "Python / Errors",
                "violations": violations[:3],
                "details": "; ".join(violations[:3])
            }
        return {
            "name": "Python Exception Specificity",
            "status": "PASSED",
            "is_good": True,
            "category": "Python / Errors",
            "details": "Exception handlers declare specific error types."
        }

    def _check_python_resource_management(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        violations = []
        for path, diff in diffs.items():
            if not path.endswith('.py'):
                continue
            for line_no, raw_code in self._get_diff_additions(diff):
                code = raw_code.strip()
                if re.search(r'=\s*open\(', code) and 'with ' not in code:
                    violations.append(f"{path}:{line_no}: Unmanaged file descriptor `open(...)` outside `with` statement")

        if violations:
            return {
                "name": "Python Resource Management",
                "status": "FAILED",
                "is_good": False,
                "category": "Python / Resources",
                "violations": violations[:3],
                "details": "; ".join(violations[:3])
            }
        return {
            "name": "Python Resource Management",
            "status": "PASSED",
            "is_good": True,
            "category": "Python / Resources",
            "details": "I/O resources are safely managed via context managers (`with`)."
        }

    # Legacy whole-file checks (_check_component_modularity, whole-file
    # _check_input_validation) were removed — replaced by changed-function
    # scoped versions above. See PLAN.md A0.2/A3.2.

    # -------------------------------------------------------------------------
    # Terminal Presentation
    # -------------------------------------------------------------------------

    def print_report(self, results: List[Dict[str, Any]]):
        """Render a formatted, colorful coding standards report to the terminal."""
        if not results:
            return

        GREEN = "\033[92m"
        RED = "\033[91m"
        CYAN = "\033[96m"
        BOLD = "\033[1m"
        DIM = "\033[2m"
        RESET = "\033[0m"

        passed = [r for r in results if r["is_good"]]
        failed = [r for r in results if not r["is_good"]]

        print(f"\n\n{BOLD}{CYAN}Coding Standards Audit (Staged Changes){RESET}\n")

        # Print Failed (Not Good) first so developer immediately notices violations
        for r in failed:
            print(f"{RED}{BOLD}FAILED{RESET}  {BOLD}{r['name']:<35}{RESET}")
            violation_items = r.get("violations") or [r["details"]]
            for v in violation_items:
                print(f"         {DIM}↳ {v}{RESET}")

        # Print Passed (Good) standards
        for r in passed:
            print(f"{GREEN}{BOLD}PASSED{RESET}  {BOLD}{r['name']:<35}{RESET}")
            print(f"         {DIM}↳ {r['details']}{RESET}")

        pass_text = f"{GREEN}{BOLD}{len(passed)} Passed (Good){RESET}"
        fail_text = f"{RED}{BOLD}{len(failed)} Failed (Violations){RESET}" if failed else f"{GREEN}0 Violations{RESET}"
        print(f"{BOLD}\nSummary: {pass_text} │ {fail_text}\n")
        
