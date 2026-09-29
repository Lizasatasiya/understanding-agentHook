import subprocess
import ast
import os
from typing import List, Dict, Any, Set


class ChangeDetector:
    def detect(self) -> Dict[str, Any]:

        try:
            diff_output = subprocess.check_output(
                ["git", "diff", "--cached", "--name-status"],
                text=True
            ).strip()
        except subprocess.CalledProcessError:
            return {"files": []}

        if not diff_output:
            return {"files": []}

        files = []
        for line in diff_output.split('\n'):
            if not line:
                continue
            parts = line.split('\t')
            status = parts[0]
            path = parts[-1]

            if not path.endswith('.py') or status == 'D':
                continue

            files.append({
                "path": path,
                "status": "modified" if status == 'M' else "added",
                "language": "python",
                "changed_functions": self._analyze_ast_changes(path, status)
            })

        return {"files": files}

    def _analyze_ast_changes(self, filepath: str, status: str) -> List[Dict[str, Any]]:
        """
        Identify the functions that were changed (added, modified, or deleted).
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

        # For brand-new files, old_content is empty, so this only returns new functions.
        # For deletions, new_content is empty, so this only returns old functions.
        new_funcs = self._all_functions(new_content)
        old_funcs = self._all_functions(old_content)
        
        seen = set()
        result = []
        for f in new_funcs + old_funcs:
            if f["name"] not in seen:
                seen.add(f["name"])
                result.append(f)
                
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
                    # Extract the +<start>[,<count>] part
                    plus_part = line.split('+')[1].split('@@')[0].strip()
                    if ',' in plus_part:
                        start, count = plus_part.split(',')
                        start, count = int(start), int(count)
                    else:
                        start, count = int(plus_part), 1
                    for ln in range(start, start + count):
                        changed.add(ln)
                except (ValueError, IndexError):
                    pass
        return changed

    def _all_functions(self, source: str) -> List[Dict[str, Any]]:
        """Return every function definition in source (used for new files)."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        result = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
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
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        result = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_lines = set(range(node.lineno, node.end_lineno + 1))
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
