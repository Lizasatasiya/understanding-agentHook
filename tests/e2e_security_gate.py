"""E2E: the security gate must abort a real `git commit` containing a live credential."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(tempfile.mkdtemp(prefix="ua-gate-e2e-"))

def run(cmd, **kw):
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, **kw)

run(["git", "init", "-q"])
run(["git", "config", "user.email", "t@t"])
run(["git", "config", "user.name", "t"])

# Make sure the pre-commit hook IS our entrypoint (installed via -e earlier; verify importable)
probe = subprocess.run([sys.executable, "-c", "import understanding_agent.cli; print('ok')"],
                       capture_output=True, text=True)
assert probe.stdout.strip() == "ok", f"package not importable: {probe.stderr}"

# Install as a real pre-commit hook (classic hook style)
(REPO / ".git" / "hooks" / "pre-commit").write_text(
    "#!/bin/sh\nexec python3 -m understanding_agent.cli\n", encoding="utf-8")
os.chmod(REPO / ".git" / "hooks" / "pre-commit", 0o755)

# 1. Benign commit MUST pass the gate (no GROQ key -> offline fallback questions,
#    evaluator fail-open score 75 -> commit allowed). Voice prompts skip (no TTY).
(REPO / "app.py").write_text("def total(xs):\n    return sum(xs)\n", encoding="utf-8")
run(["git", "add", "-A"])
r1 = run(["git", "commit", "-m", "benign change"])
print("BENIGN commit exit:", r1.returncode)
assert "credential" not in (r1.stdout + r1.stderr), "gate fired on benign commit!"

# 2. Commit with a hardcoded AWS key MUST be blocked
(REPO / "billing.py").write_text(
    'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n\ndef charge(c):\n    return AWS_KEY and c\n',
    encoding="utf-8")
run(["git", "add", "-A"])
r2 = run(["git", "commit", "-m", "add billing"])
out2 = r2.stdout + r2.stderr
print("SECRET commit exit:", r2.returncode)
print("--- hook output ---")
print(out2[:600])
assert r2.returncode != 0, "COMMIT WITH LIVE CREDENTIAL WAS ALLOWED!"
assert "credential detected" in out2, "block message missing"
assert "AKIAIOSFODNN7EXAMPLE" not in out2, "FULL SECRET ECHOED IN OUTPUT!"
assert "rotate" in out2.lower(), "remediation guidance missing"
# The commit must not exist
log = run(["git", "log", "--oneline"])
assert "add billing" not in log.stdout, "blocked commit landed in history!"

# 3. Same commit with the gate disabled via env must proceed past the gate
#    (offline fallback questions with fail-open scores)
env = dict(os.environ)
env["UNDERSTANDING_AGENT_SECURITY_GATE"] = "off"
r3 = run(["git", "commit", "-m", "add billing"], env=env)
out3 = r3.stdout + r3.stderr
print("GATE-OFF commit exit:", r3.returncode)
assert "credential detected" not in out3, "gate fired despite disable flag"

print()
print("ALL GATE E2E ASSERTIONS PASSED")
print("fixture:", REPO)
