"""Pokerface P3b — central ledger service tests (FastAPI TestClient + SQLite).

Contract (PLAN_POKERFACE_JUDAS.md):
  - JIT user/repo provisioning from the payload identity block
  - Idempotent ingestion by session_id
  - Batch endpoint drains the offline queue shape
  - Team token + HMAC signature verification
  - /api/v1/defense returns proof-of-defense rows for a repo
  - Retention: purge_expired_answers nulls old raw answer text, keeps scores
  - Org hierarchy: repos assignable to org/team; members manageable
"""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from service.app import ServiceState, create_app, purge_expired_answers
from service.models import Repository, SessionQuestion, SessionRecord, Team


def _payload(sid="sess_1", email="dev@company.com", repo="acme/billing",
             status="PASSED", verification="llm-verified", score=88.0,
             questions=None):
    return {
        "schema_version": 3,
        "session_id": sid,
        "attempt_id": sid,
        "attempt_number": 1,
        "commit_id": "staged",
        "head_commit": "abc123",
        "commit_message": "feat: x",
        "diff_hash": "d1" * 32,
        "repository": repo,
        "branch": "main",
        "status": status,
        "verification": verification,
        "score": score,
        "changed_files": [{"path": "app.py", "status": "modified"}],
        "change_summary": {"what_changed": "t"},
        "standards_report": [],
        "violations_count": 0,
        "questions": questions if questions is not None else [
            {"question_id": "q1", "question": "What does X do?",
             "answer": "It validates the token",
             "evaluation": {"score": 90, "llm_verified": True}},
        ],
        "identity": {
            "user_email": email,
            "user_name": "Dev A",
            "system_user": "deva",
            "machine_id": "abc123def456",
            "repo_slug": repo,
            "git_platform": "github",
            "remote_url": "git@github.com:acme/billing.git",
        },
    }


class CentralServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(self.tmp.name, "ledger.db")
        state = ServiceState(database_url=f"sqlite:///{db_path}")
        state.team_token = "team-token-1"
        state.team_secret = ""  # HMAC tests set it explicitly
        state.admin_token = "admin-token-1"
        self.client = TestClient(create_app(state=state))
        self.state = state

    def tearDown(self):
        self.tmp.cleanup()

    def _post_session(self, payload, token="team-token-1", signature=None):
        headers = {"X-Team-Token": token}
        if signature:
            headers["X-Signature"] = signature
        return self.client.post("/api/v1/sessions", json=payload, headers=headers)


class TestIngestion(CentralServiceTestCase):
    def test_jit_provisions_user_and_repo(self):
        r = self._post_session(_payload())
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "success")
        self.assertIn("schema_version", body)
        self.assertEqual(body["user_id"], 1)
        self.assertEqual(body["repo_id"], 1)

    def test_idempotent_on_session_id(self):
        self._post_session(_payload())
        self._post_session(_payload(status="FAILED", score=10.0))
        rows = self.state.SessionLocal().query(SessionRecord).all()
        self.assertEqual(len(rows), 1)  # updated, not duplicated
        self.assertEqual(rows[0].status, "FAILED")

    def test_bad_team_token_rejected(self):
        r = self._post_session(_payload(), token="wrong")
        self.assertEqual(r.status_code, 401)

    def test_hmac_signature_verified(self):
        self.state.team_secret = "sec"
        import json, hmac as h
        canonical = json.dumps(_payload(), sort_keys=True,
                                separators=(",", ":")).encode()
        good = h.new(b"sec", canonical, "sha256").hexdigest()
        r = self._post_session(_payload(), signature=good)
        self.assertEqual(r.status_code, 200)
        r2 = self._post_session(_payload(sid="sess_2"), signature="deadbeef")
        self.assertEqual(r2.status_code, 401)

    def test_questions_persisted_with_scores(self):
        self._post_session(_payload())
        qrows = self.state.SessionLocal().query(SessionQuestion).all()
        self.assertEqual(len(qrows), 1)
        self.assertEqual(qrows[0].score, 90.0)
        self.assertEqual(qrows[0].answer_text, "It validates the token")

    def test_batch_ingests_queue_shape(self):
        batch = {"sessions": [_payload(sid=f"sess_b{i}") for i in range(3)]}
        r = self.client.post("/api/v1/sessions/batch", json=batch,
                             headers={"X-Team-Token": "team-token-1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["ingested"], 3)


class TestDefenseLookup(CentralServiceTestCase):
    def test_defense_rows_for_repo(self):
        self._post_session(_payload(sid="s1"))
        self._post_session(_payload(sid="s2", email="dev2@company.com",
                                    status="FAILED", score=12.0,
                                    verification="llm-unverified"))
        r = self.client.get("/api/v1/defense", params={"repo_slug": "acme/billing"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["sessions"]), 2)
        self.assertIn("schema_version", body)
        emails = {s["user_email"] for s in body["sessions"]}
        self.assertEqual(emails, {"dev@company.com", "dev2@company.com"})

    def test_defense_unknown_repo_is_empty_list(self):
        r = self.client.get("/api/v1/defense", params={"repo_slug": "no/such"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["sessions"], [])


class TestOrgHierarchy(CentralServiceTestCase):
    def test_repo_assignment_to_team(self):
        self._post_session(_payload())  # repo JIT-created
        r = self.client.post("/api/v1/admin/repositories", json={
            "repo_slug": "acme/billing", "org": "acme", "team": "payments",
        }, headers={"X-Admin-Token": "admin-token-1"})
        self.assertEqual(r.status_code, 200)
        db = self.state.SessionLocal()
        repo = db.query(Repository).filter_by(repo_slug="acme/billing").one()
        team = db.query(Team).get(repo.team_id)
        self.assertEqual(team.name, "payments")

    def test_member_management(self):
        r = self.client.post("/api/v1/admin/members", json={
            "user_email": "dev@company.com", "team": "payments", "org": "acme",
        }, headers={"X-Admin-Token": "admin-token-1"})
        self.assertEqual(r.status_code, 200)


class TestRetention(unittest.TestCase):
    def test_purge_nulls_old_raw_answers_keeps_scores(self):
        import datetime
        tmp = tempfile.TemporaryDirectory()
        try:
            db_path = os.path.join(tmp.name, "ledger.db")
            state = ServiceState(database_url=f"sqlite:///{db_path}")
            client = TestClient(create_app(state=state))
            client.post("/api/v1/sessions", json=_payload())
            db = state.SessionLocal()
            # backdate the session beyond retention
            record = db.query(SessionRecord).one()
            record.created_at = datetime.datetime(2020, 1, 1)
            db.commit()
            purged = purge_expired_answers(db, retention_days=90)
            self.assertEqual(purged, 1)
            q = db.query(SessionQuestion).one()
            self.assertEqual(q.answer_text, "")       # raw answer nulled
            self.assertEqual(q.score, 90.0)           # aggregate kept
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
