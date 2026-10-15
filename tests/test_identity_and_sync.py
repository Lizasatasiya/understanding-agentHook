"""Pokerface P3 tests — identity resolution and central sync client.

Contract:
  - IdentityDetector harvests git author metadata with no interactive
    prompts; email is the primary key; machine_id is a one-way hash.
  - ServerClient central sync: unreachable server enqueues the session;
    the next send drains the queue as a batch before delivering.
  - HMAC signing: X-Signature is HMAC-SHA256 of the canonical payload.
  - Sync failures never raise (commit must proceed).
"""

import json
import os
import subprocess
import tempfile
import unittest

from understanding_agent.identity import IdentityDetector
from understanding_agent.server_client import ServerClient, _sign, _canonical_payload


def _git_repo(tmp, email="dev@company.com", name="Dev A"):
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "config", "user.email", email], cwd=tmp, capture_output=True)
    subprocess.run(["git", "config", "user.name", name], cwd=tmp, capture_output=True)
    return tmp


class TestIdentityDetector(unittest.TestCase):
    def test_harvests_git_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            _git_repo(tmp, email="Dev@Company.com", name="Dev A")
            ident = IdentityDetector.resolve(tmp)
            self.assertEqual(ident["user_email"], "dev@company.com")  # lowercased
            self.assertEqual(ident["user_name"], "Dev A")
            self.assertEqual(ident["system_user"], os.environ.get("USER") or ident["system_user"])
            self.assertTrue(ident["machine_id"])
            self.assertEqual(len(ident["machine_id"]), 12)  # hashed, not raw hostname

    def test_email_fallback_when_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
            ident = IdentityDetector.resolve(tmp)
            self.assertTrue(ident["user_email"].endswith(".local"))
            self.assertIn("@", ident["user_email"])

    def test_repo_slug_from_remote(self):
        with tempfile.TemporaryDirectory() as tmp:
            _git_repo(tmp)
            subprocess.run(
                ["git", "remote", "add", "origin", "git@github.com:acme-corp/billing-service.git"],
                cwd=tmp, capture_output=True,
            )
            ident = IdentityDetector.resolve(tmp)
            self.assertEqual(ident["repo_slug"], "acme-corp/billing-service")
            self.assertEqual(ident["git_platform"], "github")

    def test_repo_slug_fallback_to_dirname(self):
        with tempfile.TemporaryDirectory() as tmp:
            _git_repo(tmp)
            ident = IdentityDetector.resolve(tmp)
            self.assertTrue(ident["repo_slug"])  # dirname fallback, never empty
            self.assertEqual(ident["git_platform"], "custom")


class TestHmacSigning(unittest.TestCase):
    def test_signature_is_deterministic_hmac(self):
        body = _canonical_payload({"b": 1, "a": 2})
        sig = _sign("team-secret", body)
        self.assertEqual(len(sig), 64)
        self.assertEqual(sig, _sign("team-secret", body))
        self.assertNotEqual(sig, _sign("other-secret", body))

    def test_canonical_payload_is_key_sorted(self):
        body = _canonical_payload({"b": 1, "a": 2}).decode()
        self.assertEqual(body, '{"a":2,"b":1}')


class TestSyncQueue(unittest.TestCase):
    def test_unreachable_server_enqueues(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".git"))
            os.environ["UNDERSTANDING_AGENT_SYNC_URL"] = "http://127.0.0.1:9"  # closed port
            os.environ.pop("UNDERSTANDING_AGENT_TELEMETRY_URL", None)
            try:
                # must not raise
                ServerClient().send({"session_id": "s1", "schema_version": 3}, repo_root=tmp)
                queue_path = os.path.join(tmp, ".git", "understanding_sync_queue.json")
                self.assertTrue(os.path.exists(queue_path))
                queue = json.load(open(queue_path))
                self.assertEqual(len(queue), 1)
                self.assertEqual(queue[0]["session_id"], "s1")
                perms = os.stat(queue_path).st_mode & 0o777
                self.assertEqual(perms, 0o600)
            finally:
                os.environ.pop("UNDERSTANDING_AGENT_SYNC_URL", None)

    def test_queue_drains_as_batch_on_next_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".git"))
            queue_path = os.path.join(tmp, ".git", "understanding_sync_queue.json")
            with open(queue_path, "w") as f:
                json.dump([{"session_id": "queued-1"}], f)

            received = []

            import understanding_agent.server_client as sc

            def fake_post(url, session_data):
                received.append((url, session_data))
                return True

            orig_post = sc._post
            sc._post = fake_post
            os.environ["UNDERSTANDING_AGENT_SYNC_URL"] = "http://central.test"
            try:
                ServerClient().send({"session_id": "s2"}, repo_root=tmp)
            finally:
                sc._post = orig_post
                os.environ.pop("UNDERSTANDING_AGENT_SYNC_URL", None)

            # batch drained first, then the new session delivered
            self.assertEqual(len(received), 2)
            self.assertTrue(received[0][0].endswith("/api/v1/sessions/batch"))
            self.assertEqual(received[0][1]["sessions"][0]["session_id"], "queued-1")
            self.assertTrue(received[1][0].endswith("/api/v1/sessions"))
            self.assertEqual(received[1][1]["session_id"], "s2")
            # queue truncated after successful drain
            self.assertEqual(json.load(open(queue_path)), [])

    def test_no_sync_and_no_telemetry_is_silent_noop(self):
        os.environ.pop("UNDERSTANDING_AGENT_SYNC_URL", None)
        os.environ.pop("UNDERSTANDING_AGENT_TELEMETRY_URL", None)
        # must not raise or touch anything
        ServerClient().send({"session_id": "s1"})


if __name__ == "__main__":
    unittest.main()
