"""Tests for deterministic formatting & trivial changes detector."""

import os
import subprocess
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from understanding_agent.formatting_detector import (
    is_formatting_change,
    is_file_formatting_only,
    is_commit_formatting_only,
    normalize_code,
)
from understanding_agent.change_detector import ChangeDetector
from understanding_agent.code_graph import CodeGraph
from understanding_agent.context_builder import ContextBuilder


class TestFormattingDetector(unittest.TestCase):

    # 1. Whitespace & Indentation
    def test_trailing_whitespace_ignored(self):
        old = "def calculate(x):\n    return x * 2\n"
        new = "def calculate(x):   \n    return x * 2     \n"
        self.assertTrue(is_formatting_change(old, new, "calc.py"))

    def test_tabs_to_spaces_ignored(self):
        old = "def calculate(x):\n\treturn x * 2\n"
        new = "def calculate(x):\n    return x * 2\n"
        self.assertTrue(is_formatting_change(old, new, "calc.py"))

    def test_indentation_adjustment_ignored(self):
        old = "def f():\n  x = 1\n  return x\n"
        new = "def f():\n    x = 1\n    return x\n"
        self.assertTrue(is_formatting_change(old, new, "calc.py"))

    def test_blank_lines_added_or_removed_ignored(self):
        old = "def a(): pass\ndef b(): pass\n"
        new = "def a(): pass\n\n\n\ndef b(): pass\n"
        self.assertTrue(is_formatting_change(old, new, "calc.py"))

    # 2. Line Wrapping & Bracket Breaks
    def test_line_wrapping_arguments_python(self):
        old = "res = compute(alpha, beta, gamma)\n"
        new = "res = compute(\n    alpha,\n    beta,\n    gamma\n)\n"
        self.assertTrue(is_formatting_change(old, new, "calc.py"))

    def test_line_wrapping_arguments_js(self):
        old = "const res = compute(alpha, beta, gamma);"
        new = "const res = compute(\n  alpha,\n  beta,\n  gamma\n);"
        self.assertTrue(is_formatting_change(old, new, "calc.js"))

    def test_bracket_breaks_js(self):
        old = "if (ready) { execute(); }"
        new = "if (ready)\n{\n  execute();\n}"
        self.assertTrue(is_formatting_change(old, new, "calc.js"))

    # 3. Quote Normalization
    def test_single_to_double_quotes_python(self):
        old = "msg = 'hello world'\n"
        new = 'msg = "hello world"\n'
        self.assertTrue(is_formatting_change(old, new, "calc.py"))

    def test_single_to_double_quotes_js(self):
        old = "const user = { role: 'admin', name: 'Alice' };"
        new = 'const user = { role: "admin", name: "Alice" };'
        self.assertTrue(is_formatting_change(old, new, "calc.ts"))

    def test_quotes_with_internal_quotes(self):
        old = "val = 'hello \"world\"'\n"
        new = 'val = "hello \\"world\\""\n'
        self.assertTrue(is_formatting_change(old, new, "calc.py"))

    # 4. Trailing Commas
    def test_trailing_comma_in_list_python(self):
        old = "items = [1, 2, 3]\n"
        new = "items = [\n    1,\n    2,\n    3,\n]\n"
        self.assertTrue(is_formatting_change(old, new, "calc.py"))

    def test_trailing_comma_in_object_js(self):
        old = "const obj = { a: 1, b: 2 };"
        new = "const obj = {\n  a: 1,\n  b: 2,\n};"
        self.assertTrue(is_formatting_change(old, new, "calc.js"))

    def test_trailing_comma_in_function_call(self):
        old = "call(x, y)"
        new = "call(\n  x,\n  y,\n)"
        self.assertTrue(is_formatting_change(old, new, "calc.js"))

    # 5. Delimiter Adjustments
    def test_optional_semicolons_js(self):
        old = "const a = 1;\nconst b = 2;\n"
        new = "const a = 1\nconst b = 2\n"
        self.assertTrue(is_formatting_change(old, new, "calc.js"))

    # 6. Negative Cases (Semantic Changes MUST NOT be treated as formatting)
    def test_variable_rename_is_not_formatting(self):
        old = "let count = 0;"
        new = "let total = 0;"
        self.assertFalse(is_formatting_change(old, new, "calc.js"))

    def test_value_change_is_not_formatting(self):
        old = "const maxRetries = 3;"
        new = "const maxRetries = 5;"
        self.assertFalse(is_formatting_change(old, new, "calc.js"))

    def test_string_content_change_is_not_formatting(self):
        old = 'msg = "hello"'
        new = 'msg = "world"'
        self.assertFalse(is_formatting_change(old, new, "calc.py"))

    def test_logic_operator_change_is_not_formatting(self):
        old = "if (count > 0) return true;"
        new = "if (count >= 0) return true;"
        self.assertFalse(is_formatting_change(old, new, "calc.js"))

    def test_added_parameter_is_not_formatting(self):
        old = "function f(a, b) { return a + b; }"
        new = "function f(a, b, c) { return a + b + c; }"
        self.assertFalse(is_formatting_change(old, new, "calc.js"))

    def test_for_loop_semicolons_not_stripped_wrongly(self):
        old = "for (let i = 0; i < 10; i++) {}"
        new = "for (let i = 0; i < 20; i++) {}"
        self.assertFalse(is_formatting_change(old, new, "calc.js"))

    # --- Category 2: Imports & Module Organization ---

    def test_import_reordering_python(self):
        old = "import sys\nimport os\n\ndef get_cwd(): return os.getcwd()\n"
        new = "import os\nimport sys\n\ndef get_cwd(): return os.getcwd()\n"
        self.assertTrue(is_formatting_change(old, new, "test.py"))

    def test_import_grouping_blank_lines_python(self):
        old = "import os\nimport sys\nimport requests\n\ndef get_cwd(): return os.getcwd()\n"
        new = "import os\nimport sys\n\nimport requests\n\ndef get_cwd(): return os.getcwd()\n"
        self.assertTrue(is_formatting_change(old, new, "test.py"))

    def test_multiline_import_python(self):
        old = "from typing import List, Dict, Optional\n\ndef f(x: List): return x\n"
        new = "from typing import (\n    Dict,\n    List,\n    Optional,\n)\n\ndef f(x: List): return x\n"
        self.assertTrue(is_formatting_change(old, new, "test.py"))

    def test_dead_unused_import_removal_python(self):
        old = "import os\nimport math\nfrom typing import List, Dict\n\ndef get_cwd(): return os.getcwd()\n"
        new = "import os\n\ndef get_cwd(): return os.getcwd()\n"
        self.assertTrue(is_formatting_change(old, new, "test.py"))

    def test_conventional_aliasing_numpy_python(self):
        old = "import numpy\n\ndef zeros(n): return numpy.zeros(n)\n"
        new = "import numpy as np\n\ndef zeros(n): return np.zeros(n)\n"
        self.assertTrue(is_formatting_change(old, new, "test.py"))

    def test_conventional_aliasing_pandas_python(self):
        old = "import pandas\n\ndef df(d): return pandas.DataFrame(d)\n"
        new = "import pandas as pd\n\ndef df(d): return pd.DataFrame(d)\n"
        self.assertTrue(is_formatting_change(old, new, "test.py"))

    def test_import_reordering_js(self):
        old = "import { b } from './b';\nimport { a } from './a';\nconst x = a() + b();\n"
        new = "import { a } from './a';\nimport { b } from './b';\nconst x = a() + b();\n"
        self.assertTrue(is_formatting_change(old, new, "test.js"))

    def test_dead_unused_import_removal_js(self):
        old = "import { a, unused } from './utils';\nconst x = a();\n"
        new = "import { a } from './utils';\nconst x = a();\n"
        self.assertTrue(is_formatting_change(old, new, "test.js"))

    def test_negative_used_import_removal_python(self):
        old = "import os\n\ndef get_cwd(): return os.getcwd()\n"
        new = "\ndef get_cwd(): return os.getcwd()\n"
        self.assertFalse(is_formatting_change(old, new, "test.py"))

    def test_negative_used_import_removal_js(self):
        old = "import { a } from './utils';\nconst x = a();\n"
        new = "const x = a();\n"
        self.assertFalse(is_formatting_change(old, new, "test.js"))

    def test_negative_swapped_import_module(self):
        old = "from lib_v1 import helper\ndef f(): return helper()\n"
        new = "from lib_v2 import helper\ndef f(): return helper()\n"
        self.assertFalse(is_formatting_change(old, new, "test.py"))

    def test_negative_logic_changed_with_import_reorder(self):
        old = "import sys\nimport os\n\ndef get_cwd(): return os.getcwd()\n"
        new = "import os\nimport sys\n\ndef get_cwd(): return os.name\n"
        self.assertFalse(is_formatting_change(old, new, "test.py"))

    # --- Category 3: Comments, Docs & Annotations ---

    # 1. Typo & Grammar Fixes
    def test_typo_fix_in_inline_comment_python(self):
        old = "def calculate(x):\n    # Calcluate the doulbed value\n    return x * 2\n"
        new = "def calculate(x):\n    # Calculate the doubled value\n    return x * 2\n"
        self.assertTrue(is_formatting_change(old, new, "calc.py"))

    def test_grammar_fix_in_inline_comment_js(self):
        old = "function getUser() {\n  // this functions are returning user\n  return api.user;\n}"
        new = "function getUser() {\n  // this function returns the user\n  return api.user;\n}"
        self.assertTrue(is_formatting_change(old, new, "user.js"))

    def test_typo_fix_in_docstring_python(self):
        old = 'def get_company():\n    """Return the user compnay name."""\n    return "Acme"\n'
        new = 'def get_company():\n    """Return the user company name."""\n    return "Acme"\n'
        self.assertTrue(is_formatting_change(old, new, "company.py"))

    def test_markdown_file_typo_and_grammar_fixes(self):
        old = "# Documentation\n\nThere is a smal eror in this documnetation.\n"
        new = "# Documentation\n\nThere is a small error in this documentation.\n"
        self.assertTrue(is_formatting_change(old, new, "README.md"))
        self.assertTrue(is_formatting_change(old, new, "docs/guide.rst"))

    # 2. Comment Cleanup
    def test_comment_cleanup_removing_dead_code_python(self):
        old = "def process(x):\n    # old_impl(x)\n    # legacy_calc = x + 1\n    return x * 2\n"
        new = "def process(x):\n    return x * 2\n"
        self.assertTrue(is_formatting_change(old, new, "calc.py"))

    def test_comment_cleanup_removing_dead_code_js(self):
        old = "function process(x) {\n  // let oldVal = x + 10;\n  // const deprecated = true;\n  return x * 2;\n}"
        new = "function process(x) {\n  return x * 2;\n}"
        self.assertTrue(is_formatting_change(old, new, "calc.js"))

    def test_comment_cleanup_removing_stale_todo_notes(self):
        old = "def fetch_data():\n    # TODO: remove this temporary workaround\n    # NOTE: verify response status\n    return http.get('/data')\n"
        new = "def fetch_data():\n    return http.get('/data')\n"
        self.assertTrue(is_formatting_change(old, new, "api.py"))

    def test_comment_cleanup_stale_todo_js(self):
        old = "const fetchUser = () => {\n  // TODO: completed in ticket 123\n  return api.getUser();\n};"
        new = "const fetchUser = () => {\n  return api.getUser();\n};"
        self.assertTrue(is_formatting_change(old, new, "api.ts"))

    # 3. Pure Type Annotations
    def test_pure_type_annotations_python(self):
        old = "def add(a, b=1):\n    total = a + b\n    return total\n"
        new = "def add(a: int, b: int = 1) -> int:\n    total: int = a + b\n    return total\n"
        self.assertTrue(is_formatting_change(old, new, "calc.py"))

    def test_pure_type_annotations_with_typing_imports_python(self):
        old = "def get_items(keys):\n    res = [k.upper() for k in keys]\n    return res\n"
        new = "from typing import List, Optional\n\ndef get_items(keys: List[str]) -> List[str]:\n    res: List[str] = [k.upper() for k in keys]\n    return res\n"
        self.assertTrue(is_formatting_change(old, new, "items.py"))

    def test_refining_type_annotations_python(self):
        old = "from typing import Any\ndef compute(val: Any) -> Any:\n    return val * 2\n"
        new = "def compute(val: int) -> int:\n    return val * 2\n"
        self.assertTrue(is_formatting_change(old, new, "calc.py"))

    def test_pure_type_annotations_typescript(self):
        old = "async function fetchData(id, retries = 3) {\n  const res = await api.get(id);\n  const data = res.data;\n  return data;\n}"
        new = "interface ApiResponse {\n  data: string;\n}\n\ntype ID = string | number;\n\nasync function fetchData(id: ID, retries: number = 3): Promise<ApiResponse> {\n  const res: ApiResponse = await api.get(id) as ApiResponse;\n  const data: string = res.data;\n  return data;\n}"
        self.assertTrue(is_formatting_change(old, new, "service.ts"))

    # 4. Documentation Formatting
    def test_docstring_reformatting_google_to_sphinx_python(self):
        old = '''def divide(a, b):
    """Divide a by b.

    Args:
        a: Numerator
        b: Denominator

    Returns:
        Result of division
    """
    return a / b
'''
        new = '''def divide(a, b):
    """Divide a by b.

    :param a: Numerator
    :param b: Denominator
    :return: Result of division
    """
    return a / b
'''
        self.assertTrue(is_formatting_change(old, new, "math_utils.py"))

    def test_docstring_reformatting_numpy_style_python(self):
        old = '''def divide(a, b):
    """Divide a by b."""
    return a / b
'''
        new = '''def divide(a, b):
    """Divide a by b.

    Parameters
    ----------
    a : float
        Numerator
    b : float
        Denominator

    Returns
    -------
    float
        Result of division
    """
    return a / b
'''
        self.assertTrue(is_formatting_change(old, new, "math_utils.py"))

    def test_jsdoc_documentation_formatting(self):
        old = "function query(sql) {\n  // Run query\n  return db.execute(sql);\n}"
        new = "/**\n * Executes a SQL query against the primary database.\n * @param {string} sql - SQL query string\n * @returns {Promise<any>}\n */\nfunction query(sql) {\n  return db.execute(sql);\n}"
        self.assertTrue(is_formatting_change(old, new, "db.js"))

    # 5. Negative Cases (Semantic and Logic Changes MUST NOT pass)
    def test_negative_logic_change_with_comment_fix(self):
        old = "def calculate(x):\n    # Calcluate the doulbed value\n    return x * 2\n"
        new = "def calculate(x):\n    # Calculate the doubled value\n    return x * 3\n"
        self.assertFalse(is_formatting_change(old, new, "calc.py"))

    def test_negative_logic_change_with_type_annotation(self):
        old = "def add(a, b=1):\n    return a + b\n"
        new = "def add(a: int, b: int = 2) -> int:\n    return a + b\n"
        self.assertFalse(is_formatting_change(old, new, "calc.py"))

    def test_negative_string_change_outside_docstring(self):
        old = 'def get_message():\n    """Docstring"""\n    return "Welcome"\n'
        new = 'def get_message():\n    """Updated docstring"""\n    return "Goodbye"\n'
        self.assertFalse(is_formatting_change(old, new, "msg.py"))

    def test_negative_logic_change_with_ts_types(self):
        old = "function fetchData(id) { return api.get(id); }"
        new = "function fetchData(id: string): Promise<any> { return api.post(id); }"
        self.assertFalse(is_formatting_change(old, new, "api.ts"))

    # ========================================================================
    # Category 4: Non-Code & Metadata Files
    # ========================================================================
    def test_git_metadata_and_ignore_files(self):
        old_ignore = "# Ignore node_modules\nnode_modules/\n"
        new_ignore = "# Ignore node_modules and cache\nnode_modules/\n.cache/\n.eslintcache\n"
        self.assertTrue(is_formatting_change(old_ignore, new_ignore, ".gitignore"))
        self.assertTrue(is_formatting_change("*.log\n", "*.log\n.env\n", ".dockerignore"))
        self.assertTrue(is_formatting_change("dist/\n", "dist/\nbuild/\n", ".eslintignore"))
        self.assertTrue(is_formatting_change("* text=auto\n", "* text=auto\n*.png binary\n", ".gitattributes"))
        self.assertTrue(is_formatting_change("John Doe <j@ex.com>\n", "John Doe <j@example.com>\n", ".mailmap"))

    def test_license_and_copyright_headers(self):
        # Dedicated license file
        old_lic = "MIT License\nCopyright (c) 2025 Acme Corp\n"
        new_lic = "MIT License\nCopyright (c) 2026 Acme Corp\n"
        self.assertTrue(is_formatting_change(old_lic, new_lic, "LICENSE"))
        self.assertTrue(is_formatting_change(old_lic, new_lic, "NOTICE"))

        # Header copyright year bump in Python source file
        old_py = "# Copyright (c) 2025 Acme Corp\n# All rights reserved.\n\ndef run():\n    return 42\n"
        new_py = "# Copyright (c) 2025-2026 Acme Corp\n# All rights reserved.\n\ndef run():\n    return 42\n"
        self.assertTrue(is_formatting_change(old_py, new_py, "main.py"))

        # Header copyright year bump in JS source file
        old_js = "// Copyright (c) 2025 Acme Corp\nexport const run = () => 42;\n"
        new_js = "// Copyright (c) 2026 Acme Corp\nexport const run = () => 42;\n"
        self.assertTrue(is_formatting_change(old_js, new_js, "main.js"))

    def test_project_documentation_files(self):
        old_readme = "# Project\n\nWelcome to our library.\n"
        new_readme = "# Project\n\nWelcome to our awesome library! Check docs below.\n"
        self.assertTrue(is_formatting_change(old_readme, new_readme, "README.md"))
        self.assertTrue(is_formatting_change("## v1.0.0\n- Init", "## v1.0.1\n- Bug fix", "CHANGELOG.md"))
        self.assertTrue(is_formatting_change("# Contributing Guide", "# Contributing Guidelines", "CONTRIBUTING.md"))
        self.assertTrue(is_formatting_change("Detailed architecture info", "Detailed architecture flow", "docs/architecture.md"))

    # ========================================================================
    # Category 5: Local Variable & Cosmetic Refactoring (Within Local Scope)
    # ========================================================================
    def test_local_variable_renaming_python(self):
        old = "def process(payload):\n    tmp = sanitize(payload)\n    res = compute(tmp)\n    return res\n"
        new = "def process(payload):\n    user_payload = sanitize(payload)\n    result = compute(user_payload)\n    return result\n"
        self.assertTrue(is_formatting_change(old, new, "process.py"))

        # Loop index renaming: i -> idx
        old_loop = "def iterate(items):\n    for i in range(len(items)):\n        items[i] = items[i] * 2\n"
        new_loop = "def iterate(items):\n    for idx in range(len(items)):\n        items[idx] = items[idx] * 2\n"
        self.assertTrue(is_formatting_change(old_loop, new_loop, "iter.py"))

    def test_local_variable_renaming_javascript(self):
        old = "function calculate(items) {\n  const tmp = items.length;\n  if (tmp > 0) {\n    return tmp * 2;\n  }\n  return 0;\n}"
        new = "function calculate(items) {\n  const count = items.length;\n  if (count > 0) {\n    return count * 2;\n  }\n  return 0;\n}"
        self.assertTrue(is_formatting_change(old, new, "calc.js"))

    def test_inlining_and_extracting_return_locals_python(self):
        # Inlining local return: loc = expr; return loc -> return expr
        old_extracted = "def get_discount(total):\n    val = total * 0.1\n    return val\n"
        new_inlined = "def get_discount(total):\n    return total * 0.1\n"
        self.assertTrue(is_formatting_change(old_extracted, new_inlined, "discount.py"))
        # Extracting return expression into local
        self.assertTrue(is_formatting_change(new_inlined, old_extracted, "discount.py"))

    def test_inlining_and_extracting_return_locals_javascript(self):
        old_extracted = "function sum(a, b) {\n  const res = a + b;\n  return res;\n}"
        new_inlined = "function sum(a, b) {\n  return a + b;\n}"
        self.assertTrue(is_formatting_change(old_extracted, new_inlined, "sum.js"))
        self.assertTrue(is_formatting_change(new_inlined, old_extracted, "sum.js"))

    def test_syntactic_sugar_and_idioms_python(self):
        # == None -> is None
        c1 = "def check(x):\n    if x == None:\n        return True\n"
        c2 = "def check(x):\n    if x is None:\n        return True\n"
        self.assertTrue(is_formatting_change(c1, c2, "check.py"))

        # != None -> is not None
        c3 = "def check(x):\n    if x != None:\n        return True\n"
        c4 = "def check(x):\n    if x is not None:\n        return True\n"
        self.assertTrue(is_formatting_change(c3, c4, "check.py"))

        # not len(arr) -> not arr
        c5 = "def check(arr):\n    if not len(arr):\n        return True\n"
        c6 = "def check(arr):\n    if not arr:\n        return True\n"
        self.assertTrue(is_formatting_change(c5, c6, "check.py"))

        # len(arr) == 0 -> not arr
        c7 = "def check(arr):\n    if len(arr) == 0:\n        return True\n"
        c8 = "def check(arr):\n    if not arr:\n        return True\n"
        self.assertTrue(is_formatting_change(c7, c8, "check.py"))

    def test_syntactic_sugar_and_idioms_javascript(self):
        # val === null || val === undefined -> val == null
        old_null = "function isNil(val) {\n  return val === null || val === undefined;\n}"
        new_null = "function isNil(val) {\n  return val == null;\n}"
        self.assertTrue(is_formatting_change(old_null, new_null, "nil.js"))

    def test_negative_parameter_rename_not_trivial(self):
        # Public parameter rename changes caller interface
        old = "def compute(x):\n    return x * 2\n"
        new = "def compute(y):\n    return y * 2\n"
        self.assertFalse(is_formatting_change(old, new, "calc.py"))

    def test_negative_module_level_var_rename(self):
        # Top-level variables might be exported or imported elsewhere
        old = "API_KEY = 'secret'\n"
        new = "TOKEN = 'secret'\n"
        self.assertFalse(is_formatting_change(old, new, "constants.py"))

    def test_negative_logic_change_in_inlined_expression(self):
        old = "def calc(a, b):\n    val = a + b\n    return val\n"
        new = "def calc(a, b):\n    return a * b\n"
        self.assertFalse(is_formatting_change(old, new, "calc.py"))

    # ========================================================================
    # Category 6: Logging, Print & Debug Cleanup
    # ========================================================================
    def test_debug_artifact_cleanup_python(self):
        old = "def compute(x):\n    print('DEBUG: x is', x)\n    breakpoint()\n    return x * 2\n"
        new = "def compute(x):\n    return x * 2\n"
        self.assertTrue(is_formatting_change(old, new, "calc.py"))

    def test_debug_artifact_cleanup_javascript(self):
        old = "function compute(x) {\n  console.log('DEBUG', x);\n  debugger;\n  return x * 2;\n}"
        new = "function compute(x) {\n  return x * 2;\n}"
        self.assertTrue(is_formatting_change(old, new, "calc.js"))

    def test_log_message_text_polish_python(self):
        old = "def login(user):\n    logger.info('user authenication started')\n    return user.id\n"
        new = "def login(user):\n    logger.info('User authentication started.')\n    return user.id\n"
        self.assertTrue(is_formatting_change(old, new, "auth.py"))

    def test_log_level_downgrade_python(self):
        old = "def sync(event):\n    logger.info('Received sync event', event)\n    return True\n"
        new = "def sync(event):\n    logger.debug('Received sync event', event)\n    return True\n"
        self.assertTrue(is_formatting_change(old, new, "sync.py"))

    def test_log_level_downgrade_javascript(self):
        old = "function sync(event) {\n  logger.info('processing event');\n  return true;\n}"
        new = "function sync(event) {\n  logger.debug('processing event');\n  return true;\n}"
        self.assertTrue(is_formatting_change(old, new, "sync.js"))

    def test_negative_log_arguments_change_not_trivial(self):
        old = "def notify(user):\n    logger.info('User login', user.id)\n"
        new = "def notify(user):\n    logger.info('User login', user.email)\n"
        self.assertFalse(is_formatting_change(old, new, "notify.py"))

    def test_negative_non_log_string_change(self):
        old = "def status():\n    return 'pending'\n"
        new = "def status():\n    return 'completed'\n"
        self.assertFalse(is_formatting_change(old, new, "status.py"))

    # ========================================================================
    # Category 7: Dependency & Config Bumps
    # ========================================================================
    def test_lockfile_updates_trivial(self):
        self.assertTrue(is_formatting_change('{"lock": 1}', '{"lock": 2}', "package-lock.json"))
        self.assertTrue(is_formatting_change('lock = 1', 'lock = 2', "poetry.lock"))
        self.assertTrue(is_formatting_change('version = 3', 'version = 4', "Cargo.lock"))
        self.assertTrue(is_formatting_change('lockfile', 'lockfile updated', "yarn.lock"))
        self.assertTrue(is_formatting_change('lockfile: 5.4', 'lockfile: 5.5', "pnpm-lock.yaml"))
        self.assertTrue(is_formatting_change('{"_meta": {}}', '{"_meta": {"hash": "abc"}}', "Pipfile.lock"))

    def test_patch_version_bumps(self):
        # package.json patch bump
        old_pkg = '{\n  "name": "my-lib",\n  "version": "1.0.2",\n  "dependencies": {}\n}'
        new_pkg = '{\n  "name": "my-lib",\n  "version": "1.0.3",\n  "dependencies": {}\n}'
        self.assertTrue(is_formatting_change(old_pkg, new_pkg, "package.json"))

        # pyproject.toml patch bump
        old_toml = '[project]\nname = "my-pkg"\nversion = "0.4.1"\n'
        new_toml = '[project]\nname = "my-pkg"\nversion = "0.4.2"\n'
        self.assertTrue(is_formatting_change(old_toml, new_toml, "pyproject.toml"))

        # Cargo.toml patch bump
        old_cargo = '[package]\nname = "my-crate"\nversion = "2.1.0"\n'
        new_cargo = '[package]\nname = "my-crate"\nversion = "2.1.1"\n'
        self.assertTrue(is_formatting_change(old_cargo, new_cargo, "Cargo.toml"))

    def test_negative_package_json_dependency_change(self):
        old_pkg = '{\n  "name": "my-lib",\n  "version": "1.0.2",\n  "dependencies": {\n    "express": "^4.17.1"\n  }\n}'
        new_pkg = '{\n  "name": "my-lib",\n  "version": "1.0.3",\n  "dependencies": {\n    "express": "^4.18.2"\n  }\n}'
        self.assertFalse(is_formatting_change(old_pkg, new_pkg, "package.json"))

    def test_negative_non_patch_version_bump(self):
        # Minor bump: 1.0.2 -> 1.1.0
        old_pkg = '{\n  "name": "my-lib",\n  "version": "1.0.2"\n}'
        new_pkg = '{\n  "name": "my-lib",\n  "version": "1.1.0"\n}'
        self.assertFalse(is_formatting_change(old_pkg, new_pkg, "package.json"))

        # Major bump: 1.0.2 -> 2.0.0
        major_pkg = '{\n  "name": "my-lib",\n  "version": "2.0.0"\n}'
        self.assertFalse(is_formatting_change(old_pkg, major_pkg, "package.json"))



