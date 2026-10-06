import subprocess
import ast
import os
from typing import List, Dict, Any, Set

from .language_support import (
    language_for,
    functions_touching_lines as generic_functions_touching_lines,
    extract_functions as generic_extract_functions,
)


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

            if status == 'D':
                continue

            language = language_for(path)
            if not language:
                # Not a recognized code file — still tracked as a change (docs, config,
                # SQL, notebooks get language tags; truly unknown extensions are skipped)
                continue

            changed_funcs = self._analyze_changes(path, status, language)
            files.append({
                "path": path,
                "status": "modified" if status == 'M' else "added",
                "language": language,
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

    def _analyze_changes(self, filepath: str, status: str, language: str) -> List[Dict[str, Any]]:
        """Identify functions changed (added, modified, or deleted), any language."""
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
            if language == "python":
                funcs = self._all_functions(new_content)
                if not funcs and new_content.strip():
                    return [{"name": "<module>", "change_type": "added", "added_calls": []}]
                return funcs
            generic = generic_extract_functions(new_content, language)
            if not generic and new_content.strip():
                return [{"name": "<module>", "change_type": "added", "added_calls": []}]
            return [{"name": g["name"], "change_type": "added",
                     "added_calls": []} for g in generic]

        # For modified files, filter by changed line numbers in git diff
        changed_lines = self._get_changed_line_numbers(filepath)
        if language == "python":
            touched = self._functions_touching_lines(new_content, changed_lines)
        else:
            touched = generic_functions_touching_lines(new_content, language, changed_lines)
        if touched:
            return touched

        # Check for deleted functions
        if language != "python":
            old_fn = {g["name"] for g in generic_extract_functions(old_content, language)}
            new_fn = {g["name"] for g in generic_extract_functions(new_content, language)}
            deleted_names = old_fn - new_fn
            if deleted_names:
                return [{"name": n, "change_type": "deleted", "added_calls": []} for n in deleted_names]
            # No exact function match — treat as module-level change
            if new_content.strip():
                return [{"name": "<module>", "change_type": "modified", "added_calls": []}]
            return []

        old_names = {f["name"] for f in self._all_functions(old_content)}
        new_names = {f["name"] for f in self._all_functions(new_content)}
        deleted = old_names - new_names
        if deleted:
            return [{"name": n, "change_type": "deleted", "added_calls": []} for n in deleted]

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
