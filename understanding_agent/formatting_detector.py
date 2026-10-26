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

Category 3: Comments, Docs & Annotations
10. Typo & grammar fixes: Correcting spelling or wording in inline comments, docstrings,
    or markdown files.
11. Comment cleanup: Removing commented-out dead code, temporary notes, or stale // TODO
    items that were completed.
12. Pure type annotations: Adding or refining type hints (e.g., : str, -> None in Python
    or : Promise<void> in TypeScript) that do not alter runtime behavior.
13. Documentation formatting: Reformatting docstrings (e.g., Google docstring to Sphinx/NumPy
    style) without modifying the documented behavior.

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


# ============================================================================
# Category 3: Comments, Docs & Annotations
# ============================================================================

DOCS_EXTENSIONS = ('.md', '.markdown', '.rst', '.txt', '.adoc', '.rdoc', '.docs')


def is_docs_file(filepath: str) -> bool:
    """Check if filepath is a pure documentation or markdown file."""
    if not filepath:
        return False
    norm = filepath.lower().replace('\\', '/')
    if norm.endswith(DOCS_EXTENSIONS):
        return True
    if norm.startswith(('docs/', 'doc/')) or '/docs/' in norm or '/doc/' in norm:
        return True
    return False


def strip_comments(code: str) -> str:
    """Strip all comments (//, /* */, #, <!-- -->) while preserving string literals."""
    token_pattern = re.compile(
        r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\''
        r'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\''
        r'|`[\s\S]*?`'
        r'|/\*[\s\S]*?\*/'
        r'|//[^\n]*'
        r'|#[^\n]*'
        r'|<!--[\s\S]*?-->)'
    )
    parts = []
    last_idx = 0
    for match in token_pattern.finditer(code):
        parts.append(code[last_idx:match.start()])
        tok = match.group(0)
        if tok.startswith(('"', "'", '`')):
            parts.append(tok)
        elif tok.startswith(('/*', '//', '#', '<!--')):
            if tok.startswith(('//', '#')):
                parts.append('\n')
            else:
                parts.append(' ')
        last_idx = match.end()
    parts.append(code[last_idx:])
    return ''.join(parts)


def _strip_python_docs_and_types(code: str):
    """
    Parse Python code and return (imports_set, stripped_body_dump).
    Docstrings, type annotations, and typing imports are stripped from the AST.
    """
    try:
        tree = ast.parse(code)
    except Exception:
        return None, None

    class PythonTrivialTransformer(ast.NodeTransformer):
        def _strip_doc(self, node):
            if hasattr(node, 'body') and node.body and isinstance(node.body[0], ast.Expr):
                val = node.body[0].value
                is_str = (isinstance(val, ast.Constant) and isinstance(val.value, str)) or \
                         (hasattr(ast, 'Str') and isinstance(val, getattr(ast, 'Str')))
                if is_str:
                    node.body = node.body[1:]
                    if not node.body:
                        node.body = [ast.Pass()]
            return node

        def visit_FunctionDef(self, node):
            node.returns = None
            for arg_group in (node.args.args, getattr(node.args, 'posonlyargs', []),
                              node.args.kwonlyargs):
                for a in arg_group:
                    a.annotation = None
            if node.args.vararg:
                node.args.vararg.annotation = None
            if node.args.kwarg:
                node.args.kwarg.annotation = None
            self.generic_visit(node)
            return self._strip_doc(node)

        def visit_AsyncFunctionDef(self, node):
            node.returns = None
            for arg_group in (node.args.args, getattr(node.args, 'posonlyargs', []),
                              node.args.kwonlyargs):
                for a in arg_group:
                    a.annotation = None
            if node.args.vararg:
                node.args.vararg.annotation = None
            if node.args.kwarg:
                node.args.kwarg.annotation = None
            self.generic_visit(node)
            return self._strip_doc(node)

        def visit_AnnAssign(self, node):
            self.generic_visit(node)
            if node.value is not None:
                new_node = ast.Assign(targets=[node.target], value=node.value)
                return ast.copy_location(new_node, node)
            return None

        def visit_ClassDef(self, node):
            self.generic_visit(node)
            return self._strip_doc(node)

    transformed = PythonTrivialTransformer().visit(tree)
    imports = set()
    body = []
    for node in transformed.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ('typing', 'typing_extensions'):
                    imports.add((None, alias.name, alias.asname))
        elif isinstance(node, ast.ImportFrom):
            if node.module not in ('typing', 'typing_extensions'):
                for alias in node.names:
                    imports.add((node.module, alias.name, alias.asname))
        else:
            body.append(node)

    mod = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(mod)
    dump = ast.dump(mod, include_attributes=False)
    return imports, dump


