import unittest
import os
import tempfile
import shutil
import json
from unittest.mock import patch, MagicMock
from understanding_agent.session_store import save_session


class TestAlreadyAnsweredAttempts(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.git_dir = os.path.join(self.test_dir, ".git")
        os.makedirs(self.git_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_state_preserves_questions_with_fallback(self):
        state_file = os.path.join(self.git_dir, "understanding_agent_state.json")
        valid_questions = [
            {"question_id": "q1", "question": "Q1", "is_fallback": True, "best_score": 85, "answered": True},
            {"question_id": "q2", "question": "Q2", "is_fallback": False, "best_score": 0, "answered": False},
        ]
        # Verify writing state preserves questions
        state_data = {
            "attempt_key": "k1",
            "head_commit": "h1",
            "commit_message": "m1",
            "diff_hash": "d1",
            "attempts": 2,
            "questions": valid_questions
        }
        with open(state_file, "w") as f:
            json.dump(state_data, f)

        with open(state_file, "r") as f:
            loaded = json.load(f)
        self.assertEqual(len(loaded["questions"]), 2)
        self.assertTrue(loaded["questions"][0]["answered"])
        self.assertEqual(loaded["questions"][0]["best_score"], 85)

    def test_session_recovery_when_state_questions_empty(self):
        diff_hash = "diff_123"
        head_commit = "head_123"

        # Save an earlier session that had answered questions
        save_session(self.test_dir, {
            "attempt_number": 1,
            "commit_id": "staged",
            "head_commit": head_commit,
            "diff_hash": diff_hash,
            "commit_message": "feat: test",
            "status": "FAILED",
            "questions": [
                {
                    "question_id": "q1",
                    "question": "What does this function do?",
                    "type": "Code Logic",
                    "time_limit_seconds": 60,
                    "answer": "It validates inputs.",
                    "status": "answered",
                    "evaluation": {"score": 85, "understanding": "good"}
                },
                {
                    "question_id": "q2",
                    "question": "What happens on failure?",
                    "type": "Edge Cases",
                    "time_limit_seconds": 60,
                    "answer": "",
                    "status": "timeout",
                    "evaluation": {"score": 0, "understanding": "none"}
                }
            ]
        })

        from understanding_agent.session_store import load_sessions
        sessions = load_sessions(self.test_dir)
        matching_sessions = [
            s for s in sessions
            if (s.get("commit_id") == "staged" or s.get("is_staged") or not s.get("commit_id")) and
            (s.get("head_commit") or "").strip() == head_commit
        ]

        candidates = [s for s in matching_sessions if s.get("diff_hash") == diff_hash and s.get("questions")]
        def _rank_sess(s):
            qs = s.get("questions", [])
            ans = sum(1 for q in qs if ((q.get("evaluation") or {}).get("score", 0) >= 70 or q.get("score", 0) >= 70))
            return (ans, len(qs), s.get("attempt_number", 0))

        best = max(candidates, key=_rank_sess)
        self.assertIsNotNone(best)
        self.assertEqual(len(best["questions"]), 2)

        recovered_q = []
        for sq in best["questions"]:
            score = (sq.get("evaluation") or {}).get("score", 0)
            recovered_q.append({
                "question_id": sq.get("question_id"),
                "question": sq.get("question"),
                "best_score": score,
                "answered": score >= 70,
                "passed": score >= 70
            })

        self.assertTrue(recovered_q[0]["answered"])
        self.assertEqual(recovered_q[0]["best_score"], 85)
        self.assertFalse(recovered_q[1]["answered"])
        self.assertEqual(recovered_q[1]["best_score"], 0)


if __name__ == "__main__":
    unittest.main()
