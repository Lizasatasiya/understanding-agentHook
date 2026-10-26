"""Deterministic Formatting & Trivial Changes Detector.

Identifies code changes that are purely cosmetic/formatting or import reorganization
and carry zero semantic or logic impact:

Category 1: Formatting, Whitespace & Styling
1. Whitespace & indentation: trailing whitespace, tabs-to-spaces, indentation fixes,
   or adding/removing empty blank lines.
2. Line wrapping & bracket breaks: splitting long arguments across multiple lines
   or formatting according to formatters (black, prettier, gofmt).
3. Quote normalization: switching between single quotes and double quotes without
   altering string content.
4. Trailing commas: adding or removing trailing commas in lists, dictionaries,
   tuples, or parameter lists.
5. Delimiter adjustments: adding or removing optional semicolons (e.g. JS/TS).

Category 2: Imports & Module Organization
6. Import reordering & grouping: alphabetizing imports or re-sorting by category
   (standard library vs third-party vs local modules, like isort or ruff).
7. Multi-line vs single-line imports: changing from single-line imports to multi-line
   wrapped imports without altering the symbols imported.
8. Dead/unused import removal: removing unreferenced imports flagged by linters.
9. Aliasing for convention: routine alias adjustments matching standard conventions
   (e.g., import numpy as np, import pandas as pd).

Such changes carry no semantic risk and do not require developer questioning
during pre-commit.
"""

import ast
import json
import os
import re
import subprocess
from typing import Dict, Any, Set, Tuple, List, Optional


CONVENTIONAL_ALIASES = {
    "numpy": "np",
    "pandas": "pd",
    "matplotlib.pyplot": "plt",
    "seaborn": "sns",
    "tensorflow": "tf",
    "torch.nn": "nn",
    "torch.nn.functional": "F",
    "datetime": "dt",
    "typing": "t",
    "scipy": "sp",
}


def canonicalize_string(raw: str) -> str:
    """Normalize single vs double quoted strings if their content is identical."""
    if (raw.startswith('"""') and raw.endswith('"""')) or (raw.startswith("'''") and raw.endswith("'''")):
        inner = raw[3:-3]
        return '"""' + inner + '"""'

    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        quote_char = raw[0]
        inner = raw[1:-1]
        # Unescape the string's own delimiter so 'hello \'world\'' and "hello 'world'" match
        if quote_char == "'":
            content = inner.replace(r"\'", "'")
        else:
            content = inner.replace(r'\"', '"')
        return json.dumps(content)

    return raw


def normalize_code(code: str, language: str = "") -> str:
    """
    Produce a canonical, token-normalized representation of code where whitespace,
    line wrapping, quote normalization, trailing commas, and optional semicolons
    are unified.
    """
    code = code.replace("\r\n", "\n").replace("\r", "\n")

    token_pattern = re.compile(
        r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\''
        r'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\''
        r'|`[\s\S]*?`'
        r'|/\*[\s\S]*?\*/'
        r'|//[^\n]*'
        r'|#[^\n]*)'
    )

    parts = []
    last_idx = 0
    for match in token_pattern.finditer(code):
        code_slice = code[last_idx:match.start()]
        if code_slice:
            parts.append(('code', code_slice))

        tok = match.group(0)
        if tok.startswith(('"', "'")):
            parts.append(('string', canonicalize_string(tok)))
        elif tok.startswith('`'):
            parts.append(('string', tok))
        elif tok.startswith(('/*', '//', '#')):
            comment_norm = re.sub(r'\s+', ' ', tok).strip()
            parts.append(('comment', comment_norm))

        last_idx = match.end()

    code_slice = code[last_idx:]
    if code_slice:
        parts.append(('code', code_slice))

    normalized = []
    for kind, text in parts:
        if kind in ('string', 'comment'):
            normalized.append(text)
        else:
            # Trailing commas immediately before ), ], or }
            text = re.sub(r',\s*([\)\]\}])', r'\1', text)
            # Statement-terminating optional semicolons before newline, }, or end
            text = re.sub(r';+\s*(?=[\n\}]|$)', r'', text)
            # Collapse whitespace around punctuation and operators
            text = re.sub(r'\s*([()\[\]\{\},:;=+\-*/%&|^!<>?~])\s*', r'\1', text)
            # Collapse remaining whitespace
            text = re.sub(r'\s+', ' ', text)
            normalized.append(text)

    return ''.join(normalized).strip()


