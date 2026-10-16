"""Phase 5 (PLAN_V1_COMPLETION.md) — the violation wire format is pinned in ONE
place: coding_standards.parse_violation / format_violation. Server and any
future consumer must use these, not side regexes.
"""

import unittest

from understanding_agent.coding_standards import format_violation, parse_violation


class TestViolationWireFormat(unittest.TestCase):
    def test_roundtrip_full_form(self):
        s = format_violation("src/app.py", 12, "Mutates props directly", "item.push(x)")
        parsed = parse_violation(s)
        self.assertEqual(parsed["file"], "src/app.py")
        self.assertEqual(parsed["line"], 12)
        self.assertIn("Mutates props directly", parsed["message"])
        self.assertEqual(parsed["actual_code"], "item.push(x)")

    def test_roundtrip_without_code(self):
        s = format_violation("a/b.js", 3, "Deep nesting")
        parsed = parse_violation(s)
        self.assertEqual(parsed["file"], "a/b.js")
        self.assertEqual(parsed["line"], 3)
        self.assertEqual(parsed["actual_code"], "")

    def test_real_producer_shape(self):
        # A literal from coding_standards' own emission shape
        parsed = parse_violation(
            "app.py: handle() takes external input with no validation "
            "(`def handle(req):`)"
        )
        self.assertEqual(parsed["actual_code"], "def handle(req):")
        self.assertIn("external input", parsed["message"])

    def test_unparseable_string_degrades_to_message_only(self):
        parsed = parse_violation("just a plain message")
        self.assertEqual(parsed["file"], "")
        self.assertIsNone(parsed["line"])
        self.assertEqual(parsed["message"], "just a plain message")

    def test_windows_path_with_colon_still_parses_body(self):
        # The format contract is posix paths; a windows path degrades
        # gracefully (message keeps everything) rather than raising.
        parsed = parse_violation("C:\\src\\app.py: no line number here")
        self.assertIn("C:\\src\\app.py", parsed["message"])


if __name__ == "__main__":
    unittest.main()
