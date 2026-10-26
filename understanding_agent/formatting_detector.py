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

Category 4: Non-Code & Metadata Files
14. Git metadata & commit message tweaks: tag updates, .gitattributes adjustments.
15. Ignore files: .gitignore, .dockerignore, .eslintignore, .prettierignore.
16. License / Header copyright updates: Bumping copyright years or adding license headers.
17. Project documentation files: README.md, CHANGELOG.md, CONTRIBUTING.md, or doc assets.

Category 5: Local Variable & Cosmetic Refactoring (Within Local Scope)
18. Local variable renaming for clarity: Renaming local variables inside function scope.
19. Inlining / extracting trivial locals: Inlining single-use locals or extracting expressions.
20. Syntactic sugar / Pythonic idioms: Minor idiom upgrades (x is None, not items, etc.).

Category 6: Logging, Print & Debug Cleanup
21. Debug artifact cleanup: Deleting stray print(), console.log(), or debugger statements.
22. Log message text polish: Fixing typos, casing, or punctuation in log message strings.
23. Log level downgrades: Downgrading high-frequency verbose logs (e.g., INFO -> DEBUG).

Category 7: Dependency & Config Bumps
24. Lockfile-only updates: Routine lockfile changes (package-lock.json, poetry.lock, Cargo.lock).
25. Patch version bumps: Single-line version bumps in metadata files (package.json, pyproject.toml).

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

# ============================================================================
# Category 4: Non-Code & Metadata Files
# ============================================================================

METADATA_IGNORE_PATTERNS = (
    '.gitignore', '.dockerignore', '.eslintignore', '.prettierignore',
    '.npmignore', '.stylelintignore', '.helmignore', '.gitattributes',
    '.mailmap', '.editorconfig',
)

DOCS_EXTENSIONS = ('.md', '.markdown', '.rst', '.txt', '.adoc', '.rdoc', '.docs')

DOCS_SPECIAL_FILES = (
    'license', 'licence', 'copying', 'notice', 'authors',
    'readme', 'changelog', 'contributing', 'code_of_conduct',
    'security', 'pull_request_template',
)

