"""Tests for question-generator checklist emission (RepoProbe protocol)."""
import unittest
from unittest.mock import patch

from understanding_agent.question_generator import QuestionGenerator
from understanding_agent.checklists import parse_checklist


class TestGeneratorChecklists(unittest.TestCase):
    def setUp(self):
        self.gen = QuestionGenerator()
        self.context = {"structured_changes": [], "is_large_change": False}
        self.hints = {
            "security": {
                "include_security_question": True,
                "focus_areas": ["sql-injection-risk", "input-boundary"],
                "ask_about": ["whether the query is parameterized"],
                "top_file": "db.py",
            },
            "evidence": {"evidence_findings": []},
        }

    def test_missing_key_asks_no_questions(self):
        with patch("understanding_agent.question_generator.load_nous_api_key",
                   return_value=""):
            qs = self.gen.generate(self.context, {}, hints=self.hints)
        self.assertEqual(qs, [])

    def test_llm_checklist_kept_when_valid(self):
        llm_q = {
            "question": "Why is the buffer cleared before retry?",
            "type": "Code Logic",
            "expected_concepts": ["buffer clearing"],
            "evaluation_criteria": ["understands retry safety"],
            "checklist": {"items": [
                {"key_point": "explains clearing prevents stale data reuse",
                 "weight": 2, "tiers": [2, 0]},
                {"key_point": "identifies retry ordering risk",
                 "weight": 2, "tiers": [2, 0]},
            ]},
        }
        with patch("understanding_agent.question_generator.load_nous_api_key",
                   return_value="k"), \
             patch.object(QuestionGenerator, "_call_groq", return_value=[llm_q]):
            qs = self.gen.generate(self.context, {})
        self.assertEqual(qs[0]["checklist"]["items"][0]["key_point"],
                         "explains clearing prevents stale data reuse")

    def test_invalid_llm_checklist_replaced_by_derived(self):
        llm_q = {
            "question": "Why is the buffer cleared before retry?",
            "type": "Code Logic",
            "expected_concepts": ["buffer clearing", "retry safety"],
            "evaluation_criteria": ["understands retry safety"],
            "checklist": {"items": [
                {"key_point": "bad item", "weight": 99, "tiers": [99, 0]},
            ]},
        }
        with patch("understanding_agent.question_generator.load_nous_api_key",
                   return_value="k"), \
             patch.object(QuestionGenerator, "_call_groq", return_value=[llm_q]):
            qs = self.gen.generate(self.context, {})
        cl = parse_checklist(qs[0].get("checklist"))
        self.assertIsNotNone(cl)
        self.assertNotIn("bad item", cl.key_points())

    def test_derived_checklist_minimum_items_without_concepts(self):
        q = {"question": "What does this change do to the flow?", "type": "Reasoning"}
        raw = self.gen._derive_checklist(q)
        cl = parse_checklist(raw)
        self.assertIsNotNone(cl)
        self.assertEqual(len(cl.items), 2)

    def test_prompts_request_checklists(self):
        micro = self.gen._build_prompt(self.context, {}, None)
        self.assertIn("checklist", micro)
        macro_ctx = dict(self.context)
        macro_ctx["is_large_change"] = True
        macro_ctx["stats"] = {"total_added": 500, "total_deleted": 100}
        macro = self.gen._build_prompt(macro_ctx, {}, None)
        self.assertIn("checklist", macro)


if __name__ == "__main__":
    unittest.main()
