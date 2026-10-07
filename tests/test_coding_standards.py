import unittest
from io import StringIO
import sys
from understanding_agent.coding_standards import CodingStandardsChecker


class TestCodingStandardsChecker(unittest.TestCase):

    def setUp(self):
        self.checker = CodingStandardsChecker()

    def test_diff_additions_line_numbers(self):
        diff = """--- a/foo.js
+++ b/foo.js
@@ -10,3 +10,4 @@
 context line 10
 context line 11
+added line 12
 context line 13
@@ -20,2 +21,3 @@
 context line 21
+added line 22
"""
        additions = self.checker._get_diff_additions(diff)
        self.assertEqual(len(additions), 2)
        self.assertEqual(additions[0], (12, "added line 12"))
        self.assertEqual(additions[1], (22, "added line 22"))

    def test_offset_to_line(self):
        content = "line 1\nline 2\nline 3\nline 4"
        self.assertEqual(self.checker._offset_to_line(content, 0), 1)
        self.assertEqual(self.checker._offset_to_line(content, 7), 2)  # after 'line 1\n'
        self.assertEqual(self.checker._offset_to_line(content, 14), 3)

    def test_react_immutability_includes_line_number(self):
        diff = """@@ -1,5 +1,6 @@
+function Test(props) {
+  props.user = 'bad';
+}"""
        result = self.checker._check_react_immutability({"Test.jsx": diff}, {})
        self.assertFalse(result["is_good"])
        self.assertTrue(any("Test.jsx:2: Direct mutation of props" in v for v in result["violations"]))

    def test_react_hooks_includes_line_number(self):
        content = """import React, { useCallback } from 'react';

const fn = useCallback(() => {
    return props.foo;
}, []);"""
        result = self.checker._check_react_hooks_integrity({}, {"Test.jsx": content})
        self.assertFalse(result["is_good"])
        self.assertTrue(any("Test.jsx:3: Stale closure in useCallback" in v for v in result["violations"]))

    def test_variable_scoping_and_equality_include_line_numbers(self):
        diff = """@@ -1,5 +1,7 @@
+var x = 10;
+if (x == 10) {}
"""
        res_scope = self.checker._check_variable_scoping({"Test.js": diff}, {})
        res_eq = self.checker._check_strict_equality({"Test.js": diff}, {})
        self.assertFalse(res_scope["is_good"])
        self.assertFalse(res_eq["is_good"])
        self.assertTrue(any("Test.js:1: Legacy `var` keyword" in v for v in res_scope["violations"]))
        self.assertTrue(any("Test.js:2: Loose equality operator" in v for v in res_eq["violations"]))

    def test_python_standards_include_line_numbers(self):
        diff = """@@ -10,3 +10,5 @@
+try:
+    f = open('file.txt')
+except:
+    pass
"""
        res_exc = self.checker._check_python_exception_handling({"test.py": diff}, {})
        res_res = self.checker._check_python_resource_management({"test.py": diff}, {})
        self.assertFalse(res_exc["is_good"])
        self.assertFalse(res_res["is_good"])
        self.assertTrue(any("test.py:12: Bare `except:`" in v for v in res_exc["violations"]))
        self.assertTrue(any("test.py:11: Unmanaged file descriptor `open(...)`" in v for v in res_res["violations"]))

    def test_print_report_outputs_filename_and_line(self):
        results = [{
            "name": "State & Prop Immutability",
            "status": "FAILED",
            "is_good": False,
            "category": "React / State",
            "violations": ["src/App.jsx:15: Direct mutation of props"],
            "details": "src/App.jsx:15: Direct mutation of props"
        }]
        old_stdout = sys.stdout
        sys.stdout = buffer = StringIO()
        try:
            self.checker.print_report(results)
        finally:
            sys.stdout = old_stdout

        output = buffer.getvalue()
        self.assertIn("src/App.jsx:15: Direct mutation of props", output)


