import unittest
from understanding_agent.question_generator import QuestionGenerator


class TestQuestionCountAndRatio(unittest.TestCase):
    def setUp(self):
        self.generator = QuestionGenerator()

    def test_diff_scale_small_change(self):
        # Very small diff: 10 lines
        ctx_very_small = {"stats": {"total_loc": 10}, "structured_changes": [{"file": "a.js", "function": "foo", "diff": "+ 10 lines"}]}
        scale, count = self.generator.get_diff_scale(ctx_very_small)
        self.assertEqual(scale, "small")
        self.assertIn(count, [2, 3])

        # Small diff: 25 lines
        ctx_small = {"stats": {"total_loc": 25}, "structured_changes": [{"file": "a.js", "function": "foo", "diff": "+ 25 lines"}]}
        scale, count = self.generator.get_diff_scale(ctx_small)
        self.assertEqual(scale, "small")
        self.assertIn(count, [2, 3])

    def test_diff_scale_medium_change(self):
        # Medium diffs: 35, 60, 95 lines -> should be 3 to 5 questions
        for loc in (35, 60, 95):
            ctx = {"stats": {"total_loc": loc}, "structured_changes": [{"file": "a.js", "function": "bar", "diff": "+ lines"}]}
            scale, count = self.generator.get_diff_scale(ctx)
            self.assertEqual(scale, "medium")
            self.assertGreaterEqual(count, 3)
            self.assertLessEqual(count, 5)

    def test_diff_scale_large_change(self):
        # Large diffs: 105, 180, 500 lines -> more than 5 and less than 10
        for loc in (105, 180, 500):
            ctx = {"stats": {"total_loc": loc}, "structured_changes": [{"file": "a.js", "function": "baz", "diff": "+ lines"}]}
            scale, count = self.generator.get_diff_scale(ctx)
            self.assertEqual(scale, "large")
            self.assertGreater(count, 5)
            self.assertLess(count, 10)

    def test_question_ratio_with_no_violations(self):
        # When no violations are present: 100% diff questions, 0% standards questions
        for target_count in (2, 3, 4, 5, 6, 7, 8):
            std_cnt, diff_cnt = self.generator.get_standards_and_diff_counts(target_count, [])
            self.assertEqual(std_cnt, 0)
            self.assertEqual(diff_cnt, target_count)

    def test_question_ratio_with_many_violations(self):
        # Even with many violations (e.g. 8 failed), at most 20% are standards questions
        violations = [{"name": f"Violation_{i}", "details": "desc"} for i in range(8)]
        for target_count in (2, 3, 4, 5, 6, 7, 8):
            std_cnt, diff_cnt = self.generator.get_standards_and_diff_counts(target_count, violations)
            self.assertEqual(std_cnt + diff_cnt, target_count)
            # Standards questions must not exceed ~20-25%
            self.assertLessEqual(std_cnt / target_count, 0.35)
            # Diff questions must be the majority (>= 65-80%)
            self.assertGreaterEqual(diff_cnt, std_cnt)

    def test_generate_large_commit_with_violations(self):
        # Simulating UserProfileBadge commit: 106 lines + 8 failed standards
        ctx = {
            "stats": {"total_loc": 106, "total_added": 106, "total_deleted": 0, "is_large_change": True},
            "file_summary": [{"file": "src/components/UserProfileBadge.jsx", "functions": ["UserProfileBadge"]}],
            "structured_changes": [
                {
                    "file": "src/components/UserProfileBadge.jsx",
                    "function": "UserProfileBadge",
                    "diff": "+ export function UserProfileBadge(props) { ... 106 lines ... }",
                    "dependency_summary": "React hooks"
                }
            ]
        }
        hints = {
            "standards": [
                {"name": "State & Prop Immutability", "is_good": False, "details": "Mutating props.user"},
                {"name": "React Hooks & Closure Safety", "is_good": False, "details": "Stale closure"},
                {"name": "Resource & Timer Cleanup", "is_good": False, "details": "Missing clearInterval"},
                {"name": "Virtual DOM & Reconciliation Safety", "is_good": False, "details": "Random key"},
                {"name": "Modern Variable Scoping", "is_good": False, "details": "var used"},
                {"name": "Strict Equality & Type Safety", "is_good": False, "details": "loose equality =="},
                {"name": "Error Handling & Resilience", "is_good": False, "details": "empty catch"},
                {"name": "Function Length", "is_good": False, "details": "106 lines"}
            ]
        }
        questions = self.generator.generate(ctx, {}, hints)

        # Question count must be > 5 and < 10
        self.assertGreater(len(questions), 5)
        self.assertLess(len(questions), 10)

        # At most 20% standards questions (1 out of 6, or 1-2 out of 7/8)
        std_keywords = {"immutability", "closure", "cleanup", "virtual", "scoping", "equality", "resilience", "length"}
        std_q_count = sum(1 for q in questions if any(kw in q["question"].lower() for kw in std_keywords))
        self.assertLessEqual(std_q_count, 2)
        diff_q_count = len(questions) - std_q_count
        self.assertGreaterEqual(diff_q_count, 4)


if __name__ == "__main__":
    unittest.main()
