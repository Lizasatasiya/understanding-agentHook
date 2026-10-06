import unittest
import os
from unittest.mock import patch

from understanding_agent.security_lens import (
    SecurityLens, security_gate_enabled,
)


class TestSecurityGate(unittest.TestCase):
    def setUp(self):
        self.lens = SecurityLens()
        # Keep unit tests hermetic: the raw staged-diff scan hits real git.
        patcher = patch.object(SecurityLens, "_raw_staged_added_lines", return_value={})
        self.mock_raw = patcher.start()
        self.addCleanup(patcher.stop)

    def _raw_ctx(self, raw_map, context=None):
        """Test through the raw-scan path (module-level secrets)."""
        self.mock_raw.return_value = raw_map
        return self.lens.find_critical_findings(context or {"structured_changes": []})

    def _ctx(self, diff, file="src/billing.py", func="charge"):
        return {"structured_changes": [{"file": file, "function": func, "diff": diff}]}

    def test_aws_key_blocks(self):
        findings = self.lens.find_critical_findings(self._ctx(
            '+    aws_key = "AKIAIOSFODNN7EXAMPLE"\n'
        ))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["label"], "aws-access-key")
        # Never echo the full secret
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", findings[0]["redacted"])
        self.assertTrue(findings[0]["redacted"].startswith("AKIA"))

    def test_private_key_blocks(self):
        findings = self.lens.find_critical_findings(self._ctx(
            '+-----BEGIN RSA PRIVATE KEY-----\n+MIIEpAIBAAKCAQEA...\n'
        ))
        self.assertEqual(findings[0]["label"], "private-key")

    def test_groq_token_blocks(self):
        findings = self.lens.find_critical_findings(self._ctx(
            '+    client = Groq(api_key="gsk_' + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2" + '")\n'
        ))
        self.assertEqual(findings[0]["label"], "groq-api-token")

    def test_github_and_openai_tokens_block(self):
        findings = self.lens.find_critical_findings(self._ctx(
            '+    t1 = "ghp_' + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2" + '"\n'
            '+    t2 = "sk-proj-' + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2w3x4" + '"\n'
        ))
        labels = {f["label"] for f in findings}
        self.assertEqual(labels, {"github-token", "openai-api-token"})

    def test_benign_code_passes_gate(self):
        findings = self.lens.find_critical_findings(self._ctx(
            '+    total = sum(items)\n+    return total / len(items)\n'
        ))
        self.assertEqual(findings, [])

    def test_test_files_exempt(self):
        findings = self.lens.find_critical_findings(self._ctx(
            '+    key = "AKIAIOSFODNN7EXAMPLE"  # example fixture key\n',
            file="tests/test_billing.py",
        ))
        self.assertEqual(findings, [])

    def test_docs_not_exempt_real_keys_block(self):
        # A real key in a docs file is still a real key — docs get published.
        findings = self.lens.find_critical_findings(self._ctx(
            '+Put your key here: AKIAIOSFODNN7EXAMPLE\n',
            file="docs/setup.md",
        ))
        self.assertEqual(findings[0]["label"], "aws-access-key")

    def test_removed_lines_not_flagged(self):
        # '-' lines (removing a secret) must not block — removal is good!
        findings = self.lens.find_critical_findings(self._ctx(
            '-    aws_key = "AKIAIOSFODNN7EXAMPLE"\n'
        ))
        self.assertEqual(findings, [])

    def test_module_level_secret_in_raw_diff_blocks(self):
        # Regression: secrets OUTSIDE any function (module level) must be caught
        # via the raw staged-diff scan — the per-function context misses them.
        findings = self._raw_ctx({
            "billing.py": 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n',
        })
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["label"], "aws-access-key")
        self.assertEqual(findings[0]["file"], "billing.py")

    def test_raw_scan_skips_test_paths(self):
        findings = self._raw_ctx({
            "tests/test_keys.py": 'sample = "AKIAIOSFODNN7EXAMPLE"\n',
        })
        self.assertEqual(findings, [])

    def test_function_attribution_preserved_when_available(self):
        # Both scans see it: raw finds it, context scan adds function attribution
        findings = self.lens.find_critical_findings(self._ctx(
            '+    key = "AKIAIOSFODNN7EXAMPLE"\n'
        ))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["function"], "charge")

    def test_gate_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UNDERSTANDING_AGENT_SECURITY_GATE", None)
            self.assertTrue(security_gate_enabled())

    def test_gate_disable_via_env(self):
        with patch.dict(os.environ, {"UNDERSTANDING_AGENT_SECURITY_GATE": "off"}):
            self.assertFalse(security_gate_enabled())


if __name__ == "__main__":
    unittest.main()