DOCS_ASSET_EXTENSIONS = ('.svg', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.webp')


def is_metadata_or_non_code_file(filepath: str) -> bool:
    """
    Check if filepath belongs to Category 4:
    - Ignore files (.gitignore, .dockerignore, .eslintignore, etc.)
    - Git metadata & attributes (.gitattributes, .mailmap)
    - Licenses & notices (LICENSE, LICENCE, NOTICE, AUTHORS)
    - Project documentation files (README, CHANGELOG, CONTRIBUTING, etc.)
    - Documentation assets in docs/ or assets/
    """
    if not filepath:
        return False
    norm = filepath.lower().replace('\\', '/')
    base = os.path.basename(norm)

    # 1. Ignore files & git attributes
    if base.startswith('.') and (base.endswith('ignore') or base in ('.gitattributes', '.mailmap', '.editorconfig')):
        return True
    if base in METADATA_IGNORE_PATTERNS:
        return True

    # 2. Markdown and documentation text files
    if norm.endswith(DOCS_EXTENSIONS):
        return True

    # 3. Dedicated project documentation / license files (e.g. LICENSE, README, CHANGELOG)
    name_no_ext = os.path.splitext(base)[0]
    if name_no_ext in DOCS_SPECIAL_FILES:
        return True

    # 4. Files strictly inside docs/ or doc/ directory
    if norm.startswith(('docs/', 'doc/')) or '/docs/' in norm or '/doc/' in norm:
        if norm.endswith(DOCS_EXTENSIONS) or norm.endswith(DOCS_ASSET_EXTENSIONS):
            return True

    return False


# Compatibility alias
is_docs_file = is_metadata_or_non_code_file


# ============================================================================
# Category 7: Dependency & Config Bumps
# ============================================================================

LOCKFILES = (
    'package-lock.json', 'poetry.lock', 'cargo.lock', 'yarn.lock',
    'pnpm-lock.yaml', 'pipfile.lock', 'composer.lock', 'gemfile.lock',
)

VERSION_REGEX = re.compile(
    r'([\"\']?version[\"\']?\s*[:=]\s*[\"\']\d+\.\d+\.)\d+([\"\'])'
)


def is_lockfile(filepath: str) -> bool:
    """Check if filepath is an auto-generated package lockfile."""
    if not filepath:
        return False
    base = os.path.basename(filepath).lower()
    return base in LOCKFILES


def is_patch_version_bump(old_content: str, new_content: str, filepath: str = "") -> bool:
    """
    Check if the change between old_content and new_content is strictly a single-line
    patch version bump in metadata files (package.json, pyproject.toml, Cargo.toml, etc.).
    """
    if not filepath:
        return False
    base = os.path.basename(filepath).lower()
    meta_files = (
        'package.json', 'pyproject.toml', 'setup.cfg', 'cargo.toml',
        'version.py', '__version__.py', 'pom.xml', 'build.gradle'
    )
    if base not in meta_files:
        return False

    old_norm = VERSION_REGEX.sub(r'\g<1>__PATCH__\g<2>', old_content)
    new_norm = VERSION_REGEX.sub(r'\g<1>__PATCH__\g<2>', new_content)
    return old_norm == new_norm


# ============================================================================
# Categories 3, 5, 6: Comments, Docs, Types, Local Refactoring, Logging & Debug
# ============================================================================

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
    Applies AST transformations for:
    - Category 3: Docstrings & type annotations stripped
    - Category 5: Local variable canonicalization, inlined/extracted return locals, idioms
    - Category 6: Debug artifact cleanup (print, breakpoint, pdb) & logging polish/downgrades
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

        def visit_Expr(self, node):
            # Category 6: Debug artifact cleanup (print, breakpoint, pdb.set_trace)
            if isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Name) and func.id in ('print', 'breakpoint'):
                    return None
                if isinstance(func, ast.Attribute) and func.attr == 'set_trace':
                    if isinstance(func.value, ast.Name) and func.value.id == 'pdb':
                        return None
            return self.generic_visit(node)

        def visit_Call(self, node):
            self.generic_visit(node)
            func = node.func
            # Category 6: Logging polish & log level downgrade
            if isinstance(func, ast.Attribute) and func.attr in ('debug', 'info', 'warning', 'warn'):
                func.attr = '__log__'
                if node.args and isinstance(node.args[0], (ast.Constant, getattr(ast, 'Str', ()))) :
                    node.args[0] = ast.Constant(value='__LOG_MSG__')
            return node

        def visit_Compare(self, node):
            self.generic_visit(node)
            # Category 5: Syntactic sugar / Pythonic idioms
            if len(node.ops) == 1 and len(node.comparators) == 1:
                op = node.ops[0]
                comp = node.comparators[0]
                # x == None -> x is None
                if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) and comp.value is None:
                    return ast.copy_location(ast.Compare(left=node.left, ops=[ast.Is()], comparators=[comp]), node)
                # x != None -> x is not None
                if isinstance(op, ast.NotEq) and isinstance(comp, ast.Constant) and comp.value is None:
                    return ast.copy_location(ast.Compare(left=node.left, ops=[ast.IsNot()], comparators=[comp]), node)
                # len(x) == 0 -> not x
                if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) and comp.value == 0:
                    if isinstance(node.left, ast.Call) and isinstance(node.left.func, ast.Name) and node.left.func.id == 'len' and len(node.left.args) == 1:
                        return ast.copy_location(ast.UnaryOp(op=ast.Not(), operand=node.left.args[0]), node)
            return node

        def visit_UnaryOp(self, node):
            self.generic_visit(node)
            # Category 5: not len(x) -> not x
            if isinstance(node.op, ast.Not):
                if isinstance(node.operand, ast.Call) and isinstance(node.operand.func, ast.Name) and node.operand.func.id == 'len' and len(node.operand.args) == 1:
                    return ast.copy_location(ast.UnaryOp(op=ast.Not(), operand=node.operand.args[0]), node)
            return node

        def _canonicalize_locals(self, func_node):
            param_names = set()
            for arg in (func_node.args.args + getattr(func_node.args, 'posonlyargs', []) +
                        func_node.args.kwonlyargs):
                param_names.add(arg.arg)
            if func_node.args.vararg:
                param_names.add(func_node.args.vararg.arg)
            if func_node.args.kwarg:
                param_names.add(func_node.args.kwarg.arg)

            explicit_nonlocals = set()
            for node in ast.walk(func_node):
                if isinstance(node, (ast.Global, ast.Nonlocal)):
                    explicit_nonlocals.update(node.names)

            local_names = []
            for node in ast.walk(func_node):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    if node.id not in param_names and node.id not in explicit_nonlocals:
                        if node.id not in local_names:
                            local_names.append(node.id)

            rename_map = {name: f'__loc_{i}__' for i, name in enumerate(local_names)}

            class LocalRenamer(ast.NodeTransformer):
                def visit_Name(self, node):
                    if node.id in rename_map:
                        node.id = rename_map[node.id]
                    return node

            LocalRenamer().visit(func_node)

        def _inline_return_locals(self, func_node):
            new_body = []
            i = 0
            while i < len(func_node.body):
                stmt = func_node.body[i]
                if i + 1 < len(func_node.body):
                    next_stmt = func_node.body[i + 1]
                    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                        loc_name = stmt.targets[0].id
                        if isinstance(next_stmt, ast.Return) and isinstance(next_stmt.value, ast.Name) and next_stmt.value.id == loc_name:
                            new_body.append(ast.copy_location(ast.Return(value=stmt.value), next_stmt))
                            i += 2
                            continue
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
                        loc_name = stmt.target.id
                        if isinstance(next_stmt, ast.Return) and isinstance(next_stmt.value, ast.Name) and next_stmt.value.id == loc_name:
                            new_body.append(ast.copy_location(ast.Return(value=stmt.value), next_stmt))
                            i += 2
                            continue
                new_body.append(stmt)
                i += 1
            func_node.body = new_body

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
            self._strip_doc(node)
            self.generic_visit(node)
            self._inline_return_locals(node)
            self._canonicalize_locals(node)
            return node

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
            self._strip_doc(node)
            self.generic_visit(node)
            self._inline_return_locals(node)
            self._canonicalize_locals(node)
            return node

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
    for n in ast.walk(transformed):
        if hasattr(n, 'body') and isinstance(n.body, list):
            n.body = [stmt for stmt in n.body if stmt is not None]
            if not n.body:
                n.body = [ast.Pass()]

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


