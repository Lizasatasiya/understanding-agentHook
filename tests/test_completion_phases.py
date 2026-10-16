"""Phases 6-8 (PLAN_V1_COMPLETION.md): backfill CLI, retention endpoint,
push guard.
"""

import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from service.app import ServiceState, create_app
from understanding_agent.push_guard import precommit_hook_status
from understanding_agent.sync_history import collect_backfill_sessions
from understanding_agent.session_store import save_session


def _payload(sid, email=None, repo="acme/billing"):
    return {
        "session_id": sid, "schema_version": 3, "attempt_number": 1,
        "commit_id": "staged", "head_commit": "abc", "status": "PASSED",
        "verification": "llm-verified", "score": 80.0, "questions": [],
        "identity": ({"user_email": email, "repo_slug": repo} if email else {}),
    }


def _payload_with_question(sid):
    p = _payload(sid, email="dev@company.com")
    p["questions"] = [{
        "question_id": "q1", "question": "What does X do?",
        "answer": "It validates the token",
        "evaluation": {"score": 90, "llm_verified": True},
    }]
    return p


class TestBackfill(unittest.TestCase):
    def test_legacy_sessions_get_git_author_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
            subprocess.run(["git", "config", "user.email", "legacy@corp.io"], cwd=tmp, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Legacy Dev"], cwd=tmp, capture_output=True)
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "c1"],
                           cwd=tmp, capture_output=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp,
                                  capture_output=True, text=True).stdout.strip()
            # legacy row: no identity block
            save_session(tmp, {"session_id": "sess_legacy_1", "attempt_number": 1,
                               "commit_id": head, "head_commit": head,
                               "status": "PASSED", "questions": []})
            rows = collect_backfill_sessions(tmp)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["identity"]["user_email"], "legacy@corp.io")

    def test_unresolvable_author_is_never_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
            save_session(tmp, {"session_id": "sess_x", "attempt_number": 1,
                               "commit_id": "staged", "head_commit": "",
                               "status": "FAILED", "questions": []})
            rows = collect_backfill_sessions(tmp)
            self.assertEqual(rows[0]["identity"]["user_email"], "unknown@local.dev")


class TestRetentionEndpoint(unittest.TestCase):
    def test_admin_purge_endpoint(self):
        import datetime
        with tempfile.TemporaryDirectory() as tmp:
            state = ServiceState(database_url=f"sqlite:///{tmp}/l.db")
            state.admin_token = "adm"
            client = TestClient(create_app(state=state))
            client.post("/api/v1/sessions", json=_payload_with_question("s1"))
            from service.models import SessionRecord
            db = state.SessionLocal()
            record = db.query(SessionRecord).one()
            record.created_at = datetime.datetime(2020, 1, 1)
            db.commit()
            r = client.post("/api/v1/admin/purge", headers={"X-Admin-Token": "adm"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["purged"], 1)
            self.assertEqual(r.json()["retention_days"], 90)
            # wrong token rejected
            r2 = client.post("/api/v1/admin/purge", headers={"X-Admin-Token": "nope"})
            self.assertEqual(r2.status_code, 401)


class TestPushGuard(unittest.TestCase):
    def test_healthy_when_precommit_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
            hooks = os.path.join(tmp, ".git", "hooks")
            with open(os.path.join(hooks, "pre-commit"), "w") as f:
                f.write("#!/bin/sh\npre-commit run\n")
            status = precommit_hook_status(tmp)
            self.assertTrue(status["healthy"])

    def test_missing_hook_fails_with_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
            status = precommit_hook_status(tmp)
            self.assertFalse(status["healthy"])
            self.assertIn("no pre-commit hook", status["reason"])

    def test_foreign_hook_is_flagged_not_confused(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
            hooks = os.path.join(tmp, ".git", "hooks")
            with open(os.path.join(hooks, "pre-commit"), "w") as f:
                f.write("#!/bin/sh\necho some-other-tool\n")
            status = precommit_hook_status(tmp)
            self.assertFalse(status["healthy"])
            self.assertIn("does not reference", status["reason"])

    def test_hooks_path_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
            custom = os.path.join(tmp, "custom-hooks")
            os.makedirs(custom)
            subprocess.run(["git", "config", "core.hooksPath", custom],
                           cwd=tmp, capture_output=True)
            status = precommit_hook_status(tmp)
            self.assertFalse(status["healthy"])
            self.assertIn(custom, status["hook_path"])
            with open(os.path.join(custom, "pre-commit"), "w") as f:
                f.write("understanding-agent\n")
            self.assertTrue(precommit_hook_status(tmp)["healthy"])


if __name__ == "__main__":
    unittest.main()
