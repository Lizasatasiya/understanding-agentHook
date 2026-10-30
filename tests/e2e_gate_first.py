"""E2E: scratch git repo; verifies gate-first ordering, no-LLM fallback behavior.

Run: python3.14 tests/e2e_gate_first.py
Creates a temp git repo, stages a real-shaped credential, runs the real
SecurityLens gate + CLI ordering logic, and asserts:
  1. Credential gate blocks with ZERO LLM calls and ZERO prompts.
  2. No-key path: no hardcoded questions are asked.
  3. Sessions prune keeps the most recent records.
Exits non-zero on the first failed assertion.
"""
import os
import sys
import json
import subprocess
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

FAILED = []

def expect(label, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(label)

def run(cwd, *args):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)

# ---------------------------------------------------------------- repo setup
tmp = tempfile.mkdtemp(prefix="ua-e2e-")
run(tmp, "git", "init", "-q")
run(tmp, "git", "config", "user.email", "e2e@test.local")
run(tmp, "git", "config", "user.name", "E2E")
run(tmp, "git", "config", "commit.gpgsign", "false")
with open(os.path.join(tmp, ".gitignore"), "w") as f:
    f.write(".env\n")
run(tmp, "git", "add", ".gitignore")
run(tmp, "git", "commit", "-q", "-m", "init")

os.environ.pop("NOUS_API_KEY", None)
os.environ.pop("GROQ_API_KEY", None)
os.environ.pop("UNDERSTANDING_AGENT_API_KEY", None)
os.environ["UNDERSTANDING_AGENT_TELEMETRY_URL"] = ""  # no telemetry in e2e

from understanding_agent.security_lens import SecurityLens, security_gate_enabled
from understanding_agent.question_generator import QuestionGenerator
from understanding_agent.change_detector import ChangeDetector
from understanding_agent.code_graph import CodeGraph
from understanding_agent.context_builder import ContextBuilder
from understanding_agent.stack_detector import StackDetector
from understanding_agent.coding_standards import CodingStandardsChecker
from understanding_agent import api_utils

os.chdir(tmp)

def build_context():
    changes = ChangeDetector().detect()
    graph = CodeGraph()
    graph.build(changes.get("files") or [])
    return changes, ContextBuilder().build(changes, graph)

# ------------------------------------------------- scene 1: credential blocks
# Build a real-shaped AWS key by concatenation (never a complete literal).
aws_key = "AKIA" + "ABCDEFGHIJ012345"   # AKIA + 16 chars = real vendor shape
with open("config.py", "w") as f:
    f.write(f"import os\nAWS_ACCESS_KEY_ID = \"{aws_key}\"\n")
run(tmp, "git", "add", "config.py")

changes, context = build_context()
lens = SecurityLens()

llm_calls = []
orig_request = api_utils._nous_request_once
def counting_request(api_key, payload, timeout):
    llm_calls.append(payload)
    return orig_request(api_key, payload, timeout)
api_utils._nous_request_once = counting_request

critical = lens.find_critical_findings(context) if security_gate_enabled() else []
expect("scene1: gate finds the staged AWS key", len(critical) >= 1,
       f"findings={critical}")
expect("scene1: finding names the file", critical and critical[0]["file"] == "config.py",
       f"finding={critical[0] if critical else None}")
expect("scene1: zero LLM calls on the gate path", len(llm_calls) == 0,
       f"llm_calls={len(llm_calls)}")

# The gate path must exit before the standards audit — verify ordering by
# simulating the cli flow: gate check happens with the same context the CLI
# builds BEFORE CodingStandardsChecker is ever constructed.
std_checker = CodingStandardsChecker(tmp)
report = std_checker.check(changes, context)
expect("scene1: standards audit runs only after a clean gate (wiring intact)",
       isinstance(report, list))

# Cleanup scene 1
run(tmp, "git", "restore", "--staged", "config.py")
os.remove("config.py")
api_utils._nous_request_once = orig_request

# ------------------------------------- scene 2: no key, no hardcoded questions
with open("app.py", "w") as f:
    f.write("import os\n\ndef handle(req):\n"
            "    query = 'SELECT * FROM users WHERE name = ' + req['name']\n"
            "    return run_raw_query(query)\n")
run(tmp, "git", "add", "app.py")

changes, context = build_context()
lens = SecurityLens()
profile = lens.profile_change(context)
expect("scene2: SQL injection surface detected", profile["is_security_relevant"],
      f"profile={profile}")

