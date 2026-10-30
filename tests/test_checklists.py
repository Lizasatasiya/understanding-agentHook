"""Tests for the Checklist-Based Verification protocol (RepoProbe-derived)."""
import json
import os
import unittest
from unittest.mock import patch

from understanding_agent.checklists import (
    ChecklistItem,
    Checklist,
    parse_checklist,
    aggregate_score,
    verify_offline,
    clarity_offline,
    fallback_banding,
)
from understanding_agent.answer_evaluator import AnswerEvaluator
from understanding_agent.api_utils import get_judge_model


GOOD_RAW = {
    "items": [
        {"key_point": "identifies the retry loop overwriting partial results",
         "weight": 4, "tiers": [4, 2, 0]},
        {"key_point": "mentions the missing error handling for timeouts",
         "weight": 3, "tiers": [3, 0]},
        {"key_point": "explains the propagation of the failure to callers",
         "weight": 2, "tiers": [2, 0]},
    ]
}


class TestChecklistSchema(unittest.TestCase):
    def test_valid_checklist_parses(self):
        cl = parse_checklist(GOOD_RAW)
        self.assertIsNotNone(cl)
        self.assertEqual(len(cl.items), 3)
        self.assertEqual(cl.total_points, 9 + 1)

    def test_rejects_too_few_and_too_many_items(self):
        one = {"items": GOOD_RAW["items"][:1]}
        self.assertIsNone(parse_checklist(one))
        five = {"items": GOOD_RAW["items"] * 2}
        self.assertIsNone(parse_checklist(five))

    def test_rejects_bad_rubric(self):
        bad = {"items": [
            {"key_point": "x", "weight": 4, "tiers": [4, 2]},  # no zero tier
            {"key_point": "y", "weight": 3, "tiers": [3, 0]},
        ]}
        self.assertIsNone(parse_checklist(bad))

    def test_rejects_out_of_range_tier(self):
        bad = {"items": [
            {"key_point": "x", "weight": 2, "tiers": [2, 5, 0]},  # 5 > weight
            {"key_point": "y", "weight": 3, "tiers": [3, 0]},
        ]}
        self.assertIsNone(parse_checklist(bad))

    def test_non_dict_rejected(self):
        self.assertIsNone(parse_checklist(None))
        self.assertIsNone(parse_checklist("items"))
        self.assertIsNone(parse_checklist({"key_point": "no items list"}))


class TestAggregation(unittest.TestCase):
    def setUp(self):
        self.cl = parse_checklist(GOOD_RAW)

    def test_full_marks_is_100(self):
        score = aggregate_score(self.cl, [4, 3, 2], 1)
        self.assertEqual(score, 100)

    def test_partial_aggregation_is_deterministic(self):
        # 4 + 0 + 2 knowledge + 1 clarity = 7 of 10 -> 70
        self.assertEqual(aggregate_score(self.cl, [4, 0, 2], 1), 70)
        # 2 + 3 + 0 + 0 clarity = 5 of 10 -> 50
        self.assertEqual(aggregate_score(self.cl, [2, 3, 0], 0), 50)

    def test_rubric_violation_raises(self):
        with self.assertRaises(ValueError):
            aggregate_score(self.cl, [3, 3, 2], 1)  # 3 not in [4,2,0]

    def test_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            aggregate_score(self.cl, [4, 3], 1)


class TestOfflineVerifier(unittest.TestCase):
    def setUp(self):
        self.cl = parse_checklist(GOOD_RAW)

    def test_good_answer_scores_items(self):
        answer = ("The retry loop overwrites partial results because it does not "
                  "clear the buffer, and there is missing error handling for "
                  "timeouts so the failure propagates to callers as a crash.")
        awarded = verify_offline(self.cl, answer)
        self.assertTrue(all(a > 0 for a in awarded))
        clarity = clarity_offline(answer)
        score = aggregate_score(self.cl, awarded, clarity)
        self.assertGreaterEqual(score, 85)

    def test_unrelated_answer_scores_zero(self):
        awarded = verify_offline(self.cl, "I refactored the styling of the nav bar")
        self.assertTrue(all(a == 0 for a in awarded))
        coverage = sum(awarded) / sum(i.weight for i in self.cl.items)
        score, follow_up = fallback_banding(coverage)
        self.assertEqual(score, 76)  # frozen fail-open baseline
        self.assertFalse(follow_up)

    def test_partial_answer_hits_some_items(self):
        awarded = verify_offline(self.cl, "there is missing error handling for timeouts")
        self.assertGreater(sum(awarded), 0)
        self.assertLess(sum(awarded), sum(i.weight for i in self.cl.items))

    def test_frozen_banding_values(self):
        self.assertEqual(fallback_banding(0.0), (76, False))
        self.assertEqual(fallback_banding(1.0), (94, False))
        score, follow_up = fallback_banding(0.5)
        self.assertEqual(score, 71)  # 70 + int(0.1*20) with float precision
        self.assertTrue(follow_up)