def strip_ts_type_annotations(code: str) -> str:
    """Strip TypeScript type declarations, annotations, generics, and assertions."""
    # 1. Strip import type: import type { Foo } from ... or import { type Foo } from ...
    code = re.sub(r'import\s+type\s+[^;]+;?', '', code)
    code = re.sub(r'\btype\s+([\w$]+)\s*,', '', code)
    code = re.sub(r',\s*type\s+([\w$]+)', '', code)

    # 2. Strip interface & type declarations
    code = re.sub(r'\b(?:export\s+)?interface\s+[\w$]+(?:<[^>]*>)?(?:\s+extends\s+[^{]+)?\s*\{[^}]*\}', '', code)
    code = re.sub(r'\b(?:export\s+)?type\s+[\w$]+(?:<[^>]*>)?\s*=\s*[^;]+;?', '', code)

    # 3. Strip 'as Type' assertions: as User, as const, as unknown as User
    code = re.sub(r'\s+as\s+(?:unknown\s+as\s+)?[\w$]+(?:<[^>]*>)?(?:\[\])?', '', code)

    # 4. Strip return type annotations: ): Promise<void> => or ): string {
    code = re.sub(r'\)\s*:\s*[\w$]+(?:<[^>]*>)?(?:\[\])?(?:\s*\|\s*[\w$]+(?:<[^>]*>)?(?:\[\])?)*\s*(?=[{=])', ') ', code)

    # 5. Variable types: const x: number = 5; or let x: string;
    code = re.sub(r'(\b(?:let|const|var)\s+[\w$]+)\s*:\s*[\w$]+(?:<[^>]*>)?(?:\[\])?(?:\s*\|\s*[\w$]+(?:<[^>]*>)?(?:\[\])?)*\s*(=|;)', r'\1 \2', code)

    # 6. Parameter types: (x: number, y: string = "a")
    def strip_params(m):
        return '(' + re.sub(r'([\w$]+)\s*:\s*[\w$]+(?:<[^>]*>)?(?:\[\])?(?:\s*\|\s*[\w$]+(?:<[^>]*>)?(?:\[\])?)*', r'\1', m.group(1)) + ')'
    code = re.sub(r'\(([^)]*)\)', strip_params, code)

    # 7. Generic type parameters in calls: useState<User | null>(null)
    code = re.sub(r'([\w$]+)<[\w$\s,|\[\]]+>\s*\(', r'\1(', code)

    return code


