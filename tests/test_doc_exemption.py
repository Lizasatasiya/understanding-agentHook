"""Regression: docs describing the gate's redacted examples must not block.

The canonical case: committing a README that documents the credential gate
with `postgres://user:***@...` examples was hard-blocked by the gate itself
(this literally blocked the hook repo's own commit). Redacted/example-shaped
connection strings in DOC paths are exempt; real-shaped ones still block,
and vendor formats block everywhere.
"""
import os
import subprocess
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from understanding_agent.security_lens import SecurityLens


def _stage(repo, path, content):
    os.makedirs(os.path.dirname(os.path.join(repo, path)), exist_ok=True)
    with open(os.path.join(repo, path), "w") as f:
        f.write(content)
    subprocess.run(["git", "add", path], cwd=repo, capture_output=True)


class TestDocExemption(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="ua-docex-")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, capture_output=True)

    def _findings(self):
        from understanding_agent.change_detector import ChangeDetector
        from understanding_agent.code_graph import CodeGraph
        from understanding_agent.context_builder import ContextBuilder
        os.chdir(self.repo)
        changes = ChangeDetector().detect()
        graph = CodeGraph()
        graph.build(changes.get("files") or [])
        context = ContextBuilder().build(changes, graph)
        return SecurityLens().find_critical_findings(context)

    def test_redacted_connection_string_in_readme_is_exempt(self):
        # The exact shape from the hook repo's own README that got blocked.
        doc = ("## Gate docs\n"
               "The gate catches `postgres://user:***@...` inline credentials\n"
               "and `postgres://user:***@db:5432/app` basic-auth URLs.\n")
        _stage(self.repo, "README.md", doc)
        findings = self._findings()
        labels = {f["label"] for f in findings}
        self.assertNotIn("creded-connection-string", labels, findings)
        self.assertNotIn("basic-auth-url", labels, findings)

    def test_real_shaped_connection_string_in_readme_still_blocks(self):
        conn = "postgres" + "://admin:S3cretPa55word@db.internal:5432/app"
        _stage(self.repo, "notes.md", f"config = \"{conn}\"\n")
        findings = self._findings()
        labels = {f["label"] for f in findings}
        self.assertIn("creded-connection-string", labels, findings)

    def test_vendor_token_in_readme_still_blocks(self):
        # Docs exemption must NOT weaken vendor formats: a real key is real anywhere.
        tok = "sk-ant-api03-" + "realtokenvaluehere1234567890abcdef"
        _stage(self.repo, "docs.md", f" leaked = \"{tok}\"\n")
        findings = self._findings()
        labels = {f["label"] for f in findings}
        self.assertIn("anthropic-api-token", labels, findings)

    def test_real_entropy_secret_in_readme_still_blocks(self):
        # Generic secrets keep full scanning in docs (only test paths exempt).
        val = "kx9vT2mQwLpZ8rN4yHj7uBcD3eF6gA1i"
        _stage(self.repo, "docs.md", f"password = \"{val}\"\n")
        findings = self._findings()
        labels = {f["label"] for f in findings}
        self.assertIn("high-entropy-secret", labels, findings)


if __name__ == "__main__":
    unittest.main()