def _judge_body(item_scores, clarity=1):
    """Build a fake gateway response for the checklist judge."""
    result = {
        "items": [
            {"id": i + 1, "rationale": f"evidence {i + 1}", "score": s}
            for i, s in enumerate(item_scores)
        ],
        "clarity": {"rationale": "coherent", "score": clarity},
        "incorrect_claims": [],
        "evaluation": "Solid understanding of the retry behavior.",
    }
    return 200, json.dumps({"choices": [{"message": {"content": json.dumps(result)}}]})


class TestChecklistEvaluator(unittest.TestCase):
    def setUp(self):
        self.evaluator = AnswerEvaluator()
        self.q = {
            "question": "Why can this retry loop corrupt data?",
            "checklist": GOOD_RAW,
            "expected_concepts": ["retry", "error handling"],
        }
        self.context = {"structured_changes": []}

    def _with_key(self):
        return patch("understanding_agent.answer_evaluator.load_groq_api_key",
                     return_value="k")

    def test_llm_checklist_result_aggregates(self):
        with self._with_key(), \
             patch("understanding_agent.answer_evaluator.call_nous_api",
                   return_value=_judge_body([4, 3, 2], clarity=1)):
            res = self.evaluator.evaluate(self.q, {"answer": "because retry overwrites"}, self.context, {})
        self.assertEqual(res.score, 100)
        self.assertTrue(res.llm_verified)
        self.assertEqual(len(res.covered_concepts), 3)
        self.assertFalse(res.follow_up_required)

    def test_out_of_rubric_score_degrades_to_fallback(self):
        # Judge returns 3 for an item whose rubric is [4,2,0] -> invalid.
        with self._with_key(), \
             patch("understanding_agent.answer_evaluator.call_nous_api",
                   return_value=_judge_body([3, 3, 2])), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UNDERSTANDING_AGENT_FAIL_MODE", None)
            res = self.evaluator.evaluate(self.q, {"answer": "retry overwrites partial results"}, self.context, {})
        # Falls back to deterministic offline verification (llm_verified False)
        self.assertFalse(res.llm_verified)
        self.assertIn("fallback", res.evaluation.lower())

    def test_api_failure_falls_back_with_checklist(self):
        with self._with_key(), \
             patch("understanding_agent.answer_evaluator.call_nous_api",
                   return_value=(500, "")):
            res = self.evaluator.evaluate(self.q, {"answer": "retry overwrites partial results"}, self.context, {})
        self.assertFalse(res.llm_verified)
        self.assertGreaterEqual(res.score, 70)

    def test_no_key_uses_offline_checklist_verification(self):
        with patch("understanding_agent.answer_evaluator.load_groq_api_key",
                   return_value=""):
            res = self.evaluator.evaluate(
                self.q,
                {"answer": "the retry loop overwrites partial results; error handling for timeouts is missing so failure propagates to callers"},
                self.context, {})
        self.assertGreaterEqual(res.score, 85)
        self.assertFalse(res.llm_verified)
        self.assertIn("key points", res.evaluation)

    def test_dismissive_checklist_answer_scores_low_with_followup(self):
        with patch("understanding_agent.answer_evaluator.load_groq_api_key",
                   return_value=""):
            res = self.evaluator.evaluate(self.q, {"answer": "idk"}, self.context, {})
        self.assertLessEqual(res.score, 15)
        self.assertTrue(res.follow_up_required)

    def test_followup_band_targets_missing_items(self):
        with self._with_key(), \
             patch("understanding_agent.answer_evaluator.call_nous_api",
                   return_value=_judge_body([2, 0, 0], clarity=1)):
            res = self.evaluator.evaluate(self.q, {"answer": "partial answer"}, self.context, {})
        # 2+1 clarity of 10 -> 30 -> inside frozen 25-70 follow-up band
        self.assertEqual(res.score, 30)
        self.assertTrue(res.follow_up_required)
        self.assertTrue(res.missing_concepts)


class TestJudgeModel(unittest.TestCase):
    def test_judge_model_ignores_separate_judge_env(self):
        with patch.dict(os.environ, {
            "UNDERSTANDING_AGENT_MODEL": "haiku",
            "UNDERSTANDING_AGENT_JUDGE_MODEL": "test-judge-1",
            "UNDERSTANDING_AGENT_IGNORE_DOTENV": "1",
        }):
            self.assertEqual(get_judge_model(), "claude-haiku-5-5")

    def test_judge_model_defaults_to_normal_model(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UNDERSTANDING_AGENT_JUDGE_MODEL", None)
            from understanding_agent.api_utils import get_model
            self.assertEqual(get_judge_model(), get_model())


if __name__ == "__main__":
    unittest.main()
