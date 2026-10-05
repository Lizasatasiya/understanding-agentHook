import subprocess
import ast
import os
import re
from typing import List, Dict, Any, Set
from .environment_detector import _EXTENSION_MAP

_SUPPORTED_EXTENSIONS = set(_EXTENSION_MAP.keys()) | {
    ".html", ".css", ".scss", ".json", ".yaml", ".yml",
    ".sql", ".graphql", ".prisma", ".proto", ".h", ".hpp", ".md"
}

_IGNORED_FILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
}


class ChangeDetector:
    def detect(self) -> Dict[str, Any]:
        try:
            diff_output = subprocess.check_output(
                ["git", "diff", "--cached", "--name-status"],
                text=True
            ).strip()
        except subprocess.CalledProcessError:
            return {"files": [], "stats": {}}

        if not diff_output:
            return {"files": [], "stats": {}}

        files = []
        for line in diff_output.split('\n'):
            if not line:
                continue
            parts = line.split('\t')
            status = parts[0]
            path = parts[-1]

            ext = os.path.splitext(path)[1].lower()
            filename = os.path.basename(path)

            if filename in _IGNORED_FILES or (ext not in _SUPPORTED_EXTENSIONS and not path.endswith('.py')):
                continue

            lang = _EXTENSION_MAP.get(ext, "unknown")

            if status == 'D':
                changed_funcs = [{"name": "<module>", "change_type": "deleted", "added_calls": []}]
                file_status = "deleted"
            elif ext == '.py':
                changed_funcs = self._analyze_ast_changes(path, status)
                file_status = "modified" if status == 'M' else "added"
            elif ext in ('.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts'):
                changed_funcs = self._analyze_js_ts_changes(path, status)
                file_status = "modified" if status == 'M' else "added"
            else:
                changed_funcs = [{"name": "<module>", "change_type": "modified" if status == 'M' else "added", "added_calls": []}]
                file_status = "modified" if status == 'M' else "added"

            if not changed_funcs and status != 'D':
                changed_funcs = [{"name": "<module>", "change_type": file_status, "added_calls": []}]

            files.append({
                "path": path,
                "status": file_status,
                "language": lang,
                "changed_functions": changed_funcs
            })

        # Calculate commit-level scale metrics
        total_added = 0
        total_deleted = 0
        try:
            numstat = subprocess.check_output(
                ["git", "diff", "--cached", "--numstat"],
                text=True, stderr=subprocess.DEVNULL
            ).strip()
            for n_line in numstat.split('\n'):
                if n_line.strip():
                    parts = n_line.split()
                    if len(parts) >= 2:
                        try:
                            total_added += int(parts[0]) if parts[0] != '-' else 0
                            total_deleted += int(parts[1]) if parts[1] != '-' else 0
                        except ValueError:
                            pass
        except Exception:
            pass

        total_loc = total_added + total_deleted
        total_changed_funcs = sum(len(f.get("changed_functions", [])) for f in files)
        is_large_change = (total_loc > 80) or (total_changed_funcs > 3) or (len(files) > 2)

        return {
            "files": files,
            "stats": {
                "total_added": total_added,
                "total_deleted": total_deleted,
                "total_loc": total_loc,
                "total_changed_functions": total_changed_funcs,
                "is_large_change": is_large_change
            }
        }

    def _analyze_ast_changes(self, filepath: str, status: str) -> List[Dict[str, Any]]:
        """
        Identify only the functions that were actually changed (added, modified, or deleted).
        """
        try:
            new_content = subprocess.check_output(
                ["git", "show", f":{filepath}"], text=True, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            new_content = ""

        try:
            old_content = subprocess.check_output(
                ["git", "show", f"HEAD:{filepath}"], text=True, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            old_content = ""

        if status == 'added':
            funcs = self._all_functions(new_content)
            if not funcs and new_content.strip():
                return [{"name": "<module>", "change_type": "added", "added_calls": []}]
            return funcs

        # For modified files, filter specifically by changed line numbers in git diff
        changed_lines = self._get_changed_line_numbers(filepath)
        touched = self._functions_touching_lines(new_content, changed_lines)
        if touched:
            return touched

        # Check if functions were deleted
        old_funcs = self._all_functions(old_content)
        new_funcs = self._all_functions(new_content)
        new_names = {f["name"] for f in new_funcs}
        deleted = [f for f in old_funcs if f["name"] not in new_names]
        if deleted:
            for d in deleted:
                d["change_type"] = "deleted"
            return deleted

        # If modified lines don't fall neatly into an existing function, mark as module-level change
        if new_content.strip():
            return [{"name": "<module>", "change_type": "modified", "added_calls": []}]

        return []

   
    # Helpers


    def _get_changed_line_numbers(self, filepath: str) -> Set[int]:
        """
        Parse `git diff --cached -U0 <file>` to collect the line numbers
        (in the NEW file) that were added or modified.
        """
        try:
            diff = subprocess.check_output(
                ["git", "diff", "--cached", "-U0", filepath],
                text=True
            )
        except subprocess.CalledProcessError:
            return set()

        changed = set()
        for line in diff.split('\n'):
            # Hunk header e.g. @@ -5,3 +6,4 @@ or @@ -5 +6 @@
            if line.startswith('@@'):
                try:
                    plus_part = line.split('+')[1].split('@@')[0].strip()
                    if ',' in plus_part:
                        start, count = plus_part.split(',')
                        start, count = int(start), int(count)
                    else:
                        start, count = int(plus_part), 1
                    for ln in range(start, start + max(1, count)):
                        changed.add(ln)
                except (ValueError, IndexError):
                    pass
        return changed

    def _all_functions(self, source: str) -> List[Dict[str, Any]]:
        """Return every function definition in source."""
        if not source:
            return []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        result = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                calls = [
                    n.func.id for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                ]
                result.append({
                    "name": node.name,
                    "change_type": "added",
                    "added_calls": calls
                })
        return result

    def _functions_touching_lines(
        self, source: str, changed_lines: Set[int]
    ) -> List[Dict[str, Any]]:
        """
        Return only functions whose line range overlaps with changed_lines.
        """
        if not source or not changed_lines:
            return []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        result = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end_lineno = getattr(node, "end_lineno", node.lineno)
                func_lines = set(range(node.lineno, end_lineno + 1))
                if func_lines & changed_lines:  # intersection — lines overlap
                    calls = [
                        n.func.id for n in ast.walk(node)
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    ]
                    result.append({
                        "name": node.name,
                        "change_type": "modified",
                        "added_calls": calls
                    })
        return result

    def _analyze_js_ts_changes(self, filepath: str, status: str) -> List[Dict[str, Any]]:
        try:
            new_content = subprocess.check_output(
                ["git", "show", f":{filepath}"], text=True, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            new_content = ""

        try:
            old_content = subprocess.check_output(
                ["git", "show", f"HEAD:{filepath}"], text=True, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            old_content = ""

        if status == 'added':
            funcs = self._extract_js_ts_functions(new_content, "added")
            if not funcs and new_content.strip():
                return [{"name": "<module>", "change_type": "added", "added_calls": []}]
            return funcs

        changed_lines = self._get_changed_line_numbers(filepath)
        touched = self._extract_js_ts_touching_lines(new_content, changed_lines)
        if touched:
            return touched

        old_funcs = self._extract_js_ts_functions(old_content, "deleted")
        new_funcs = self._extract_js_ts_functions(new_content, "added")
        new_names = {f["name"] for f in new_funcs}
        deleted = [f for f in old_funcs if f["name"] not in new_names]
        if deleted:
            return deleted

        if new_content.strip():
            return [{"name": "<module>", "change_type": "modified", "added_calls": []}]

        return []

    def _extract_js_ts_blocks(self, source: str) -> List[Dict[str, Any]]:
        if not source:
            return []
        lines = source.splitlines()
        patterns = [
            # Standard functions & async functions
            re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([a-zA-Z0-9_$]+)'),
            # Arrow functions & assigned functions
            re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+([a-zA-Z0-9_$]+)\s*(?::\s*[^=]+)?\s*=\s*(?:async\s*)?(?:\([^)]*\)|[a-zA-Z0-9_$]+)?\s*(?::\s*[^=]+)?\s*=>'),
            re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?function'),
            # Classes & NestJS / Angular controllers / services
            re.compile(r'^\s*(?:export\s+)?(?:default\s+)?class\s+([a-zA-Z0-9_$]+)'),
            # Class methods
            re.compile(r'^\s*(?:public|private|protected|async|static|\s)*([a-zA-Z0-9_$]+)\s*\([^)]*\)\s*(?::\s*[^;{]+)?\s*\{'),
        ]
        ignored = {'if', 'for', 'while', 'switch', 'catch', 'constructor', 'finally', 'with'}
        blocks = []
        for i, line in enumerate(lines, 1):
            for pat in patterns:
                m = pat.search(line)
                if m:
                    name = m.group(1)
                    if name not in ignored:
                        start_line = i
                        brace_count = 0
                        found_brace = False
                        end_line = start_line
                        for j in range(i - 1, len(lines)):
                            l = lines[j]
                            brace_count += l.count('{') - l.count('}')
                            if '{' in l:
                                found_brace = True
                            if found_brace and brace_count <= 0:
                                end_line = j + 1
                                break
                        if end_line == start_line and not found_brace:
                            end_line = min(len(lines), start_line + 5)

                        block_source = "\n".join(lines[start_line - 1 : end_line])
                        raw_calls = re.findall(r'\b([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\(', block_source)
                        calls = [c for c in raw_calls if c not in ignored and c != name]

                        blocks.append({
                            'name': name,
                            'start': start_line,
                            'end': end_line,
                            'calls': calls
                        })
                    break
        return blocks

    def _extract_js_ts_functions(self, source: str, change_type: str) -> List[Dict[str, Any]]:
        blocks = self._extract_js_ts_blocks(source)
        return [
            {
                "name": b["name"],
                "change_type": change_type,
                "added_calls": b["calls"]
            }
            for b in blocks
        ]

    def _extract_js_ts_touching_lines(self, source: str, changed_lines: Set[int]) -> List[Dict[str, Any]]:
        blocks = self._extract_js_ts_blocks(source)
        result = []
        for b in blocks:
            func_lines = set(range(b["start"], b["end"] + 1))
            if func_lines & changed_lines:
                result.append({
                    "name": b["name"],
                    "change_type": "modified",
                    "added_calls": b["calls"]
                })
        return result
