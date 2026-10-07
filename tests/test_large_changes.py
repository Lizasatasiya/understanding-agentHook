import unittest
from unittest.mock import patch, MagicMock
from understanding_agent.change_detector import ChangeDetector
from understanding_agent.context_builder import ContextBuilder
from understanding_agent.change_summary import ChangeSummary
from understanding_agent.question_generator import QuestionGenerator
from understanding_agent.interaction import Interaction


class TestLargeChangesPipeline(unittest.TestCase):

    def test_change_detector_scale_metrics(self):
        detector = ChangeDetector()
        
        def sub_handler(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "--name-status" in cmd_str:
                return "M\tapp/test_service.py\n"
            elif "--numstat" in cmd_str:
                return "95\t15\tapp/test_service.py\n"
            elif "git show :" in cmd_str:
                return "def func_a():\n    pass\n\ndef func_b():\n    return 42\n"
            elif "git show HEAD:" in cmd_str:
                return "def func_a():\n    pass\n"
            elif "-U0" in cmd_str:
                return "@@ -2,0 +3,10 @@\n+def func_b():\n+    return 42\n"
            return ""

        with patch("subprocess.check_output", side_effect=sub_handler):
            result = detector.detect()
            self.assertEqual(len(result["files"]), 1)
            stats = result["stats"]
            self.assertEqual(stats["total_added"], 95)
            self.assertEqual(stats["total_deleted"], 15)
            self.assertEqual(stats["total_loc"], 110)
            self.assertTrue(stats["is_large_change"])

    def test_context_builder_compression(self):
        builder = ContextBuilder()
        
        # Long diff with 50 lines
        long_diff = "--- a/file.py\n+++ b/file.py\n" + "\n".join([f"+    item_{i} = compute_value({i})" for i in range(50)])
        compressed = builder._compress_diff(long_diff, max_lines=20)
        
        self.assertLessEqual(len(compressed.splitlines()), 25)
        self.assertIn("compressed", compressed)

    def test_change_summary_unified_for_large_change(self):
        summary_gen = ChangeSummary()
        
        context = {
            "is_large_change": True,
            "stats": {"total_added": 120, "total_deleted": 10, "total_loc": 130},
            "file_summary": [{"file": "app/cart.py", "functions": ["checkout", "calculate"]}],
            "structured_changes": [
                {"file": "app/cart.py", "function": "checkout", "diff": "+ checkout()"},
                {"file": "app/cart.py", "function": "calculate", "diff": "+ calculate()"}
            ]
        }
        
        # Offline fallback test
        summary = summary_gen.generate(context)
        self.assertIn("what_changed", summary)
        self.assertIn("impact", summary)
        self.assertIn("key_risks", summary)
        # All structured changes should share the unified summary
        self.assertEqual(context["structured_changes"][0]["summary"], summary)
        self.assertEqual(context["structured_changes"][1]["summary"], summary)

    def test_question_generator_caps_for_large_change(self):
        q_gen = QuestionGenerator()
        
        context = {
            "is_large_change": True,
            "stats": {"total_added": 140, "total_deleted": 20, "total_loc": 160},
            "file_summary": [{"file": "app/cart.py", "functions": ["checkout"]}],
            "structured_changes": [
                {"file": "app/cart.py", "function": "checkout", "diff": "+ checkout()"}
            ]
        }
        summary = {"what_changed": "Refactored checkout", "impact": "Faster checkout", "key_risks": "Edge case on empty"}
        
        # Fallback questions when GROQ_API_KEY is not set or offline
        questions = q_gen.generate(context, summary)
        self.assertGreater(len(questions), 5)
        self.assertLess(len(questions), 10)
        for q in questions:
            self.assertIn(q["type"], ["System Architecture", "Invariants", "Reasoning", "Change Impact", "Edge Cases", "Code Logic", "Data Flow", "Dependencies", "What-if"])

    def test_interaction_dashboard_rendering(self):
        interaction = Interaction()
        context = {
            "is_large_change": True,
            "stats": {"total_added": 140, "total_deleted": 20, "total_loc": 160},
            "file_summary": [{"file": "app/cart.py", "functions": ["checkout"]}],
            "summary": {"what_changed": "Refactored checkout", "impact": "Faster checkout", "why_it_matters": "Improves UX", "key_risks": "Empty cart error"},
            "structured_changes": []
        }
        # Verify print_context does not raise exception
        interaction.print_context(context)


if __name__ == "__main__":
    unittest.main()
