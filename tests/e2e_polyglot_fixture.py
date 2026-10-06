#!/usr/bin/env python3
"""End-to-end validation of the multi-language pipeline on a real git repo fixture.

Creates a polyglot repo (NestJS TS + C# .NET + ML Python), stages realistic
changes, and runs the REAL pipeline (no mocks): ChangeDetector -> ContextBuilder
-> SecurityLens -> EvidenceCollector -> PracticePacks -> QuestionGenerator prompt.
Verifies each stage's output. Prints a report.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(tempfile.mkdtemp(prefix="ua-e2e-"))

# ---- 1. Build the fixture repo ----
def run(cmd, **kw):
    return subprocess.run(cmd, cwd=REPO, check=True, capture_output=True, text=True, **kw)

run(["git", "init", "-q"])
run(["git", "config", "user.email", "test@example.com"])
run(["git", "config", "user.name", "Test"])

# Manifests
(REPO / "package.json").write_text(json.dumps({
    "name": "polyglot-shop",
    "dependencies": {"@nestjs/core": "^10", "@nestjs/common": "^10", "typescript": "^5", "class-validator": "^0.5"},
}), encoding="utf-8")
(REPO / "tsconfig.json").write_text("{}", encoding="utf-8")

csproj_dir = REPO / "src" / "Api"
csproj_dir.mkdir(parents=True)
(csproj_dir / "Api.csproj").write_text(
    '<Project Sdk="Microsoft.NET.Sdk.Web">'
    '<ItemGroup><PackageReference Include="Microsoft.EntityFrameworkCore" Version="8.0.0"/>'
    '<PackageReference Include="AutoMapper" Version="12.0.0"/></ItemGroup>'
    "</Project>", encoding="utf-8")

# requirements.txt — ML stack
(REPO / "requirements.txt").write_text("pandas\nscikit-learn\n", encoding="utf-8")

# --- TypeScript: NestJS service (old version committed first) ---
ts_dir = REPO / "src" / "users"
ts_dir.mkdir(parents=True)
old_ts = """import { Injectable } from '@nestjs/common';

@Injectable()
export class UsersService {
  async findOne(id: string) {
    return this.repo.findOneBy({ id });
  }
}
"""
new_ts = """import { Injectable } from '@nestjs/common';

@Injectable()
export class UsersService {
  async findOne(id: string) {
    return this.repo.findOneBy({ id });
  }

  async findByName(name: string) {
    const query = `SELECT * FROM users WHERE name = '${name}'`;
    return this.rawQuery(query);
  }
}
"""
ts_path = ts_dir / "users.service.ts"
ts_path.write_text(old_ts, encoding="utf-8")

# --- C#: .NET controller ---
cs_controllers = REPO / "src" / "Api" / "Controllers"
cs_controllers.mkdir(parents=True)
old_cs = """public class UsersController : ControllerBase {
}
"""
new_cs = """using Microsoft.AspNetCore.Mvc;

public class UsersController : ControllerBase {
    [HttpGet("report")]
    public async Task<IActionResult> GetReport(string fileName) {
        var psi = new ProcessStartInfo("cmd.exe", $"/c {fileName}");
        var output = await Process.Start(psi)!;
        return Ok(output);
    }
}
"""
cs_path = cs_controllers / "UsersController.cs"
cs_path.write_text(old_cs, encoding="utf-8")

# --- Python: ML pipeline ---
ml_dir = REPO / "ml"
ml_dir.mkdir()
old_py = """import pandas as pd

def load_data(path):
    return pd.read_csv(path)
"""
new_py = """import pandas as pd

def load_data(path):
    df = pd.read_csv(path)
    return df

def evaluate(model, df):
    y_true = df["target"]
    preds = model.predict(df.drop(columns=["target"]))
    return (preds == y_true).mean()
