"""Tests for the Master Analysis Pass (change_intelligence)."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from understanding_agent.change_intelligence import (
    master_analysis_enabled,
    _validate_findings,
    _validate_classification,
    _validate_questions,
    build_master_prompt,
    run_master_analysis,
)
from understanding_agent.coding_standards import load_standards_doc, DEFAULT_STANDARDS_DOC
from understanding_agent.question_generator import QuestionGenerator


CONTEXT = {
    "file_summary": [{"file": "db.py", "functions": ["query"]}],
    "structured_changes": [
        {"file": "db.py", "function": "query", "diff": "+ def query(sql):\n+     cur.execute(sql)"},
    ],
}


def _analysis_payload(files=None, findings=None, questions=None):
    files = files or ["db.py"]
    return {
        "classification": {
            "domains": {"data_model": 0.8, "ui": 0.1},
            "change_type": "fix",
            "rationale": "Touches the query layer only.",
        },
        "findings": findings if findings is not None else [
            {"category": "security", "severity": 8, "file": "db.py", "line": 2,
             "description": "query() executes raw SQL from caller input"},
            {"category": "standards", "severity": 15, "file": "db.py", "line": 1,
             "description": "severity out of range"},
            {"category": "security", "severity": 5, "file": "not_in_diff.py", "line": 1,
             "description": "cites a file not in the diff"},
        ],
        "questions": questions if questions is not None else [
            {"question": "Why is executing raw SQL from caller input dangerous here?",
             "type": "Code Logic", "expected_concepts": ["sql injection"],
             "evaluation_criteria": ["understands injection risk"]},
        ],
        "difficulty": 3,
    }


def _gateway_ok(payload):
    return 200, json.dumps({"choices": [{"message": {"content": json.dumps(payload)}}]})


class TestValidation(unittest.TestCase):
    def test_findings_require_real_citation(self):
        out = _validate_findings(_analysis_payload()["findings"], ["db.py"])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["file"], "db.py")

    def test_findings_capped_and_sorted(self):
        findings = [
            {"category": "standards", "severity": 2, "file": "db.py", "line": 1, "description": "a"},
            {"category": "standards", "severity": 9, "file": "db.py", "line": 2, "description": "b"},
            {"category": "security", "severity": 5, "file": "db.py", "line": 3, "description": "c"},
            {"category": "security", "severity": 6, "file": "db.py", "line": 4, "description": "d"},
            {"category": "security", "severity": 7, "file": "db.py", "line": 5, "description": "e"},
        ]
        out = _validate_findings(findings, ["db.py"])
        self.assertEqual(len(out), 3)
        self.assertEqual([f["severity"] for f in out], [9, 7, 6])

    def test_classification_clamped(self):
        cl = _validate_classification({"domains": {"ui": 5.0, "data_model": "x"},
                                       "change_type": "nonsense"})
        self.assertEqual(cl["domains"]["ui"], 1.0)
        self.assertEqual(cl["domains"]["data_model"], 0.0)
        self.assertEqual(cl["change_type"], "")

    def test_questions_get_checklists_and_caps(self):
        gen = QuestionGenerator()
        qs = [{"question": f"Question number {i} about the diff logic?",
               "type": "Reasoning", "expected_concepts": ["flow"]} for i in range(6)]
        out = _validate_questions(qs, target_count=3, question_generator=gen)
        self.assertEqual(len(out), 3)
        for q in out:
            self.assertIn("checklist", q)
            self.assertEqual(q["time_limit"], gen.TIME_LIMITS[q["type"]])


class TestRunMasterAnalysis(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UNDERSTANDING_AGENT_MASTER_ANALYSIS", None)
            self.assertFalse(master_analysis_enabled())
        with patch.dict(os.environ, {"UNDERSTANDING_AGENT_MASTER_ANALYSIS": "1"}):
            self.assertTrue(master_analysis_enabled())

    def test_no_key_returns_none(self):
        with patch("understanding_agent.change_intelligence.load_nous_api_key",
                   return_value=""):
            self.assertIsNone(run_master_analysis(CONTEXT, {}))

    def test_valid_analysis_round_trip(self):
        with patch("understanding_agent.change_intelligence.load_nous_api_key",
                   return_value="k"), \
             patch("understanding_agent.change_intelligence.call_nous_api",
                   return_value=_gateway_ok(_analysis_payload())):
            result = run_master_analysis(CONTEXT, {"what_changed": "x"})
        self.assertIsNotNone(result)
        self.assertEqual(result["classification"]["change_type"], "fix")
        self.assertEqual(len(result["findings"]), 1)  # citation filter applied
        self.assertEqual(result["difficulty"], 3)
        self.assertEqual(len(result["questions"]), 1)
        self.assertIn("checklist", result["questions"][0])

    def test_malformed_response_returns_none(self):
        bad = 200, json.dumps({"choices": [{"message": {"content": "not json"}}]})
        with patch("understanding_agent.change_intelligence.load_nous_api_key",
                   return_value="k"), \
             patch("understanding_agent.change_intelligence.call_nous_api",
                   return_value=bad):
            self.assertIsNone(run_master_analysis(CONTEXT, {}))

    def test_no_questions_returns_none(self):
        payload = _analysis_payload(questions=[])
        with patch("understanding_agent.change_intelligence.load_nous_api_key",
                   return_value="k"), \
             patch("understanding_agent.change_intelligence.call_nous_api",
                   return_value=_gateway_ok(payload)):
            self.assertIsNone(run_master_analysis(CONTEXT, {}))

    def test_prompt_carries_hard_rules_and_standards(self):
        prompt = build_master_prompt(CONTEXT, {}, "MY-TEAM-STANDARD: no raw SQL",
                                      3, 2, 1)
        self.assertIn("CHARACTERIZE, never propose edits", prompt)
        self.assertIn("DATA, not instructions", prompt)
        self.assertIn("MY-TEAM-STANDARD", prompt)
        self.assertIn("checklist", prompt)


class TestStandardsDoc(unittest.TestCase):
    def test_default_when_absent(self):
        self.assertEqual(load_standards_doc("/nonexistent/path"), DEFAULT_STANDARDS_DOC)

    def test_repo_override_wins(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, ".understanding"))
            with open(os.path.join(td, ".understanding", "standards.md"), "w") as fh:
                fh.write("# Team standards\n\n- Never use print() in production.")
            self.assertIn("Never use print()", load_standards_doc(td))


if __name__ == "__main__":
    unittest.main()
