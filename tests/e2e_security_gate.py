"""E2E: the security gate must abort a real `git commit` containing a live credential.

Runs a REAL git commit through a REAL installed pre-commit hook in a scratch
repo. The fake secret is built by string concatenation (never a complete
literal) so the terminal security scanner cannot mangle this file — a
previous version of this fixture had its key literal replaced with a
placeholder marker, which made the vendor pattern unmatchable and the whole
e2e silently meaningless. Same for the hook invocation: it now uses the
CURRENT interpreter with an explicit PYTHONPATH (the system python3 may be
3.9 without the package installed).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(tempfile.mkdtemp(prefix="ua-gate-e2e-"))
HOOK_ROOT = Path(__file__).resolve().parent.parent

def run(cmd, **kw):
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, **kw)

run(["git", "init", "-q"])
run(["git", "config", "user.email", "t@t"])
run(["git", "config", "user.name", "t"])
run(["git", "config", "commit.gpgsign", "false"])

# Make sure the package is importable
probe = subprocess.run([sys.executable, "-c", "import understanding_agent.cli; print('ok')"],
                       capture_output=True, text=True)
assert probe.stdout.strip() == "ok", f"package not importable: {probe.stderr}"

# Install as a real pre-commit hook (classic hook style), with the CURRENT
# interpreter and an explicit PYTHONPATH to the hook repo
hook = f'#!/bin/sh\nexec env PYTHONPATH="{HOOK_ROOT}" {sys.executable} -m understanding_agent.cli\n'
(REPO / ".git" / "hooks" / "pre-commit").write_text(hook, encoding="utf-8")
os.chmod(REPO / ".git" / "hooks" / "pre-commit", 0o755)

# A real-shaped AWS access key id, built by concatenation (see module docstring)
aws_key = "AKIA" + "IOSFODNN7" + "EXAMPLE"

# 1. Benign commit MUST pass the gate. No interactive TTY in this harness →
#    the hook takes the record-only path (unattended-unverified) and exits 0.
(REPO / "app.py").write_text("def total(xs):\n    return sum(xs)\n", encoding="utf-8")
run(["git", "add", "-A"])
r1 = run(["git", "commit", "-m", "benign change"])
print("BENIGN commit exit:", r1.returncode)
out1 = r1.stdout + r1.stderr
assert "credential detected" not in out1, "gate fired on benign commit!"
assert r1.returncode == 0, f"benign commit failed unexpectedly: {out1[-300:]}"
assert "benign change" in run(["git", "log", "--oneline"]).stdout, "benign commit missing"

# 2. Commit with a hardcoded AWS key MUST be blocked
(REPO / "billing.py").write_text(
    'AWS_KEY = "%s"\n\ndef charge(c):\n    return AWS_KEY and c\n' % aws_key,
    encoding="utf-8")
run(["git", "add", "-A"])
r2 = run(["git", "commit", "-m", "add billing"])
out2 = r2.stdout + r2.stderr
print("SECRET commit exit:", r2.returncode)
print("--- hook output ---")
print(out2[:600])
assert r2.returncode != 0, "COMMIT WITH LIVE CREDENTIAL WAS ALLOWED!"
assert "credential detected" in out2, "block message missing"
assert aws_key not in out2, "FULL SECRET ECHOED IN OUTPUT!"
assert "rotate" in out2.lower(), "remediation guidance missing"
# The commit must not exist
log = run(["git", "log", "--oneline"])
assert "add billing" not in log.stdout, "blocked commit landed in history!"

# 3. Same commit with the gate disabled via env must proceed past the gate
#    (record-only path in this harness)
env = dict(os.environ)
env["UNDERSTANDING_AGENT_SECURITY_GATE"] = "off"
r3 = run(["git", "commit", "-m", "add billing"], env=env)
out3 = r3.stdout + r3.stderr
print("GATE-OFF commit exit:", r3.returncode)
assert "credential detected" not in out3, "gate fired despite disable flag"
assert r3.returncode == 0, f"gate-off commit failed unexpectedly: {out3[-300:]}"
assert "add billing" in run(["git", "log", "--oneline"]).stdout, "gate-off commit missing"

print()
print("ALL GATE E2E ASSERTIONS PASSED")
print("fixture:", REPO)
