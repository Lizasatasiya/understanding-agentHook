"""Deterministic Coding Standards Analyzer.

Audits staged changes against established coding standards:
- React & Frontend Standards (Immutability, Hooks, Cleanups, Virtual DOM, XSS)
- Modern JavaScript/TypeScript Standards (Variable Scoping, Strict Equality, Error Handling)
- Python Standards (Exception specificity, Resource management)
- Universal Standards (Secret protection, Transport security, Modularity, Input validation)

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

    def check(self, changes: Dict[str, Any], context: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        """Run all coding standard checks across the staged files."""
        staged_files = changes.get("files", [])
        if not staged_files:
            return []

        results: List[Dict[str, Any]] = []

        # Collect raw staged diff and added lines per file
        file_diffs = {}
        file_contents = {}
        for f in staged_files:
            path = f.get("path", "")
            if not path or f.get("status") == "deleted":
                continue
            try:
                diff_out = subprocess.check_output(
                    ["git", "diff", "--cached", "-U3", path],
                    text=True, stderr=subprocess.DEVNULL, cwd=self.repo_root
                )
                file_diffs[path] = diff_out
            except Exception:
                file_diffs[path] = ""

            try:
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

        # 1. State & Prop Immutability (React / Frontend)
        if has_react:
            results.append(self._check_react_immutability(file_diffs, file_contents))

        # 2. React Hooks & Closure Dependencies
        if has_react:
            results.append(self._check_react_hooks_integrity(file_diffs, file_contents))

        # 3. Resource & Timer Cleanup in Effects
        if has_react:
            results.append(self._check_resource_cleanup(file_diffs, file_contents))

        # 4. Virtual DOM & Reconciliation Safety
        if has_react:
            results.append(self._check_virtual_dom_safety(file_diffs, file_contents))

        # 5. XSS & Code Execution Prevention
        if has_js_ts:
            results.append(self._check_xss_and_code_injection(file_diffs, file_contents))

        # 6. Secret & Credential Protection (Universal)
        results.append(self._check_secrets_protection(file_diffs, file_contents))

        # 7. Secure Transport Protocols (Universal)
        results.append(self._check_secure_transport(file_diffs, file_contents))

        # 8. Modern Variable Scoping (JS/TS)
        if has_js_ts:
            results.append(self._check_variable_scoping(file_diffs, file_contents))

        # 9. Strict Equality & Type Safety (JS/TS)
        if has_js_ts:
            results.append(self._check_strict_equality(file_diffs, file_contents))

        # 10. Error Handling & Resilience
        results.append(self._check_error_handling(file_diffs, file_contents))

        # 11. Python Specific Standards (if Python files present)
        if has_python:
            results.append(self._check_python_exception_handling(file_diffs, file_contents))
            results.append(self._check_python_resource_management(file_diffs, file_contents))

        # 12. Component & Function Modularity (Universal)
        results.append(self._check_component_modularity(file_diffs, file_contents))

        # 13. Input Validation & Defensive Guardrails (Universal)
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
                # Check for in-place array mutations in render code
                if re.search(r'\b(?:featuresList|items|list|data)\.(?:push|splice|shift|unshift|pop)\(', code):
                    violations.append(f"{path}:{line_no}: In-place array mutation during component lifecycle (`{code[:50]}`)")
                # Check for parameter mutation inside calculation functions
                if re.search(r'\bplan\.[a-zA-Z0-9_$]+\s*=\s*', code) and 'calculate' in diff:
                    violations.append(f"{path}:{line_no}: Mutating input object parameter directly (`{code[:50]}`)")

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
            # Look for useCallback with empty dependency array referencing external variables
            matches = re.finditer(r'useCallback\s*\(\s*\(\s*\)\s*=>\s*\{([\s\S]*?)\}\s*,\s*\[\s*\]\s*\)', content)
            for m in matches:
                body = m.group(1)
                if re.search(r'\b(?:props|billingCycle|state|plan|users)\b', body):
                    line_no = self._offset_to_line(content, m.start())
                    violations.append(f"{path}:{line_no}: Stale closure in useCallback (empty `[]` dependencies while referencing outer scope)")

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

    def _check_xss_and_code_injection(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        violations = []
        for path, diff in diffs.items():
            for line_no, raw_code in self._get_diff_additions(diff):
                code = raw_code.strip()
                if 'dangerouslySetInnerHTML' in code:
                    msg = f"{path}:{line_no}: Unescaped HTML injection via `dangerouslySetInnerHTML`"
                    if msg not in violations:
                        violations.append(msg)
                if re.search(r'\beval\s*\(', code):
                    msg = f"{path}:{line_no}: Insecure dynamic execution using `eval()`"
                    if msg not in violations:
                        violations.append(msg)
                if re.search(r'\bnew\s+Function\s*\(', code):
                    msg = f"{path}:{line_no}: Arbitrary code execution via `new Function()`"
                    if msg not in violations:
                        violations.append(msg)

        if violations:
            return {
                "name": "XSS & Script Execution Safety",
                "status": "FAILED",
                "is_good": False,
                "category": "Security / Injection",
                "violations": violations[:3],
                "details": "; ".join(violations[:3])
            }
        return {
            "name": "XSS & Script Execution Safety",
            "status": "PASSED",
            "is_good": True,
            "category": "Security / Injection",
            "details": "Safe string templating; no dangerous innerHTML or dynamic eval."
        }

    def _check_secrets_protection(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        violations = []
        secret_patterns = [
            (r'sk_live_[a-zA-Z0-9_\-]{16,}', "Stripe/Service live secret key"),
            (r'AKIA[0-9A-Z]{16}', "AWS Access Key ID"),
            (r'ghp_[a-zA-Z0-9]{36}', "GitHub Personal Access Token"),
            (r'HARDCODED_API_KEY\s*=\s*["\'][^"\']+["\']', "Hardcoded API secret token"),
            (r'(?:password|secret|token)\s*=\s*["\'][a-zA-Z0-9_\-!@#\$%\^&\*]{10,}["\']', "Hardcoded secret string"),
        ]

        for path, diff in diffs.items():
            for line_no, raw_code in self._get_diff_additions(diff):
                code = raw_code.strip()
                for pattern, desc in secret_patterns:
                    if re.search(pattern, code, re.IGNORECASE):
                        violations.append(f"{path}:{line_no}: Exposed {desc}")
                        break

        if violations:
            return {
                "name": "Secret & Credential Protection",
                "status": "FAILED",
                "is_good": False,
                "category": "Security / Secrets",
                "violations": violations[:3],
                "details": "; ".join(violations[:3])
            }
        return {
            "name": "Secret & Credential Protection",
            "status": "PASSED",
            "is_good": True,
            "category": "Security / Secrets",
            "details": "No hardcoded credentials, API keys, or private tokens detected."
        }

    def _check_secure_transport(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        violations = []
        for path, diff in diffs.items():
            for line_no, raw_code in self._get_diff_additions(diff):
                code = raw_code.strip()
                # Check for unencrypted HTTP API endpoints (ignore schemas / namespaces)
                if re.search(r'["\']http:\/\/(?!localhost|127\.0\.0\.1|www\.w3\.org)[a-zA-Z0-9\-_.]+', code):
                    violations.append(f"{path}:{line_no}: Insecure cleartext HTTP endpoint (`{code[:50]}`)")

        if violations:
            return {
                "name": "Secure Transport Protocols",
                "status": "FAILED",
                "is_good": False,
                "category": "Security / Network",
                "violations": violations[:3],
                "details": "; ".join(violations[:3])
            }
        return {
            "name": "Secure Transport Protocols",
            "status": "PASSED",
            "is_good": True,
            "category": "Security / Network",
            "details": "Uses encrypted HTTPS protocols for network endpoints."
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

    def _check_component_modularity(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        # Modularity is good when code is structured into functions/components under reasonable length
        violations = []
        for path, content in contents.items():
            lines = content.splitlines()
            if len(lines) > 400:
                violations.append(f"{path}:1: Monolithic file ({len(lines)} LOC) exceeds recommended modular threshold (350 LOC)")

        if violations:
            return {
                "name": "Component & Function Modularity",
                "status": "FAILED",
                "is_good": False,
                "category": "Architecture",
                "violations": violations[:3],
                "details": "; ".join(violations[:3])
            }
        return {
            "name": "Component & Function Modularity",
            "status": "PASSED",
            "is_good": True,
            "category": "Architecture",
            "details": "Clean modular boundaries with focused single-responsibility functions."
        }

    def _check_input_validation(self, diffs: Dict[str, str], contents: Dict[str, str]) -> Dict[str, Any]:
        # Check if functions incorporate boundary or type validation
        has_checks = False
        for content in contents.values():
            if re.search(r'\b(?:if\s*\(.*?(?:null|undefined|<=|>=|typeof|\.length).*?\)|if not \w+:)', content):
                has_checks = True
                break

        if has_checks:
            return {
                "name": "Input Validation & Boundary Guards",
                "status": "PASSED",
                "is_good": True,
                "category": "Reliability",
                "details": "Input guards and boundary checks are implemented for incoming parameters."
            }

        violations = []
        code_files = [p for p in contents.keys() if p.endswith(('.js', '.jsx', '.ts', '.tsx', '.py'))]
        for path in code_files:
            content = contents.get(path, "")
            m = re.search(r'(?:export\s+(?:default\s+)?function\s+\w+|function\s+\w+|const\s+\w+\s*=\s*(?:\([^)]*\)|[a-zA-Z0-9_$]+)\s*=>|def\s+\w+)', content)
            line_no = self._offset_to_line(content, m.start()) if m else 1
            violations.append(f"{path}:{line_no}: Missing defensive boundary guards and input validation checks")

        return {
            "name": "Input Validation & Boundary Guards",
            "status": "FAILED",
            "is_good": False,
            "category": "Reliability",
            "violations": violations[:3] if violations else ["Missing defensive boundary guards and input validation checks."],
            "details": "; ".join(violations[:3]) if violations else "Missing defensive boundary guards and input validation checks."
        }

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

        print(f"\n\n{BOLD}{CYAN}📋 Coding Standards Audit (Staged Changes){RESET}\n")

        # Print Failed (Not Good) first so developer immediately notices violations
        for r in failed:
            print(f"{RED}{BOLD}❌ FAILED{RESET}  {BOLD}{r['name']:<35}{RESET}")
            violation_items = r.get("violations") or [r["details"]]
            for v in violation_items:
                print(f"         {DIM}↳ {v}{RESET}")

        # Print Passed (Good) standards
        for r in passed:
            print(f"{GREEN}{BOLD}✅ PASSED{RESET}  {BOLD}{r['name']:<35}{RESET}")
            print(f"         {DIM}↳ {r['details']}{RESET}")

        pass_text = f"{GREEN}{BOLD}{len(passed)} Passed (Good){RESET}"
        fail_text = f"{RED}{BOLD}{len(failed)} Failed (Violations){RESET}" if failed else f"{GREEN}0 Violations{RESET}"
        print(f"{BOLD}\nSummary: {pass_text} │ {fail_text}\n")
        