def is_pure_formatting_change(old_content: str, new_content: str, filepath: str = "") -> bool:
    """Check if the diff is purely Category 1 (formatting, whitespace, quotes, commas)."""
    if old_content == new_content:
        return True

    if not old_content or not new_content:
        return False

    is_python = filepath.endswith('.py') or ('def ' in old_content and 'import ' in old_content)
    if is_python:
        try:
            tree_old = ast.parse(old_content)
            tree_new = ast.parse(new_content)
            if ast.dump(tree_old, include_attributes=False) == ast.dump(tree_new, include_attributes=False):
                old_comments = [re.sub(r'\s+', ' ', c).strip() for c in re.findall(r'#[^\n]*', old_content)]
                new_comments = [re.sub(r'\s+', ' ', c).strip() for c in re.findall(r'#[^\n]*', new_content)]
                if old_comments == new_comments:
                    return True
        except Exception:
            pass

    return normalize_code(old_content, filepath) == normalize_code(new_content, filepath)


# ============================================================================
# Category 2: Imports & Module Organization
# ============================================================================

def _is_python_import_change(old_content: str, new_content: str) -> bool:
    """Evaluate Category 2 for Python code using AST."""
    try:
        tree_old = ast.parse(old_content)
        tree_new = ast.parse(new_content)
    except Exception:
        return False

    def extract_imports(tree):
        imports = set()
        body = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add((None, alias.name, alias.asname))
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imports.add((node.module, alias.name, alias.asname))
            else:
                body.append(node)
        return imports, body

    old_imports, old_body = extract_imports(tree_old)
    new_imports, new_body = extract_imports(tree_new)

    # 1. Compare non-import body ASTs
    t_old = ast.Module(body=old_body, type_ignores=[])
    t_new = ast.Module(body=new_body, type_ignores=[])
    old_dump = ast.dump(t_old, include_attributes=False)
    new_dump = ast.dump(t_new, include_attributes=False)

    if old_dump != new_dump:
        # Check if difference is solely conventional alias renaming in body (e.g. numpy -> np)
        for mod, alias in CONVENTIONAL_ALIASES.items():
            old_dump = old_dump.replace(f"id='{mod}'", f"id='{alias}'")
            new_dump = new_dump.replace(f"id='{mod}'", f"id='{alias}'")
        if old_dump != new_dump:
            return False

    # 2. If imports are identical (e.g. reordering, grouping, single/multiline)
    if old_imports == new_imports:
        return True

    # 3. Check for dead/unused import removal or conventional aliasing
    used_names = {node.id for node in ast.walk(t_new) if isinstance(node, ast.Name)}

    removed = old_imports - new_imports
    added = new_imports - old_imports

    conv_removed = set()
    conv_added = set()
    for mod, name, alias in added:
        target = alias or name
        for orig_mod, orig_alias in CONVENTIONAL_ALIASES.items():
            if (mod == orig_mod or name == orig_mod) and target == orig_alias:
                conv_added.add((mod, name, alias))
                for r in removed:
                    if r[1] == orig_mod or r[0] == orig_mod:
                        conv_removed.add(r)
        # Redundant alias removal: e.g. from x import y as y -> from x import y
        if alias is None and (mod, name, name) in removed:
            conv_added.add((mod, name, alias))
            conv_removed.add((mod, name, name))

    removed -= conv_removed
    added -= conv_added

    # All removed imports must be UNUSED in the new body
    for mod, name, alias in removed:
        bound_name = alias if alias else (name.split('.')[0] if mod is None else name)
        if bound_name in used_names:
            return False  # A used import was removed!

    # No unapproved new functional imports added
    if added:
        return False

    return True


JS_IMPORT_REGEX = re.compile(
    r'\b(?:'
    r'import\s+(?:(?:\*\s+as\s+[\w$]+|[\w$]+|(?:\{\s*[\w\s,$]+\s*\}))\s*,\s*)?'
    r'(?:(?:\*\s+as\s+[\w$]+|[\w$]+|(?:\{\s*[\w\s,$]+\s*\}))\s+from\s+)?[\'"][^\'"]+[\'"]\s*;?'
    r'|(?:const|let|var)\s+(?:[\w$]+|\{\s*[\w\s,$]+\s*\})\s*=\s*require\([\'"][^\'"]+[\'"]\)\s*;?'
    r'|import\s+[\'"][^\'"]+[\'"]\s*;?'
    r')'
)


def _parse_js_imports(code: str):
    imports = set()
    for m in JS_IMPORT_REGEX.finditer(code):
        text = m.group(0).strip()
        mod_m = re.search(r'[\'"]([^\'"]+)[\'"]', text)
        module = mod_m.group(1) if mod_m else ""
        named_m = re.search(r'\{\s*([\w\s,$]+)\s*\}', text)
        if named_m:
            for item in named_m.group(1).split(','):
                item = item.strip()
                if not item:
                    continue
                parts = re.split(r'\s+as\s+', item)
                if len(parts) == 2:
                    imports.add((module, parts[0].strip(), parts[1].strip()))
                else:
                    imports.add((module, parts[0].strip(), None))
        if 'from' in text:
            before_from = text.split('from')[0].replace('import', '').strip()
            before_from = re.sub(r'\{[^}]*\}', '', before_from).strip().strip(',')
            if before_from:
                ns_m = re.search(r'\*\s+as\s+([\w$]+)', before_from)
                if ns_m:
                    imports.add((module, '*', ns_m.group(1)))
                else:
                    def_name = before_from.strip()
                    if def_name:
                        imports.add((module, 'default', def_name))
        elif 'require' in text:
            lhs_m = re.search(r'(?:const|let|var)\s+([\w$]+)\s*=', text)
            if lhs_m:
                imports.add((module, 'default', lhs_m.group(1)))

    non_import = JS_IMPORT_REGEX.sub('\n', code)
    return imports, non_import


