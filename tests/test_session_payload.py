"""Tests for the versioned session payload builder."""
import unittest

from understanding_agent.session_payload import build_session_payload, SCHEMA_VERSION


def _base(**overrides):
    kwargs = dict(
        attempt_number=2,
        head_commit="abc123",
        commit_message="feat: x",
        attempt_key="key-1",
        diff_hash="deadbeef",
        repository="acme/billing",
        branch="main",
        status="PASSED",
        score=88.0,
        changed_files=["a.py"],
        change_summary={"what_changed": "x"},
        verification="llm-verified",
        standards_report=[],
        violations_count=0,
        questions=[{"question": "q?"}],
    )
    kwargs.update(overrides)
    return build_session_payload(**kwargs)


class TestSessionPayload(unittest.TestCase):
    def test_schema_version_and_identity(self):
        p = _base()
        self.assertEqual(p["schema_version"], SCHEMA_VERSION)
        self.assertEqual(p["session_id"], p["attempt_id"])
        self.assertTrue(p["session_id"].endswith("_2"))

    def test_verification_is_required(self):
        # Callers must state verification — no silent default.
        kwargs = dict(
            attempt_number=1, head_commit="h", commit_message="m",
            attempt_key="k", diff_hash="d", repository="r", branch="b",
            status="FAILED", score=0, changed_files=[], change_summary={},
        )
        with self.assertRaises(TypeError):
            build_session_payload(**kwargs)

    def test_standards_blocked_path_records_unverified(self):
        # The historical bug: this path omitted verification entirely.
        p = _base(status="FAILED", score=0, verification="llm-unverified",
                  change_summary={"what_changed": "blocked"},
                  questions=[])
        self.assertEqual(p["verification"], "llm-unverified")
        self.assertEqual(p["questions"], [])

    def test_analysis_optional(self):
        self.assertNotIn("analysis", _base())
        p = _base(analysis={"classification": {"change_type": "fix"}})
        self.assertEqual(p["analysis"]["classification"]["change_type"], "fix")

    def test_defaults_for_lists(self):
        p = _base(standards_report=None, questions=None)
        self.assertEqual(p["standards_report"], [])
        self.assertEqual(p["questions"], [])


if __name__ == "__main__":
    unittest.main()
