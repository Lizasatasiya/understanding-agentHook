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