def _is_js_import_change(old_content: str, new_content: str, filepath: str = "") -> bool:
    """Evaluate Category 2 for JavaScript / TypeScript code."""
    old_imp, old_body = _parse_js_imports(old_content)
    new_imp, new_body = _parse_js_imports(new_content)

    norm_old = normalize_code(old_body, filepath)
    norm_new = normalize_code(new_body, filepath)
    if norm_old != norm_new:
        return False

    if old_imp == new_imp:
        return True

    removed = old_imp - new_imp
    added = new_imp - old_imp

    # JS conventional aliases (e.g. React default vs * as React)
    conv_removed = set()
    conv_added = set()
    for mod, sym, alias in added:
        if mod == 'react' and (sym == '*' or sym == 'default'):
            for r in removed:
                if r[0] == 'react' and (r[1] == '*' or r[1] == 'default'):
                    conv_added.add((mod, sym, alias))
                    conv_removed.add(r)
    removed -= conv_removed
    added -= conv_added

    for mod, sym, alias in removed:
        bound = alias if alias else (sym if sym != '*' else None)
        if bound and re.search(r'\b' + re.escape(bound) + r'\b', new_body):
            return False  # A used import was removed!

    if added:
        return False

    return True


def is_import_organization_change(old_content: str, new_content: str, filepath: str = "") -> bool:
    """
    Check if changes between old_content and new_content consist purely of Category 2
    (Import reordering & grouping, multiline wrapping, dead import removal, aliasing).
    """
    is_python = filepath.endswith('.py') or ('def ' in old_content and 'import ' in old_content)
    if is_python:
        if _is_python_import_change(old_content, new_content):
            return True

    is_js = any(filepath.endswith(ext) for ext in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs')) or (
        'import ' in old_content or 'require(' in old_content
    )
    if is_js:
        if _is_js_import_change(old_content, new_content, filepath):
            return True

    return False


def is_trivial_change(old_content: str, new_content: str, filepath: str = "") -> bool:
    """
    Returns True if differences between old_content and new_content fall into either:
    - Category 1: Formatting, whitespace, line wrapping, quotes, trailing commas, semicolons.
    - Category 2: Imports & module organization (reordering, single/multiline, dead removal, aliasing).
    """
    if old_content == new_content:
        return True

    if not old_content or not new_content:
        return False

    # Check Category 1 (Pure formatting)
    if is_pure_formatting_change(old_content, new_content, filepath):
        return True

    # Check Category 2 (Imports & module organization)
    if is_import_organization_change(old_content, new_content, filepath):
        return True

    return False


# Backward compatibility alias
is_formatting_change = is_trivial_change


def is_file_formatting_only(filepath: str, project_root: str = "") -> bool:
    """
    Check if a staged file's differences relative to HEAD are strictly formatting
    or import organization.
    """
    cwd = project_root or os.getcwd()
    try:
        old_content = subprocess.check_output(
            ["git", "show", f"HEAD:{filepath}"],
            text=True, stderr=subprocess.DEVNULL, cwd=cwd
        )
    except subprocess.CalledProcessError:
        return False  # Newly added file or unborn HEAD

    try:
        new_content = subprocess.check_output(
            ["git", "show", f":{filepath}"],
            text=True, stderr=subprocess.DEVNULL, cwd=cwd
        )
    except subprocess.CalledProcessError:
        return False  # Deleted file

    return is_trivial_change(old_content, new_content, filepath)


is_file_trivial_only = is_file_formatting_only


def is_commit_formatting_only(changes: Dict[str, Any], project_root: str = "") -> bool:
    """
    Check if ALL staged changes across the commit are purely formatting or import
    organization. Returns False if there are added/deleted files or any file with
    semantic changes.
    """
    files = (changes or {}).get("files", [])
    if not files:
        return False

    for f in files:
        status = f.get("status")
        if status not in ("modified", "M"):
            return False

        path = f.get("path")
        if not path:
            return False

        if not is_file_formatting_only(path, project_root):
            return False

    return True


is_commit_trivial_only = is_commit_formatting_only