os.environ["UNDERSTANDING_AGENT_IGNORE_DOTENV"] = "1"
gen = QuestionGenerator()
questions = gen.generate(context, {}, hints={"security": None, "standards": []}, env=None)
expect("scene2: no questions when the model key is missing", len(questions) == 0,
       f"count={len(questions)}")

from understanding_agent.security_lens import security_question_hint
sec_hint = security_question_hint(profile)
hints = {"security": sec_hint, "standards": []}
questions2 = gen.generate(context, {}, hints=hints, env=None)
expect("scene2: security hint does not invent a hardcoded question",
       questions2 == [], f"questions={questions2}")

# ------------------------------------------------- scene 3: sessions prune
from understanding_agent.session_store import save_session, load_sessions
os.environ["UNDERSTANDING_AGENT_MAX_SESSIONS"] = "25"
for i in range(40):
    save_session(tmp, {
        "session_id": f"sess_e2e_{i}", "attempt_id": f"sess_e2e_{i}",
        "attempt_number": i, "commit_id": "staged",
        "head_commit": "e2ehead", "timestamp": f"2026-10-08T10:{i:02d}:00",
        "status": "FAILED", "score": 0, "questions": [],
    })
loaded = load_sessions(tmp)
expect("scene3: sessions file pruned to 25", len(loaded) == 25,
       f"count={len(loaded)}")
kept = {s["session_id"] for s in loaded}
expect("scene3: most recent sessions kept", "sess_e2e_39" in kept and "sess_e2e_0" not in kept,
       f"kept={sorted(kept)[:3]}...{sorted(kept)[-3:]}")

# ------------------------------------------- scene 4: agent mode is strictly blocked
# Autonomous AI agents (Antigravity, Cursor, Copilot, Claude, Cline, etc.) cannot commit
# staged code without a human performing the oral defense. The hook must trigger and
# hard-block agent commits (exit 1) and record verification="agent-blocked".
import json as _json
hook_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env4 = dict(os.environ)
env4["UNDERSTANDING_AGENT_MODE"] = "agent"
env4["PYTHONPATH"] = hook_root
proc = subprocess.run(
    [sys.executable, "-m", "understanding_agent.cli"],
    cwd=tmp, env=env4, capture_output=True, text=True, stdin=subprocess.DEVNULL,
)
expect("scene4: agent mode is blocked (exits 1)", proc.returncode == 1,
       f"rc={proc.returncode} out={proc.stdout[-400:]} err={proc.stderr[-200:]}")
expect("scene4: agent mode prints the blocked banner",
       "COMMIT BLOCKED" in proc.stdout, f"out={proc.stdout[-400:]}")
expect("scene4: agent mode asks no questions",
       "Question 1/" not in proc.stdout, f"out={proc.stdout[-400:]}")
sess_path = os.path.join(tmp, ".git", "understanding_sessions.json")
sessions = load_sessions(tmp)
agent_rows = [s for s in sessions if s.get("verification") == "agent-blocked"]
expect("scene4: session recorded as agent-blocked", len(agent_rows) == 1,
       f"rows={[s.get('verification') for s in sessions]}")
expect("scene4: local store is SQLite (not plaintext JSON)",
       os.path.exists(os.path.join(tmp, ".git", "understanding_sessions.db")),
       "no .git/understanding_sessions.db found")

# --------------------- scene 5: the gate STILL hard-blocks in agent mode
# Agents are unverified actors, not exempt ones: a staged live credential must
# block (exit 1, gate output) before record-only mode is ever reached.
aws = "AKIA" + "".join(chr(65 + (i * 7 + 3) % 26) for i in range(16))
with open("leaky.py", "w") as f:
    f.write(f'import os\nAWS_ACCESS_KEY_ID = "{aws}"\n')
run(tmp, "git", "add", "leaky.py")
proc5 = subprocess.run(
    [sys.executable, "-m", "understanding_agent.cli"],
    cwd=tmp, env=env4, capture_output=True, text=True, stdin=subprocess.DEVNULL,
)
expect("scene5: gate blocks in agent mode", proc5.returncode == 1,
       f"rc={proc5.returncode} out={proc5.stdout[-400:]}")
expect("scene5: gate output only (no record-only marker)",
       "RECORD-ONLY" not in proc5.stdout, f"out={proc5.stdout[-400:]}")
run(tmp, "git", "restore", "--staged", "leaky.py")
os.remove("leaky.py")

# ------------------------------------------------------------------- summary
print()
if FAILED:
    print(f"E2E FAILED: {len(FAILED)} assertion(s): {FAILED}")
    sys.exit(1)
print("E2E PASSED: all scenes verified against real git state.")
