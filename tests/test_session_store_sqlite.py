"""Pokerface P3 — SQLite local store tests (decision B).

Contract: the local session store is SQLite (.git/understanding_sessions.db,
0600 perms). A legacy JSON file is imported once on first open and renamed
to .imported (backup, never deleted). JSON remains an export format only.
"""

import json
import os
import sqlite3
import stat
import tempfile
import unittest

from understanding_agent.session_store import (
    export_sessions,
    get_sessions_db,
    load_sessions,
    save_session,
)


def _mk(i, ts="2026-10-16T10:00:00"):
    return {
        "session_id": f"sess_db_{i}",
        "attempt_id": f"sess_db_{i}",
        "attempt_number": i,
        "commit_id": "staged",
        "head_commit": f"head{i}",
        "timestamp": ts,
        "status": "FAILED",
        "score": 0,
        "questions": [],
    }


class TestSQLiteStore(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            s = _mk(1)
            save_session(d, s)
            loaded = load_sessions(d)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["session_id"], "sess_db_1")
            self.assertEqual(loaded[0]["head_commit"], "head1")

    def test_store_is_sqlite_with_restricted_perms(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            save_session(d, _mk(1))
            db = get_sessions_db(d)
            self.assertTrue(os.path.exists(db))
            self.assertEqual(stat.S_IMODE(os.stat(db).st_mode) & 0o777, 0o600)

    def test_no_plaintext_json_created(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            save_session(d, _mk(1))
            self.assertFalse(os.path.exists(os.path.join(d, ".git", "understanding_sessions.json")))

    def test_update_in_place_by_session_id(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            s = _mk(1)
            save_session(d, s)
            s["status"] = "PASSED"
            s["score"] = 90
            save_session(d, s)
            loaded = load_sessions(d)
            self.assertEqual(len(loaded), 1)  # updated, not appended
            self.assertEqual(loaded[0]["status"], "PASSED")
            self.assertEqual(loaded[0]["score"], 90)

    def test_legacy_json_migrated_once_and_backed_up(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            legacy = [_mk(i) for i in range(3)]
            legacy_path = os.path.join(d, ".git", "understanding_sessions.json")
            with open(legacy_path, "w") as f:
                json.dump(legacy, f)
            loaded = load_sessions(d)
            self.assertEqual(len(loaded), 3)
            # JSON renamed to .imported backup, not deleted
            self.assertFalse(os.path.exists(legacy_path))
            self.assertTrue(os.path.exists(legacy_path + ".imported"))
            # second open does not re-import (backup has different name)
            save_session(d, _mk(9, ts="2026-10-16T11:00:00"))
            loaded2 = load_sessions(d)
            self.assertEqual(len(loaded2), 4)
            ids = {s["session_id"] for s in loaded2}
            self.assertIn("sess_db_9", ids)

    def test_export_to_json(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            save_session(d, _mk(1))
            save_session(d, _mk(2, ts="2026-10-16T10:05:00"))
            out = os.path.join(d, "export.json")
            count = export_sessions(d, out)
            self.assertEqual(count, 2)
            data = json.load(open(out))
            self.assertEqual(len(data), 2)

    def test_corrupt_db_falls_back_to_legacy_json(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".git"))
            # a file where the db should be, unreadable as sqlite
            with open(get_sessions_db(d), "w") as f:
                f.write("this is not a database" * 100)
            legacy = [_mk(1)]
            with open(os.path.join(d, ".git", "understanding_sessions.json"), "w") as f:
                json.dump(legacy, f)
            loaded = load_sessions(d)
            # falls back to reading the legacy JSON rather than raising
            self.assertEqual([s["session_id"] for s in loaded], ["sess_db_1"])


if __name__ == "__main__":
    unittest.main()