def _normalize_js_trivia(code: str) -> str:
    """Normalize JavaScript/TypeScript for Categories 3, 5, and 6."""
    # 1. Comments & TypeScript types
    code = strip_ts_type_annotations(strip_comments(code))

    # 2. Category 6: Debug artifact cleanup (console.log, debugger)
    code = re.sub(r'\bconsole\.(?:log|debug|info|warn|error)\s*\([^)]*\)\s*;?', '', code)
    code = re.sub(r'\bdebugger\s*;?', '', code)

    # 3. Category 6: Logging polish & log level downgrade
    code = re.sub(r'\blogger\.(?:debug|info|warning|warn)\s*\([\"\'][^\"\']*[\"\']', 'logger.__log__("__LOG_MSG__"', code)

    # 4. Category 5: Syntactic sugar / idioms
    code = re.sub(r'([\w$]+)\s*===\s*null\s*\|\|\s*\1\s*===\s*undefined', r'\1 == null', code)
    code = re.sub(r'([\w$]+)\s*!==\s*null\s*&&\s*\1\s*!==\s*undefined', r'\1 != null', code)

    # 5. Category 5: Inlining / extracting single-use return locals
    code = re.sub(r'\b(?:const|let|var)\s+([\w$]+)\s*=\s*([^;]+);\s*return\s+\1;?', r'return \2;', code)

    # 6. Category 5: Canonicalize function locals using balanced brace scanning
    func_pattern = re.compile(r'(?:function\s*[\w$]*\s*\(([^)]*)\)|(?:\(([^)]*)\)|([\w$]+))\s*=>)\s*\{')
    pos = 0
    result = []
    while pos < len(code):
        m = func_pattern.search(code, pos)
        if not m:
            result.append(code[pos:])
            break
        result.append(code[pos:m.end()])

        param_str = m.group(1) or m.group(2) or m.group(3) or ""
        params = {p.strip().split('=')[0].strip() for p in param_str.split(',') if p.strip()}

        depth = 1
        i = m.end()
        in_str = None
        while i < len(code) and depth > 0:
            ch = code[i]
            if in_str:
                if ch == '\\':
                    i += 2
                    continue
                elif ch == in_str:
                    in_str = None
            else:
                if ch in ('"', "'", '`'):
                    in_str = ch
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        break
            i += 1

        body = code[m.end():i]
        locals_found = []
        for decl in re.finditer(r'\b(?:const|let|var)\s+([\w$]+)\b', body):
            name = decl.group(1)
            if name not in params and name not in locals_found:
                locals_found.append(name)
        new_body = body
        for idx, name in enumerate(locals_found):
            new_body = re.sub(r'\b' + re.escape(name) + r'\b', f'__loc_{idx}__', new_body)
        result.append(new_body)
        pos = i

    code = ''.join(result)
    return code


