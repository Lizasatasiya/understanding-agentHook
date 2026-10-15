import unittest
import os
import json
import tempfile

from understanding_agent.session_store import save_session, load_sessions, get_sessions_file, _prune_sessions


def _mk_session(i):
    return {
        "session_id": f"sess_{i}",
        "attempt_id": f"sess_{i}",
        "attempt_number": i,
        "commit_id": "staged",
        "head_commit": f"head{i}",
        "timestamp": f"2026-10-0{i % 9 + 1}T10:00:00",
        "status": "FAILED",
        "score": 10,
        "questions": [],
    }


class TestSessionPrune(unittest.TestCase):
    def test_prune_keeps_most_recent(self):
        sessions = [_mk_session(i) for i in range(1, 11)]
        pruned = _prune_sessions(sessions)  # default limit 500: no prune
        self.assertEqual(len(pruned), 10)
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"), exist_ok=True)
            with patch_dict(os.environ, {"UNDERSTANDING_AGENT_MAX_SESSIONS": "3"}):
                for i in range(1, 6):
                    save_session(d, _mk_session(i))
                loaded = load_sessions(d)
            self.assertEqual(len(loaded), 3)
            kept_ids = {s["session_id"] for s in loaded}
            self.assertEqual(kept_ids, {"sess_3", "sess_4", "sess_5"})

    def test_prune_disabled_when_limit_too_small(self):
        sessions = [_mk_session(i) for i in range(1, 10)]
        with patch_dict(os.environ, {"UNDERSTANDING_AGENT_MAX_SESSIONS": "1"}):
            self.assertEqual(len(_prune_sessions(sessions)), 9)

    def test_prune_keeps_newest_timestamp_over_high_attempt_numbers(self):
        # Regression: attempt_number resets per commit cycle — ranking by it
        # first pruned a brand-new attempt-1 session in favor of OLD sessions
        # with high attempt numbers. Timestamp must dominate.
        old = [_mk_session(i) for i in range(10, 40)]  # 2026-10-0x, attempt 10..39
        for s in old:
            s["timestamp"] = "2026-10-01T09:00:00"
        new = _mk_session(1)
        new["timestamp"] = "2026-10-16T18:00:00"  # newest, attempt_number 1
        with patch_dict(os.environ, {"UNDERSTANDING_AGENT_MAX_SESSIONS": "3"}):
            pruned = _prune_sessions(old + [new])
        self.assertEqual(len(pruned), 3)
        self.assertIn("sess_1", {s["session_id"] for s in pruned},
                      "newest session must survive the prune despite attempt_number=1")

    def test_update_in_place_does_not_grow_file(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"), exist_ok=True)
            s = _mk_session(1)
            s["score"] = 10
            save_session(d, s)
            s["score"] = 90
            save_session(d, s)
            loaded = load_sessions(d)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["score"], 90)


class patch_dict:
    """Minimal patch.dict replacement to avoid importing mock helpers."""
    def __init__(self, env, values):
        self.env, self.values = env, values
        self.saved = None

    def __enter__(self):
        self.saved = {k: self.env.get(k) for k in self.values}
        self.env.update(self.values)

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                self.env.pop(k, None)
            else:
                self.env[k] = v
        return False


if __name__ == "__main__":
    unittest.main()
