import unittest
import os
import tempfile
import shutil
import json
from understanding_agent.commit_context import (
    compute_attempt_key,
    get_next_attempt_number,
    extract_commit_message_from_args,
    get_head_commit
)
from understanding_agent.session_store import save_session, load_sessions, get_sessions_for_commit


class TestCommitContextAndAttempts(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_extract_commit_message_from_args(self):
        # Single -m
        msg = extract_commit_message_from_args(["git", "commit", "-m", "feat: first message"])
        self.assertEqual(msg, "feat: first message")

        # Multiple -m joined by \n\n
        msg = extract_commit_message_from_args(["git", "commit", "-m", "title", "-m", "description"])
        self.assertEqual(msg, "title\n\ndescription")

        # --message=
        msg = extract_commit_message_from_args(["/usr/bin/git", "commit", "--message=fix: bug"])
        self.assertEqual(msg, "fix: bug")

        # No -m (interactive)
        msg = extract_commit_message_from_args(["git", "commit", "-a"])
        self.assertEqual(msg, "")

        # Not a git commit command
        msg = extract_commit_message_from_args(["python3", "-m", "server.main"])
        self.assertIsNone(msg)

    def test_attempt_number_starts_at_1_for_new_diff_and_message(self):
        head = "commit_aaa"
        diff1 = "diff_111"
        msg1 = "feat: initial commit"

        sessions = []
        attempt = get_next_attempt_number(sessions, head, diff1, msg1)
        self.assertEqual(attempt, 1)

    def test_attempt_number_increments_for_identical_diff_and_message(self):
        head = "commit_aaa"
        diff1 = "diff_111"
        msg1 = "feat: initial commit"
        key = compute_attempt_key(head, diff1, msg1)

        sessions = [
            {
                "session_id": "s1",
                "attempt_number": 1,
                "commit_id": "staged",
                "head_commit": head,
                "diff_hash": diff1,
                "commit_message": msg1,
                "attempt_key": key
            }
        ]
        attempt = get_next_attempt_number(sessions, head, diff1, msg1)
        self.assertEqual(attempt, 2)

        # After attempt 2 recorded, next is 3
        sessions.append({
            "session_id": "s2",
            "attempt_number": 2,
            "commit_id": "staged",
            "head_commit": head,
            "diff_hash": diff1,
            "commit_message": msg1,
            "attempt_key": key
        })
        attempt = get_next_attempt_number(sessions, head, diff1, msg1)
        self.assertEqual(attempt, 3)

    def test_new_commit_message_does_not_reset_attempt(self):
        head = "commit_aaa"
        diff1 = "diff_111"
        msg1 = "feat: first message"
        key1 = compute_attempt_key(head, diff1, msg1)

        sessions = [
            {
                "session_id": "s1",
                "attempt_number": 1,
                "commit_id": "staged",
                "head_commit": head,
                "diff_hash": diff1,
                "commit_message": msg1,
                "attempt_key": key1
            },
            {
                "session_id": "s2",
                "attempt_number": 2,
                "commit_id": "staged",
                "head_commit": head,
                "diff_hash": diff1,
                "commit_message": msg1,
                "attempt_key": key1
            }
        ]

        # User changes commit message to msg2: should continue to Attempt 3!
        msg2 = "feat: revised message"
        attempt = get_next_attempt_number(sessions, head, diff1, msg2)
        self.assertEqual(attempt, 3, "Changing commit message must not reset attempt number")

    def test_new_diff_does_not_reset_attempt(self):
        head = "commit_aaa"
        diff1 = "diff_111"
        msg1 = "feat: first message"
        key1 = compute_attempt_key(head, diff1, msg1)

        sessions = [
            {
                "session_id": "s1",
                "attempt_number": 1,
                "commit_id": "staged",
                "head_commit": head,
                "diff_hash": diff1,
                "commit_message": msg1,
                "attempt_key": key1
            }
        ]

        # User changes code, leading to diff2: still an attempt towards the next commit!
        diff2 = "diff_222"
        attempt = get_next_attempt_number(sessions, head, diff2, msg1)
        self.assertEqual(attempt, 2, "Modifying code should increment attempt towards next commit")

    def test_after_commit_done_new_commits_start_from_attempt_1(self):
        old_head = "commit_aaa"
        diff1 = "diff_111"
        msg1 = "feat: first message"
        key1 = compute_attempt_key(old_head, diff1, msg1)

        sessions = [
            {
                "session_id": "s1",
                "attempt_number": 1,
                "commit_id": "staged",
                "head_commit": old_head,
                "diff_hash": diff1,
                "commit_message": msg1,
                "attempt_key": key1
            },
            {
                "session_id": "s2",
                "attempt_number": 2,
                "commit_id": "staged",
                "head_commit": old_head,
                "diff_hash": diff1,
                "commit_message": msg1,
                "attempt_key": key1
            }
        ]

        # Commit succeeded! HEAD advanced to commit_bbb
        new_head = "commit_bbb"
        new_diff = "diff_333"
        new_msg = "feat: subsequent feature"

        attempt = get_next_attempt_number(sessions, new_head, new_diff, new_msg)
        self.assertEqual(attempt, 1, "New commits after a commit is done must start from attempt 1")

    def test_session_store_save_and_scoped_attempts(self):
        repo = self.test_dir
        os.makedirs(os.path.join(repo, ".git"), exist_ok=True)

        head = "head_x"
        diff1 = "diff_a"
        msg1 = "feat: test"
        key1 = compute_attempt_key(head, diff1, msg1)

        s1 = save_session(repo, {
            "attempt_number": 1,
            "commit_id": "staged",
            "head_commit": head,
            "diff_hash": diff1,
            "commit_message": msg1,
            "attempt_key": key1,
            "status": "FAILED"
        })
        self.assertEqual(s1["attempt_number"], 1)

        # Save second attempt with same diff & message
        s2 = save_session(repo, {
            "attempt_number": 1, # caller sends 1, but should bump to 2 because head matches
            "commit_id": "staged",
            "head_commit": head,
            "diff_hash": diff1,
            "commit_message": msg1,
            "attempt_key": key1,
            "status": "FAILED"
        })
        self.assertEqual(s2["attempt_number"], 2)

        # Now save with different message: should be 3, not reset to 1
        msg2 = "feat: different message"
        key2 = compute_attempt_key(head, diff1, msg2)
        s3 = save_session(repo, {
            "attempt_number": 1,
            "commit_id": "staged",
            "head_commit": head,
            "diff_hash": diff1,
            "commit_message": msg2,
            "attempt_key": key2,
            "status": "PASSED"
        })
        self.assertEqual(s3["attempt_number"], 3)


if __name__ == "__main__":
    unittest.main()