def is_comments_docs_or_annotations_change(old_content: str, new_content: str, filepath: str = "") -> bool:
    """
    Check Categories 3, 5, 6:
    - Category 3: Comments, docs, markdown, pure type annotations
    - Category 5: Local variable renaming, inlining/extracting, idioms
    - Category 6: Debug prints/console.log, log text polish, log downgrades
    """
    # 1. Non-code & Markdown / Documentation files (Category 4)
    if is_metadata_or_non_code_file(filepath):
        return True

    # 2. Universal comment stripping (all languages)
    stripped_old = strip_comments(old_content)
    stripped_new = strip_comments(new_content)
    if normalize_code(stripped_old, filepath) == normalize_code(stripped_new, filepath):
        return True

    # 3. Python AST: docstrings, type annotations, local renames, idioms, debug prints & logs
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

    # 4. JavaScript / TypeScript comments, JSDoc, types, local renames, debug & logs
    is_js = any(filepath.endswith(ext) for ext in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs')) or (
        'function ' in old_content or 'const ' in old_content or 'let ' in old_content or 'import ' in old_content
    )
    if is_js:
        norm_ts_old = normalize_code(_normalize_js_trivia(old_content), filepath)
        norm_ts_new = normalize_code(_normalize_js_trivia(new_content), filepath)
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
    - Category 4: Non-Code & Metadata Files (ignore files, git attributes, licenses, docs).
    - Category 5: Local Variable & Cosmetic Refactoring (local renaming, inlining/extracting, idioms).
    - Category 6: Logging, Print & Debug Cleanup (debug prints, log text polish, log level downgrades).
    - Category 7: Dependency & Config Bumps (lockfiles, patch version bumps).
    """
    if old_content == new_content:
        return True

    # Category 4 & Category 7: Non-code metadata or lockfile
    if is_metadata_or_non_code_file(filepath) or is_lockfile(filepath):
        return True

    # Category 7: Patch version bump
    if is_patch_version_bump(old_content, new_content, filepath):
        return True

    if not old_content or not new_content:
        return False

    # Category 1 (Pure formatting)
    if is_pure_formatting_change(old_content, new_content, filepath):
        return True

    # Category 2 (Imports & module organization)
    if is_import_organization_change(old_content, new_content, filepath):
        return True

    # Categories 3, 5, 6 (Comments, Docs, Types, Local Refactoring, Logging & Debug)
    if is_comments_docs_or_annotations_change(old_content, new_content, filepath):
        return True

    return False


# Backward compatibility alias
is_formatting_change = is_trivial_change


def is_file_formatting_only(filepath: str, project_root: str = "") -> bool:
    """
    Check if a staged file's differences relative to HEAD are strictly trivial
    (formatting, imports, comments, metadata, local refactoring, logging, dependencies).
    """
    cwd = project_root or os.getcwd()
    if is_metadata_or_non_code_file(filepath) or is_lockfile(filepath):
        return True

    try:
        old_content = subprocess.check_output(
            ["git", "show", f"HEAD:{filepath}"],
            text=True, stderr=subprocess.DEVNULL, cwd=cwd
        )
    except subprocess.CalledProcessError:
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
    Check if ALL staged changes across the commit are purely trivial (Categories 1-7).
    """
    files = (changes or {}).get("files", [])
    if not files:
        # Check if git diff --cached has staged files filtered out by ChangeDetector (e.g. lockfiles)
        cwd = project_root or os.getcwd()
        try:
            diff_names = subprocess.check_output(
                ["git", "diff", "--cached", "--name-status"],
                text=True, stderr=subprocess.DEVNULL, cwd=cwd
            ).strip()
        except Exception:
            return False
        if not diff_names:
            return False
        for line in diff_names.split('\n'):
            if not line:
                continue
            parts = line.split('\t')
            path = parts[-1]
            if is_metadata_or_non_code_file(path) or is_lockfile(path):
                continue
            return False
        return True

    for f in files:
        status = f.get("status")
        path = f.get("path")
        if not path:
            return False

        if status in ("modified", "M"):
            if not is_file_formatting_only(path, project_root):
                return False
        elif status in ("added", "A", "untracked"):
            if is_metadata_or_non_code_file(path) or is_lockfile(path):
                continue
            return False
        else:
            return False

    return True


is_commit_trivial_only = is_commit_formatting_only