def is_comments_docs_or_annotations_change(old_content: str, new_content: str, filepath: str = "") -> bool:
    """
    Check Category 3:
    - Typo & grammar fixes in inline comments, docstrings, or markdown files
    - Comment cleanup (removing commented-out dead code, temporary notes, stale TODOs)
    - Pure type annotations (adding/refining type hints in Python or TS)
    - Documentation formatting (docstring style reformatting, JSDoc formatting)
    """
    # 1. Markdown / Documentation files
    if is_docs_file(filepath):
        return True

    # 2. Universal comment stripping (applies to all languages: Python, JS, TS, Go, Rust, Java, etc.)
    stripped_old = strip_comments(old_content)
    stripped_new = strip_comments(new_content)
    if normalize_code(stripped_old, filepath) == normalize_code(stripped_new, filepath):
        return True

    # 3. Python docstrings, comments, typing imports & type annotations via AST
    is_python = filepath.endswith('.py') or ('def ' in old_content and 'import ' in old_content)
    if is_python:
        old_imp, old_dump = _strip_python_docs_and_types(old_content)
        new_imp, new_dump = _strip_python_docs_and_types(new_content)
        if old_dump is not None and new_dump is not None:
            body_matched = (old_dump == new_dump)
            if not body_matched:
                for mod, alias in CONVENTIONAL_ALIASES.items():
                    old_dump = old_dump.replace(f"id='{mod}'", f"id='{alias}'")
                    new_dump = new_dump.replace(f"id='{mod}'", f"id='{alias}'")
                body_matched = (old_dump == new_dump)

            if body_matched:
                if old_imp == new_imp:
                    return True
                removed = old_imp - new_imp
                added = new_imp - old_imp
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
                removed -= conv_removed
                added -= conv_added
                if not added:
                    try:
                        tree_new = ast.parse(new_content)
                        used_names = {node.id for node in ast.walk(tree_new) if isinstance(node, ast.Name)}
                        for mod, name, alias in removed:
                            bound_name = alias if alias else (name.split('.')[0] if mod is None else name)
                            if bound_name in used_names:
                                return False
                        return True
                    except Exception:
                        pass

    # 4. JavaScript / TypeScript comments, JSDoc & type annotations
    is_js = any(filepath.endswith(ext) for ext in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs')) or (
        'function ' in old_content or 'const ' in old_content or 'let ' in old_content or 'import ' in old_content
    )
    if is_js:
        norm_ts_old = normalize_code(strip_ts_type_annotations(strip_comments(old_content)), filepath)
        norm_ts_new = normalize_code(strip_ts_type_annotations(strip_comments(new_content)), filepath)
        if norm_ts_old == norm_ts_new:
            return True

    return False


def is_trivial_change(old_content: str, new_content: str, filepath: str = "") -> bool:
    """
    Returns True if differences between old_content and new_content fall into:
    - Category 1: Formatting, whitespace, line wrapping, quotes, trailing commas, semicolons.
    - Category 2: Imports & module organization (reordering, single/multiline, dead removal, aliasing).
    - Category 3: Comments, Docs & Annotations (typos in comments/docs/markdown, comment cleanup,
                  docstring reformatting, pure type annotations).
    """
    if old_content == new_content:
        return True

    if not old_content or not new_content:
        if is_docs_file(filepath):
            return True
        return False

    # Check Category 1 (Pure formatting)
    if is_pure_formatting_change(old_content, new_content, filepath):
        return True

    # Check Category 2 (Imports & module organization)
    if is_import_organization_change(old_content, new_content, filepath):
        return True

    # Check Category 3 (Comments, Docs & Annotations)
    if is_comments_docs_or_annotations_change(old_content, new_content, filepath):
        return True

    return False


# Backward compatibility alias
is_formatting_change = is_trivial_change


def is_file_formatting_only(filepath: str, project_root: str = "") -> bool:
    """
    Check if a staged file's differences relative to HEAD are strictly formatting,
    import organization, comments, docs, or type annotations.
    """
    cwd = project_root or os.getcwd()
    try:
        old_content = subprocess.check_output(
            ["git", "show", f"HEAD:{filepath}"],
            text=True, stderr=subprocess.DEVNULL, cwd=cwd
        )
    except subprocess.CalledProcessError:
        if is_docs_file(filepath):
            return True
        return False  # Newly added code file or unborn HEAD

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
    Check if ALL staged changes across the commit are purely formatting, import
    organization, comments, docs, or type annotations.
    """
    files = (changes or {}).get("files", [])
    if not files:
        return False

    for f in files:
        status = f.get("status")
        path = f.get("path")
        if not path:
            return False

        if status in ("modified", "M"):
            if not is_file_formatting_only(path, project_root):
                return False
        elif status in ("added", "A", "untracked") and is_docs_file(path):
            continue
        else:
            return False

    return True


is_commit_trivial_only = is_commit_formatting_only

