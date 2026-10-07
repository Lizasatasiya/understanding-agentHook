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
            '+    db_pass = "kX9mQ2pL7wR4tY8uU3iO6aS5dF1gH0jZ"  # example fixture value\n',
            file="tests/test_billing.py",
        ))
        # Generic/entropy-gated secrets are exempt in test paths (fixtures live
        # there); exact vendor formats are NOT — a real key is real in a test.
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
            "tests/test_keys.py": 'sample_pass = "kX9mQ2pL7wR4tY8uU3iO6aS5dF1gH0jZ"\n',
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


# Fake credentials below are deliberately built by concatenation so no
# complete secret-shaped literal appears in this file (which itself must be
# committable through this very hook).
_P = "A1b2C3d4E5f6G7h8I9j0K1l2"          # 24 chars
_GOOG = "AI" + "za" + "B" * 35            # AIza + 35 chars
_ANTHROPIC = "sk-" + "ant-api03-" + _P + "mNoPqRsTuVwXyZ0123456"
_STRIPE = "sk_" + "live_" + _P + "aB1cD2e3"  # sk_live_ + 28 chars
_SG_PART2 = ("aB1" * 15)[:43]             # exactly 43 word chars
_TG_PART = ("aB1" * 12)[:35]              # exactly 35 word chars
_HIPRI = "kX9" + "mQ2pL7wR4tY8uU3iO6aS5dF1gH0jZ"  # 32 chars, 3+ char classes


class TestExpandedSecurityGate(unittest.TestCase):
    """PLAN.md Workstream B: expanded vendor formats, entropy gate, key files."""

    def setUp(self):
        self.lens = SecurityLens()
        patcher = patch.object(SecurityLens, "_raw_staged_added_lines", return_value={})
        self.mock_raw = patcher.start()
        patcher2 = patch.object(SecurityLens, "_staged_file_names", return_value=[])
        self.mock_files = patcher2.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(patcher2.stop)

    def _raw_ctx(self, raw_map, context=None):
        self.mock_raw.return_value = raw_map
        return self.lens.find_critical_findings(context or {"structured_changes": []})

    def _ctx(self, diff, file="src/billing.py", func="charge"):
        return {"structured_changes": [{"file": file, "function": func, "diff": diff}]}

    # --- B1: vendor formats ---

    def test_google_api_key_blocks(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    key = "%s"\n' % _GOOG))
        self.assertEqual(f[0]["label"], "google-api-key")

    def test_anthropic_token_blocks(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    k = "%s"\n' % _ANTHROPIC))
        self.assertEqual(f[0]["label"], "anthropic-api-token")

    def test_stripe_live_key_blocks(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    stripe.api_key = "%s"\n' % _STRIPE))
        self.assertEqual(f[0]["label"], "stripe-secret-key")

    def test_npm_and_gitlab_tokens_block(self):
        f = self._raw_ctx({
            "config.js": 'tokens: ["npm_' + _P + 'M3n4O5p6Q7r8", "glpat-' + _P + 'aB1cD2e3fG4"]',
        })
        self.assertEqual({x["label"] for x in f}, {"npm-token", "gitlab-token"})

    def test_sendgrid_key_blocks(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    sg = "SG.%s.%s"\n' % (_P[:22], _SG_PART2)))
        self.assertEqual(f[0]["label"], "sendgrid-api-key")

    def test_telegram_bot_token_blocks(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    bot = "123456789:%s"\n' % _TG_PART))
        self.assertEqual(f[0]["label"], "telegram-bot-token")

    def test_too_short_google_prefix_passes(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    key = "AIzaAAAA"\n'))
        self.assertEqual(f, [])

    # --- B3: connection strings ---

    def test_credd_postgres_url_blocks(self):
        pw = "hun" + "ter2"
        f = self.lens.find_critical_findings(self._ctx(
            '+    url = "postgres://admin:%s@db.example.com/prod"\n' % pw))
        self.assertEqual(f[0]["label"], "creded-connection-string")

    def test_bare_postgres_url_passes(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    url = "postgres://db.example.com/prod"\n'))
        self.assertEqual(f, [])

    def test_basic_auth_url_blocks(self):
        pw = "sec" + "retpw"
        f = self.lens.find_critical_findings(self._ctx(
            '+    fetch("https://admin:%s@api.example.com/v1")\n' % pw))
        self.assertEqual(f[0]["label"], "basic-auth-url")

    # --- B2: entropy gate ---

    def test_high_entropy_secret_blocks(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    api_key = "%s"\n' % _HIPRI))
        labels = {x["label"] for x in f}
        self.assertIn("high-entropy-secret", labels)

    def test_placeholder_password_passes(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    password = "your-password-here-placeholder"\n'))
        self.assertEqual(f, [])

    def test_low_entropy_password_passes(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    password = "aaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'))
        self.assertEqual(f, [])

    def test_pragma_comment_escapes(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    api_key = "%s"  # pragma: allow-secret\n' % _HIPRI))
        labels = {x["label"] for x in f}
        self.assertNotIn("high-entropy-secret", labels)

    def test_entropy_gate_test_path_exempt(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    api_key = "%s"\n' % _HIPRI,
            file="tests/test_config.py"))
        labels = {x["label"] for x in f}
        self.assertNotIn("high-entropy-secret", labels)

    def test_vendor_format_still_blocks_in_test_path(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    k = "AKIA' + "ABCDEFGHIJKLMOPQ" + '"\n',
            file="tests/test_aws.py"))
        # exact vendor formats are real keys even in tests
        self.assertEqual(f[0]["label"], "aws-access-key")

    # --- B4: staged key files ---

    def test_staged_pem_blocks(self):
        self.mock_files.return_value = ["certs/server.pem"]
        f = self.lens.find_critical_findings({"structured_changes": []})
        self.assertEqual(f[0]["label"], "staged-key-file")
        self.assertEqual(f[0]["file"], "certs/server.pem")

    def test_staged_id_rsa_blocks(self):
        self.mock_files.return_value = ["id_rsa"]
        f = self.lens.find_critical_findings({"structured_changes": []})
        self.assertEqual(f[0]["label"], "staged-key-file")

    def test_example_key_file_exempt(self):
        self.mock_files.return_value = ["id_rsa.example", "ca.pem.template"]
        f = self.lens.find_critical_findings({"structured_changes": []})
        self.assertEqual(f, [])

    def test_regular_file_passes(self):
        self.mock_files.return_value = ["src/app.py", "README.md"]
        f = self.lens.find_critical_findings({"structured_changes": []})
        self.assertEqual(f, [])

    # --- B6: redaction still holds on new paths ---

    def test_new_findings_redacted(self):
        pw = "hun" + "ter2"
        f = self.lens.find_critical_findings(self._ctx(
            '+    url = "postgres://admin:%s@db.example.com/prod"\n' % pw))
        self.assertNotIn(pw, str(f))
        self.assertTrue(f[0]["redacted"].startswith("postgres"))

    def test_generic_secret_redacted(self):
        f = self.lens.find_critical_findings(self._ctx(
            '+    api_key = "%s"\n' % _HIPRI))
        for finding in f:
            self.assertNotIn(_HIPRI, finding["redacted"])
