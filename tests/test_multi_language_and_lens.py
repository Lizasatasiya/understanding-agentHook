import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from understanding_agent.stack_detector import StackDetector
from understanding_agent.language_support import (
    language_for, extract_functions, functions_touching_lines
)
from understanding_agent.security_lens import SecurityLens, security_question_hint
from understanding_agent.evidence_collector import collect_evidence, evidence_question_hint, _find_test_gaps
from understanding_agent.practice_packs import get_practice_topics, practice_pack_hint
from understanding_agent.change_detector import ChangeDetector
from understanding_agent.question_generator import QuestionGenerator


def _write(root: Path, rel: str, content: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


class TestStackDetector(unittest.TestCase):
    def _detect_in(self, files: dict) -> dict:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for rel, content in files.items():
                _write(root, rel, content)
            return StackDetector().detect(str(root))

    def test_nestjs_project(self):
        r = self._detect_in({
            "package.json": '{"dependencies": {"@nestjs/core": "^10", "typescript": "^5"}}',
            "tsconfig.json": "{}",
        })
        self.assertIn("typescript", r["languages"])
        self.assertIn("javascript", r["languages"])
        self.assertIn("nestjs", r["frameworks"])
        self.assertIn("web-backend", r["domains"])

    def test_react_project(self):
        r = self._detect_in({
            "package.json": '{"dependencies": {"react": "^18", "react-dom": "^18"}}',
        })
        self.assertIn("react", r["frameworks"])
        self.assertIn("web-frontend", r["domains"])

    def test_dotnet_csproj(self):
        r = self._detect_in({
            "src/Api/Api.csproj": (
                '<Project Sdk="Microsoft.NET.Sdk.Web">'
                '<ItemGroup><PackageReference Include="Microsoft.EntityFrameworkCore" Version="8.0.0"/></ItemGroup>'
                "</Project>"
            ),
            "app.sln": "Microsoft Visual Studio Solution File",
        })
        self.assertIn("csharp", r["languages"])
        self.assertIn("asp.net core", r["frameworks"])
        self.assertIn("entity-framework-core", r["frameworks"])
        self.assertTrue(r["is_dotnet"])
        self.assertIn("web-backend", r["domains"])

    def test_ml_project(self):
        r = self._detect_in({
            "requirements.txt": "torch\npandas\nscikit-learn\n",
            "train.py": "x = 1",
        })
        self.assertIn("python", r["languages"])
        self.assertIn("pytorch", r["frameworks"])
        self.assertIn("ml-data", r["domains"])

    def test_go_and_rust(self):
        r = self._detect_in({
            "go.mod": "module x\ngo 1.21",
            "Cargo.toml": "[package]",
        })
        self.assertIn("go", r["languages"])
        self.assertIn("rust", r["languages"])


class TestLanguageSupport(unittest.TestCase):
    def test_extension_map(self):
        self.assertEqual(language_for("app.tsx"), "typescript")
        self.assertEqual(language_for("src/Main.cs"), "csharp")
        self.assertEqual(language_for("x.py"), "python")
        self.assertEqual(language_for("x.unknown"), "")

    def test_ts_function_extraction(self):
        src = (
            "import { Injectable } from '@nestjs/common';\n"
            "\n"
            "@Injectable()\n"
            "export class UsersService {\n"
            "  async findOne(id: string): Promise<User> {\n"
            "    const user = await this.repo.findOneBy({ id });\n"
            "    return user;\n"
            "  }\n"
            "}\n"
        )
        fns = extract_functions(src, "typescript")
        names = [f["name"] for f in fns]
        self.assertIn("findOne", names)
        # touched-line targeting
        touched = functions_touching_lines(src, "typescript", {6})
        self.assertEqual([f["name"] for f in touched], ["findOne"])

    def test_csharp_function_extraction(self):
        src = (
            "namespace Api.Controllers;\n"
            "\n"
            "[ApiController]\n"
            "public class UsersController : ControllerBase {\n"
            "    [HttpGet(\"{id}\")]\n"
            "    public async Task<IActionResult> GetUser(string id) {\n"
            "        var user = await _service.GetUserAsync(id);\n"
            "        return Ok(user);\n"
            "    }\n"
            "}\n"
        )
        fns = extract_functions(src, "csharp")
        names = [f["name"] for f in fns]
        self.assertIn("GetUser", names)

    def test_js_arrow_and_declaration(self):
        src = (
            "const fetchData = async () => {\n"
            "  const res = await fetch('/api/x');\n"
            "  return res.json();\n"
            "};\n"
            "function helper(x) { return x + 1; }\n"
        )
        names = [f["name"] for f in extract_functions(src, "javascript")]
        self.assertIn("fetchData", names)
        self.assertIn("helper", names)

    def test_go_extraction(self):
        src = (
            "package main\n"
            "\n"
            "func handler(w http.ResponseWriter, r *http.Request) {\n"
            "  fmt.Fprintln(w, \"hi\")\n"
            "}\n"
            "func main() { handler(nil, nil) }\n"
        )
        names = [f["name"] for f in extract_functions(src, "go")]
        self.assertIn("handler", names)
        self.assertIn("main", names)

    def test_python_via_generic_path(self):
        # Python goes through ast in ChangeDetector, but generic extractor should still work
        src = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
        names = [f["name"] for f in extract_functions(src, "python")]
        self.assertEqual(names, ["foo", "bar"])

    def test_keywords_not_functions(self):
        src = "if (x) { return 1; }\nwhile (true) { break; }\n"
        self.assertEqual(extract_functions(src, "javascript"), [])


class TestSecurityLens(unittest.TestCase):
    def setUp(self):
        self.lens = SecurityLens()

    def _ctx(self, diff: str, file="src/api.py", func="handler"):
        return {"structured_changes": [{"file": file, "function": func, "diff": diff}]}

    def test_sql_injection_surface(self):
        ctx = self._ctx("+    query = f\"SELECT * FROM users WHERE id = {user_id}\"")
        p = self.lens.profile_change(ctx)
        self.assertTrue(p["is_security_relevant"])
        self.assertGreaterEqual(p["risk_score"], 20)
        self.assertEqual(p["top_surface"]["label"], "sql-injection-risk")
        hint = security_question_hint(p)
        self.assertTrue(hint["include_security_question"])
        self.assertIn("untrusted input", " ".join(hint["ask_about"]))

    def test_sql_injection_execute_parameterization(self):
        ctx = self._ctx("+    cursor.execute(f\"SELECT * FROM users WHERE id = {uid}\")")
        p = self.lens.profile_change(ctx)
        hint = security_question_hint(p)
        self.assertTrue(hint["include_security_question"])
        self.assertIn("parameterized", " ".join(hint["ask_about"]))

    def test_xss_react(self):
        ctx = self._ctx("+    return <div dangerouslySetInnerHTML={{ __html: userBio }} />", file="src/Bio.tsx")
        p = self.lens.profile_change(ctx)
        self.assertTrue(p["is_security_relevant"])
        self.assertEqual(p["top_surface"]["label"], "xss-risk")

    def test_hardcoded_secret(self):
        ctx = self._ctx("+    api_key = \"sk-live-9f8a7b6c5d4e3f2g1h0i\"")
        p = self.lens.profile_change(ctx)
        self.assertTrue(p["is_security_relevant"])
        labels = {s["label"] for s in p["surfaces"]}
        self.assertIn("hardcoded-secret", labels)

    def test_benign_change_not_flagged(self):
        ctx = self._ctx("+    total = sum(items)\n+    return total / len(items)")
        p = self.lens.profile_change(ctx)
        self.assertFalse(p["is_security_relevant"])
        hint = security_question_hint(p)
        self.assertFalse(hint["include_security_question"])

    def test_context_lines_ignored(self):
        # '-' removed lines and context lines must not trigger
        ctx = self._ctx("-    query = f\"SELECT * FROM users WHERE id = {user_id}\"")
        p = self.lens.profile_change(ctx)
        self.assertEqual(p["risk_score"], 0)

    def test_csharp_command_injection(self):
        ctx = self._ctx("+    var psi = new ProcessStartInfo(\"cmd.exe\", $\"/c {fileName}\");", file="src/Runner.cs", func="Run")
        p = self.lens.profile_change(ctx)
        self.assertTrue(p["is_security_relevant"])
        labels = {s["label"] for s in p["surfaces"]}
        self.assertIn("command-injection-risk", labels)

    def test_test_files_skipped(self):
        ctx = self._ctx("+    os.system(user_cmd)", file="tests/test_runner.py")
        p = self.lens.profile_change(ctx)
        self.assertEqual(p["risk_score"], 0)


class TestEvidenceCollector(unittest.TestCase):
    def test_no_tools_no_crash(self):
        with patch("shutil.which", return_value=None):
            ev = collect_evidence([{"path": "src/app.py"}])
        self.assertEqual(ev["semgrep"], [])
        self.assertEqual(ev["gitleaks"], [])

    def test_test_gap_detection(self):
        staged = [
            {"path": "src/checkout.py"},
            {"path": "tests/test_checkout.py"},
        ]
        gaps = _find_test_gaps(["src/checkout.py"], staged)
        self.assertEqual(gaps, [])  # test exists in staged set

        gaps2 = _find_test_gaps(["src/pricing.py"], staged)
        self.assertEqual(len(gaps2), 1)
        self.assertEqual(gaps2[0]["path"], "src/pricing.py")

    def test_test_files_never_flagged_as_gaps(self):
        staged = [{"path": "tests/test_x.py"}]
        gaps = _find_test_gaps(["tests/test_x.py"], staged)
        self.assertEqual(gaps, [])

    def test_evidence_hint(self):
        h = evidence_question_hint({
            "semgrep": [{"rule": "python.sql-injection", "path": "a.py", "line": 3, "message": "detected"}],
            "gitleaks": [],
            "test_coverage_gaps": [],
        })
        self.assertTrue(h["has_high_severity_evidence"])
        self.assertEqual(h["evidence_findings"][0]["source"], "semgrep")


class TestPracticePacks(unittest.TestCase):
    def test_nestjs_pack(self):
        stack = {"frameworks": ["nestjs", "typescript"], "languages": ["typescript"], "domains": ["web-backend"]}
        topics = get_practice_topics(stack, {})
        names = [t["topic"] for t in topics]
        self.assertIn("validation at the trust boundary (DTO/pipe)", names)

    def test_dotnet_pack(self):
        stack = {"frameworks": ["asp.net core", "entity-framework-core"], "languages": ["csharp"], "is_dotnet": True}
        topics = get_practice_topics(stack, {})
        names = [t["topic"] for t in topics]
        self.assertIn("async/await correctness and cancellation", names)
        self.assertIn("EF Core query behavior", names)

    def test_react_pack(self):
        stack = {"frameworks": ["react"], "languages": ["javascript"], "domains": ["web-frontend"]}
        topics = get_practice_topics(stack, {})
        self.assertGreater(len(topics), 2)

    def test_ml_pack(self):
        stack = {"frameworks": ["pytorch", "pandas"], "languages": ["python"], "domains": ["ml-data"]}
        topics = get_practice_topics(stack, {})
        names = [t["topic"] for t in topics]
        self.assertIn("train/test data separation", names)
        self.assertIn("reproducibility (seeds, versions, artifacts)", names)

    def test_security_topic_injected_first(self):
        stack = {"frameworks": [], "languages": ["python"], "domains": []}
        hint = practice_pack_hint(stack, {}, {
            "include_security_question": True,
            "focus_areas": ["sql-injection-risk"],
            "ask_about": ["whether the query is parameterized"],
            "top_file": "db.py",
        })
        self.assertTrue(hint["practice_topics"][0].startswith("security:"))

    def test_universal_topics_always(self):
        topics = get_practice_topics({"frameworks": [], "languages": [], "domains": []}, {})
        names = [t["topic"] for t in topics]
        self.assertIn("error handling at the boundary", names)
        self.assertIn("impact radius of the change", names)


class TestChangeDetectorMultiLanguage(unittest.TestCase):
    def _detect_with_git(self, name_status: str, numstat: str, show_map: dict, u0_map: dict):
        def handler(cmd, **kw):
            cmd_str = " ".join(cmd)
            if "--name-status" in cmd_str:
                return name_status
            if "--numstat" in cmd_str:
                return numstat
            for key, val in show_map.items():
                if f"git show {key}" in cmd_str.replace("git show :", "git show :").replace("git show ", "git show "):
                    pass
            # match "git show :path" and "git show HEAD:path"
            joined = " ".join(cmd)
            for key, val in show_map.items():
                if joined == f"git show {key}":
                    return val
            for key, val in u0_map.items():
                if joined == f"git diff --cached -U0 {key}" or (key in joined and "-U0" in joined):
                    return val
            return ""
        return handler

    def test_typescript_change_detected(self):
        handler = self._detect_with_git(
            "M\tsrc/users.service.ts\n",
            "12\t3\tsrc/users.service.ts\n",
            {
                ":src/users.service.ts": (
                    "import { Injectable } from '@nestjs/common';\n"
                    "@Injectable()\n"
                    "export class UsersService {\n"
                    "  async findOne(id: string) {\n"
                    "    return this.repo.findOneBy({ id });\n"
                    "  }\n"
                    "}\n"
                ),
                "HEAD:src/users.service.ts": (
                    "@Injectable()\n"
                    "export class UsersService {\n"
                    "}\n"
                ),
            },
            {"src/users.service.ts": "@@ -1,2 +1,6 @@\n+import...\n"},
        )
        with patch("subprocess.check_output", side_effect=handler):
            result = ChangeDetector().detect()
        self.assertEqual(len(result["files"]), 1)
        f = result["files"][0]
        self.assertEqual(f["language"], "typescript")
        self.assertEqual(f["path"], "src/users.service.ts")
        self.assertTrue(any(fn["name"] == "findOne" for fn in f["changed_functions"]))

    def test_csharp_change_detected(self):
        handler = self._detect_with_git(
            "M\tsrc/Controllers/UsersController.cs\n",
            "8\t1\tsrc/Controllers/UsersController.cs\n",
            {
                ":src/Controllers/UsersController.cs": (
                    "public class UsersController : ControllerBase {\n"
                    "    [HttpGet]\n"
                    "    public async Task<IActionResult> GetAll() {\n"
                    "        var users = await _service.GetAllAsync();\n"
                    "        return Ok(users);\n"
                    "    }\n"
                    "}\n"
                ),
                "HEAD:src/Controllers/UsersController.cs": (
                    "public class UsersController : ControllerBase {\n"
                    "}\n"
                ),
            },
            {"src/Controllers/UsersController.cs": "@@ -1,1 +1,6 @@\n+public class...\n"},
        )
        with patch("subprocess.check_output", side_effect=handler):
            result = ChangeDetector().detect()
        f = result["files"][0]
        self.assertEqual(f["language"], "csharp")
        self.assertTrue(any(fn["name"] == "GetAll" for fn in f["changed_functions"]))

    def test_non_code_files_skipped(self):
        handler = self._detect_with_git(
            "M\tREADME.md\nM\tsrc/app.py\nM\tpackage-lock.json\n",
            "5\t1\tREADME.md\n10\t2\tsrc/app.py\n500\t2\tpackage-lock.json\n",
            {
                ":src/app.py": "def main():\n    print('hi')\n",
                "HEAD:src/app.py": "def main():\n    pass\n",
            },
            {"src/app.py": "@@ -1,2 +1,2 @@\n+def main():\n+    print('hi')\n"},
        )
        with patch("subprocess.check_output", side_effect=handler):
            result = ChangeDetector().detect()
        paths = [f["path"] for f in result["files"]]
        # Lockfiles are always ignored
        self.assertNotIn("package-lock.json", paths)
        # Docs/config tracked as module-level changes (colleague's design, adopted)
        readme = next(f for f in result["files"] if f["path"] == "README.md")
        self.assertEqual(readme["status"], "modified")
        self.assertEqual(readme["changed_functions"][0]["name"], "<module>")
        # Code files still get function-level analysis
        self.assertIn("src/app.py", paths)


class TestQuestionGeneratorHints(unittest.TestCase):
    def test_micro_prompt_contains_hints(self):
        gen = QuestionGenerator()
        context = {
            "is_large_change": False,
            "structured_changes": [{"file": "db.py", "function": "query", "diff": "+ q = f'SELECT {x}'"}],
        }
        hints = {
            "stack": {"frameworks": ["fastapi"], "languages": ["python"], "domains": ["web-backend"]},
            "security": {
                "include_security_question": True,
                "focus_areas": ["sql-injection-risk"],
                "ask_about": ["whether the query is parameterized"],
                "top_file": "db.py",
            },
            "practices": {"prompt_hints": ["Ask about parameterized queries"]},
        }
        prompt = gen._build_prompt(context, {}, hints)
        self.assertIn("fastapi", prompt)
        self.assertIn("security question", prompt)
        self.assertIn("parameterized", prompt)
        self.assertIn("parameterized queries", prompt)

    def test_fallback_security_aware(self):
        gen = QuestionGenerator()
        hints = {
            "security": {
                "include_security_question": True,
                "focus_areas": ["sql-injection-risk", "input-boundary"],
                "ask_about": ["whether the query is parameterized"],
                "top_file": "db.py",
            },
            "evidence": {"evidence_findings": []},
        }
        qs = gen._fallback({}, hints)
        self.assertEqual(len(qs), 2)
        self.assertIn("sql-injection-risk", qs[0]["question"])
        self.assertTrue(qs[0]["is_fallback"])

    def test_fallback_with_evidence(self):
        gen = QuestionGenerator()
        hints = {
            "security": {
                "include_security_question": True,
                "focus_areas": ["xss-risk"],
                "ask_about": ["what sanitization happens"],
                "top_file": "ui.tsx",
            },
            "evidence": {"evidence_findings": [
                {"source": "semgrep", "detail": "xss at ui.tsx:10", "message": "dangerous HTML"}
            ]},
        }
        qs = gen._fallback({}, hints)
        self.assertIn("xss at ui.tsx:10", qs[1]["question"])

    def test_no_hints_backward_compatible(self):
        gen = QuestionGenerator()
        prompt = gen._build_prompt({"structured_changes": [], "is_large_change": False}, {}, None)
        self.assertIn("Changed Functions", prompt)
        qs = gen._fallback({})
        self.assertGreaterEqual(len(qs), 2)


if __name__ == "__main__":
    unittest.main()