class TestGitFormattingIntegration(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ua-fmt-")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, capture_output=True)

        # Create an initial commit with a python file and a js file
        py_content = "def add(a, b):\n    return a + b\n"
        js_content = "const config = { host: 'localhost', port: 8080 };\n"
        with open(os.path.join(self.repo, "math.py"), "w") as f:
            f.write(py_content)
        with open(os.path.join(self.repo, "app.js"), "w") as f:
            f.write(js_content)
        subprocess.run(["git", "add", "."], cwd=self.repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo, capture_output=True)

    def test_pure_formatting_commit_detected(self):
        # Modify math.py with only quote/line break and app.js with only quote/semicolon
        py_fmt = "def add(\n    a,\n    b,\n):\n    return a + b\n"
        js_fmt = 'const config = {\n  host: "localhost",\n  port: 8080,\n}\n'
        with open(os.path.join(self.repo, "math.py"), "w") as f:
            f.write(py_fmt)
        with open(os.path.join(self.repo, "app.js"), "w") as f:
            f.write(js_fmt)
        subprocess.run(["git", "add", "."], cwd=self.repo, capture_output=True)

        os.chdir(self.repo)
        changes = ChangeDetector().detect()
        self.assertTrue(is_commit_formatting_only(changes, self.repo))

    def test_logic_change_commit_not_detected_as_formatting(self):
        py_logic = "def add(a, b):\n    return a + b + 1\n"
        with open(os.path.join(self.repo, "math.py"), "w") as f:
            f.write(py_logic)
        subprocess.run(["git", "add", "."], cwd=self.repo, capture_output=True)

        os.chdir(self.repo)
        changes = ChangeDetector().detect()
        self.assertFalse(is_commit_formatting_only(changes, self.repo))

    def test_new_file_added_is_not_formatting_only(self):
        with open(os.path.join(self.repo, "new_file.py"), "w") as f:
            f.write("def helper(): pass\n")
        subprocess.run(["git", "add", "new_file.py"], cwd=self.repo, capture_output=True)

        os.chdir(self.repo)
        changes = ChangeDetector().detect()
        self.assertFalse(is_commit_formatting_only(changes, self.repo))

    def test_context_builder_skips_formatting_only_functions(self):
        # math.py has add(a, b) formatted only, and a new function multiply(a, b) added
        py_mixed = "def add(\n    a,\n    b\n):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n"
        with open(os.path.join(self.repo, "math.py"), "w") as f:
            f.write(py_mixed)
        subprocess.run(["git", "add", "math.py"], cwd=self.repo, capture_output=True)

        os.chdir(self.repo)
        changes = ChangeDetector().detect()
        graph = CodeGraph()
        graph.build(changes.get("files") or [])
        context = ContextBuilder().build(changes, graph)

        struct_changes = context.get("structured_changes", [])
        changed_functions = [sc.get("function") for sc in struct_changes]
        # multiply should be present (real logic), but add should be skipped (formatting only)
        self.assertIn("multiply", changed_functions)
        self.assertNotIn("add", changed_functions)

    def test_import_reorganization_commit_detected(self):
        # Initial commit had math.py: def add(a, b): return a + b
        # Update math.py to add reordered imports and unused import removal
        py_imp = "import sys\nimport os\n\ndef add(a, b):\n    return a + b\n"
        with open(os.path.join(self.repo, "math.py"), "w") as f:
            f.write(py_imp)
        subprocess.run(["git", "add", "math.py"], cwd=self.repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add imports"], cwd=self.repo, capture_output=True)

        # Now clean up: remove unused sys, reorder
        py_clean = "import os\n\ndef add(a, b):\n    return a + b\n"
        with open(os.path.join(self.repo, "math.py"), "w") as f:
            f.write(py_clean)
        subprocess.run(["git", "add", "math.py"], cwd=self.repo, capture_output=True)

        os.chdir(self.repo)
        changes = ChangeDetector().detect()
        self.assertTrue(is_commit_formatting_only(changes, self.repo))

    def test_context_builder_skips_import_only_module_changes(self):
        # Commit file with unused import sys and os
        py_init = "import sys\nimport os\n\ndef add(a, b):\n    return a + b\n"
        with open(os.path.join(self.repo, "math.py"), "w") as f:
            f.write(py_init)
        subprocess.run(["git", "add", "math.py"], cwd=self.repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial with imports"], cwd=self.repo, capture_output=True)

        # Stage cleanup: remove unused import sys
        py_clean = "import os\n\ndef add(a, b):\n    return a + b\n"
        with open(os.path.join(self.repo, "math.py"), "w") as f:
            f.write(py_clean)
        subprocess.run(["git", "add", "math.py"], cwd=self.repo, capture_output=True)

        os.chdir(self.repo)
        changes = ChangeDetector().detect()
        graph = CodeGraph()
        graph.build(changes.get("files") or [])
        context = ContextBuilder().build(changes, graph)

        struct_changes = context.get("structured_changes", [])
        # Since add is untouched and module only had unused import removed, structured_changes should be empty
        self.assertEqual(struct_changes, [])

    def test_git_staged_comments_and_type_annotations(self):
        py_init = "def multiply(x, y):\n    return x * y\n"
        with open(os.path.join(self.repo, "calc.py"), "w") as f:
            f.write(py_init)
        subprocess.run(["git", "add", "calc.py"], cwd=self.repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init calc"], cwd=self.repo, capture_output=True)

        # Stage docstring, comments, and type hints
        py_typed = (
            'def multiply(x: int, y: int) -> int:\n'
            '    """Multiply two integers together."""\n'
            '    # Return the product\n'
            '    return x * y\n'
        )
        with open(os.path.join(self.repo, "calc.py"), "w") as f:
            f.write(py_typed)
        subprocess.run(["git", "add", "calc.py"], cwd=self.repo, capture_output=True)

        os.chdir(self.repo)
        changes = ChangeDetector().detect()
        self.assertTrue(is_commit_formatting_only(changes, self.repo))

    def test_git_staged_markdown_doc_file(self):
        md_init = "# Title\n\nInitial draft of the documentation.\n"
        with open(os.path.join(self.repo, "README.md"), "w") as f:
            f.write(md_init)
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init readme"], cwd=self.repo, capture_output=True)

        # Stage typo fix in markdown
        md_fixed = "# Title\n\nCorrected version of the documentation with typo fix.\n"
        with open(os.path.join(self.repo, "README.md"), "w") as f:
            f.write(md_fixed)
        subprocess.run(["git", "add", "README.md"], cwd=self.repo, capture_output=True)

        os.chdir(self.repo)
        changes = ChangeDetector().detect()
    def test_git_staged_ignore_file_commit_exempt(self):
        # Adding a cache folder to .gitignore
        with open(os.path.join(self.repo, ".gitignore"), "w") as f:
            f.write(".cache/\nnode_modules/\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.repo, capture_output=True)

        os.chdir(self.repo)
        changes = ChangeDetector().detect()
        self.assertTrue(is_commit_formatting_only(changes, self.repo))

    def test_git_staged_lockfile_commit_exempt(self):
        # Adding a lockfile update
        with open(os.path.join(self.repo, "package-lock.json"), "w") as f:
            f.write('{\n  "name": "demo",\n  "lockfileVersion": 3\n}\n')
        subprocess.run(["git", "add", "package-lock.json"], cwd=self.repo, capture_output=True)

        os.chdir(self.repo)
        changes = ChangeDetector().detect()
        self.assertTrue(is_commit_formatting_only(changes, self.repo))

    def test_git_staged_patch_version_bump_commit_exempt(self):
        pkg_init = '{\n  "name": "app",\n  "version": "1.0.2",\n  "dependencies": {}\n}\n'
        with open(os.path.join(self.repo, "package.json"), "w") as f:
            f.write(pkg_init)
        subprocess.run(["git", "add", "package.json"], cwd=self.repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init package"], cwd=self.repo, capture_output=True)

        pkg_bump = '{\n  "name": "app",\n  "version": "1.0.3",\n  "dependencies": {}\n}\n'
        with open(os.path.join(self.repo, "package.json"), "w") as f:
            f.write(pkg_bump)
        subprocess.run(["git", "add", "package.json"], cwd=self.repo, capture_output=True)

        os.chdir(self.repo)
        changes = ChangeDetector().detect()
        self.assertTrue(is_commit_formatting_only(changes, self.repo))

    def test_git_staged_local_rename_and_debug_cleanup_commit_exempt(self):
        py_init = (
            "def calculate(items):\n"
            "    print('debug items:', items)\n"
            "    tmp = len(items)\n"
            "    return tmp * 2\n"
        )
        with open(os.path.join(self.repo, "calc.py"), "w") as f:
            f.write(py_init)
        subprocess.run(["git", "add", "calc.py"], cwd=self.repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init calc with debug"], cwd=self.repo, capture_output=True)

        # Remove print and rename local tmp -> count
        py_clean = (
            "def calculate(items):\n"
            "    count = len(items)\n"
            "    return count * 2\n"
        )
        with open(os.path.join(self.repo, "calc.py"), "w") as f:
            f.write(py_clean)
        subprocess.run(["git", "add", "calc.py"], cwd=self.repo, capture_output=True)

        os.chdir(self.repo)
        changes = ChangeDetector().detect()
        self.assertTrue(is_commit_formatting_only(changes, self.repo))


if __name__ == "__main__":
    unittest.main()
