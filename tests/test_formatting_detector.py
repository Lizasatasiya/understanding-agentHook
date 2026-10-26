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


if __name__ == "__main__":
    unittest.main()
