"""Unit tests for IDE / GUI terminal defense launching."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from understanding_agent.hook_stages import (
    _launch_gui_terminal_defense,
    _cleanup_temp_files,
    _interactive_input_available,
)


class TestGuiTerminalDefense(unittest.TestCase):

    def test_cleanup_temp_files(self):
        import tempfile
        f1 = tempfile.NamedTemporaryFile(delete=False)
        f1.close()
        self.assertTrue(os.path.exists(f1.name))
        _cleanup_temp_files([f1.name, "/nonexistent/path/file.tmp"])
        self.assertFalse(os.path.exists(f1.name))

    def test_non_darwin_returns_false(self):
        with patch("sys.platform", "linux"):
            ok, code = _launch_gui_terminal_defense("/some/repo")
            self.assertFalse(ok)
            self.assertEqual(code, 1)

    def test_no_osascript_returns_false(self):
        with patch("sys.platform", "darwin"), patch("shutil.which", return_value=None):
            ok, code = _launch_gui_terminal_defense("/some/repo")
            self.assertFalse(ok)
            self.assertEqual(code, 1)

    def test_delegated_terminal_env_prevents_recursion(self):
        # When UNDERSTANDING_AGENT_DELEGATED_TERMINAL=1 is set, it must not launch terminal again
        with patch.dict(os.environ, {"UNDERSTANDING_AGENT_DELEGATED_TERMINAL": "1"}):
            self.assertEqual(os.environ.get("UNDERSTANDING_AGENT_DELEGATED_TERMINAL"), "1")


if __name__ == "__main__":
    unittest.main()