"""
py_path = ml_dir / "pipeline.py"
py_path.write_text(old_py, encoding="utf-8")

# Commit the "old" state
run(["git", "add", "-A"])
run(["git", "commit", "-q", "-m", "initial"])

# ---- 2. Stage the changes ----
ts_path.write_text(new_ts, encoding="utf-8")
cs_path.write_text(new_cs, encoding="utf-8")
py_path.write_text(new_py, encoding="utf-8")
run(["git", "add", "-A"])

os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---- 3. Run the REAL pipeline ----
from understanding_agent.change_detector import ChangeDetector
from understanding_agent.stack_detector import StackDetector
from understanding_agent.code_graph import CodeGraph
from understanding_agent.context_builder import ContextBuilder
from understanding_agent.security_lens import SecurityLens, security_question_hint
from understanding_agent.evidence_collector import collect_evidence, evidence_question_hint
from understanding_agent.practice_packs import practice_pack_hint
from understanding_agent.question_generator import QuestionGenerator

print("=" * 70)
print("STAGE 1: ChangeDetector (real git)")
changes = ChangeDetector().detect()
for f in changes["files"]:
    print(f"  {f['language']:12} {f['path']}")
    for fn in f["changed_functions"]:
        print(f"      -> {fn['name']} ({fn['change_type']})")
print(f"  stats: {changes['stats']}")

langs = {f["language"] for f in changes["files"]}
assert "typescript" in langs, "TS change not detected"
assert "csharp" in langs, "C# change not detected"
assert "python" in langs, "Python change not detected"

ts_file = next(f for f in changes["files"] if f["language"] == "typescript")
assert any(fn["name"] == "findByName" for fn in ts_file["changed_functions"]), \
    f"findByName not detected: {ts_file['changed_functions']}"
cs_file = next(f for f in changes["files"] if f["language"] == "csharp")
assert any(fn["name"] == "GetReport" for fn in cs_file["changed_functions"]), \
    f"GetReport not detected: {cs_file['changed_functions']}"
py_file = next(f for f in changes["files"] if f["language"] == "python")
assert any(fn["name"] == "evaluate" for fn in py_file["changed_functions"]), \
    f"evaluate not detected: {py_file['changed_functions']}"
print("  PASS")

print("=" * 70)
print("STAGE 2: StackDetector")
stack = StackDetector().detect(str(REPO))
print(f"  languages:  {stack['languages']}")
print(f"  frameworks: {stack['frameworks']}")
print(f"  domains:    {stack['domains']}")
assert "nestjs" in stack["frameworks"]
assert "asp.net core" in stack["frameworks"]
assert "entity-framework-core" in stack["frameworks"]
assert "csharp" in stack["languages"]
assert "ml-data" in stack["domains"]
print("  PASS")

print("=" * 70)
print("STAGE 3: ContextBuilder (real diffs)")
graph = CodeGraph()
graph.build(changes["files"])
context = ContextBuilder().build(changes, graph)
print(f"  structured_changes: {len(context['structured_changes'])}")
for sc in context["structured_changes"]:
    print(f"  - {sc['file']} :: {sc['function']} ({len(sc['diff'].splitlines())} diff lines)")
assert len(context["structured_changes"]) >= 3
print("  PASS")

print("=" * 70)
print("STAGE 4: SecurityLens (TS SQL-injection + C# command-injection expected)")
profile = SecurityLens().profile_change(context)
print(f"  risk_score: {profile['risk_score']}")
for s in profile["surfaces"]:
    print(f"  - {s['file']}:{s['function']} [{s['label']}] sev={s['severity']}")
assert profile["is_security_relevant"], "security relevance not detected"
labels = {s["label"] for s in profile["surfaces"]}
assert "sql-injection-risk" in labels, f"SQL injection missed: {labels}"
assert "command-injection-risk" in labels, f"command injection missed: {labels}"
sec_hint = security_question_hint(profile)
print(f"  hint: {sec_hint['focus_areas']} -> ask about: {sec_hint['ask_about'][:3]}")
assert sec_hint["include_security_question"]
print("  PASS")

print("=" * 70)
print("STAGE 5: EvidenceCollector (no semgrep installed -> graceful)")
evidence = collect_evidence(changes["files"], str(REPO))
print(f"  semgrep: {len(evidence['semgrep'])} findings; gitleaks: {len(evidence['gitleaks'])}")
print(f"  test gaps: {evidence['test_coverage_gaps']}")
ev_hint = evidence_question_hint(evidence)
assert isinstance(ev_hint["evidence_findings"], list)
print("  PASS")

print("=" * 70)
print("STAGE 6: PracticePacks (NestJS + .NET + ML topics)")
hints = practice_pack_hint(stack, context, sec_hint)
print(f"  topics (top-8 shown to prompt): {hints['practice_topics']}")
all_topics = [t["topic"] for t in __import__('understanding_agent.practice_packs', fromlist=['get_practice_topics']).get_practice_topics(stack, context)]
assert any("DTO" in t or "validation" in t for t in all_topics), "NestJS pack missing"
assert any("async" in t or "EF Core" in t for t in all_topics), ".NET pack missing"
assert any("train/test" in t or "reproducib" in t for t in all_topics), "ML pack missing"
assert hints["practice_topics"][0].startswith("security:"), "security topic not first"
print("  PASS")

print("=" * 70)
print("STAGE 7: QuestionGenerator prompt (hints embedded, offline fallback)")
gen = QuestionGenerator()
full_hints = {"stack": stack, "security": sec_hint, "evidence": ev_hint, "practices": hints}
qs = gen.generate(context, {}, full_hints)  # no GROQ_API_KEY -> fallback path
for q in qs:
    print(f"  [{q['type']}] {q['question'][:100]}")
assert any("sql-injection" in q["question"] or "security" in q["question"].lower() for q in qs), \
    "fallback questions not security-aware"
prompt = gen._build_prompt(context, {}, full_hints)
assert "nestjs" in prompt.lower(), "stack hint missing from prompt"
assert "MANDATORY" in prompt or "security" in prompt.lower(), "security hint missing from prompt"
print("  PASS")

print("=" * 70)
print("ALL STAGES PASSED — polyglot fixture: NestJS/TS + .NET/C# + ML/Python")
print(f"fixture repo: {REPO}")