class TestStandardsAdditions(unittest.TestCase):
    """PLAN.md A2/A3: new language + complexity checks, changed-function scoped."""

    def setUp(self):
        self.checker = CodingStandardsChecker()

    def test_python_mutable_default_flags(self):
        diff = "@@ -1,3 +1,4 @@\n+def add_item(item, items=[]):\n+    items.append(item)\n"
        res = self.checker._check_python_mutable_defaults({"svc.py": diff}, {})
        self.assertFalse(res["is_good"])
        self.assertIn("Mutable default argument", res["details"])

    def test_python_mutable_default_none_ok(self):
        diff = "@@ -1,3 +1,4 @@\n+def add_item(item, items=None):\n+    return items or []\n"
        res = self.checker._check_python_mutable_defaults({"svc.py": diff}, {})
        self.assertTrue(res["is_good"])

    def test_go_discarded_error_flags(self):
        diff = "@@ -10,3 +10,4 @@\n+func load() {\n+    v, _ := fetch()\n+    use(v)\n"
        res = self.checker._check_go_error_handling({"main.go": diff}, {})
        self.assertFalse(res["is_good"])
        self.assertIn("Error return value discarded", res["details"])

    def test_go_checked_error_ok(self):
        diff = "@@ -10,3 +10,5 @@\n+    v, err := fetch()\n+    if err != nil {\n+        return err\n+    }\n"
        res = self.checker._check_go_error_handling({"main.go": diff}, {})
        self.assertTrue(res["is_good"])

    # --- A3.2: function length, changed-function scoped ---

    def test_long_changed_function_flags(self):
        body = ["def process(data):"] + ["    x = %d" % i for i in range(100)]
        content = "\n".join(body) + "\n"
        # diff says the last line of the function was added
        diff = "@@ -100,1 +100,1 @@\n+    x = 99\n"
        res = self.checker._check_function_length({"svc.py": diff}, {"svc.py": content})
        self.assertFalse(res["is_good"])
        self.assertIn("process()", res["details"])

    def test_short_changed_function_ok(self):
        content = "def process(data):\n    return data + 1\n"
        diff = "@@ -1,2 +1,2 @@\n+    return data + 2\n"
        res = self.checker._check_function_length({"svc.py": diff}, {"svc.py": content})
        self.assertTrue(res["is_good"])

    def test_legacy_500_line_file_one_line_edit_ok(self):
        # A0.2/A3.2 regression: 1-line edit to a huge legacy file must PASS the
        # length check (the old whole-file modularity check FAILED this).
        legacy = "\n".join(["# legacy"] + ["x%d = %d" % (i, i) for i in range(500)])
        content = legacy + "\ndef helper():\n    return 1\n"
        diff = "@@ -502,2 +502,2 @@\n+    return 2\n"
        res = self.checker._check_function_length({"legacy.py": diff}, {"legacy.py": content})
        self.assertTrue(res["is_good"])

    # --- A3.1: nesting depth ---

    def test_deep_nesting_flags(self):
        lines = ["def process(items):"]
        indent = 4
        for i in range(6):
            lines.append(" " * indent + "if a%d:" % i)
            indent += 4
        lines.append(" " * indent + "done()")
        content = "\n".join(lines) + "\n"
        diff = "@@ -1,8 +1,9 @@\n" + "".join("+%s\n" % l for l in lines[1:])
        res = self.checker._check_nesting_depth({"svc.py": diff}, {"svc.py": content})
        self.assertFalse(res["is_good"])
        self.assertIn("nests", res["details"])

    def test_flat_function_ok(self):
        content = "def process(items):\n    if not items:\n        return []\n    return items\n"
        diff = "@@ -1,4 +1,5 @@\n+    return items\n"
        res = self.checker._check_nesting_depth({"svc.py": diff}, {"svc.py": content})
        self.assertTrue(res["is_good"])

    # --- A0.2: input validation, changed-function scoped ---

    def test_unguarded_external_input_flags(self):
        content = "def handle(request):\n    return request.body\n"
        diff = "@@ -1,2 +1,3 @@\n+    return request.body\n"
        res = self.checker._check_input_validation({"api.py": diff}, {"api.py": content})
        self.assertFalse(res["is_good"])
        self.assertIn("handle()", res["details"])

    def test_guarded_external_input_ok(self):
        content = "def handle(request):\n    if not request.body:\n        raise ValueError\n    return request.body\n"
        diff = "@@ -1,3 +1,4 @@\n+    if not request.body:\n+        raise ValueError\n"
        res = self.checker._check_input_validation({"api.py": diff}, {"api.py": content})
        self.assertTrue(res["is_good"])

    def test_no_trust_boundary_param_ok(self):
        # internal helper without external-looking params: nothing to demand
        content = "def combine(a, b):\n    return a + b\n"
        diff = "@@ -1,2 +1,3 @@\n+    return a + b\n"
        res = self.checker._check_input_validation({"util.py": diff}, {"util.py": content})
        self.assertTrue(res["is_good"])
