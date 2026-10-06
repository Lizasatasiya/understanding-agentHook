import os
import subprocess
from pathlib import Path


# Language detection: maps file extension → language name
_EXTENSION_MAP = {
    ".py":   "python",
    ".js":   "javascript",
    ".jsx":  "javascript",
    ".mjs":  "javascript",
    ".cjs":  "javascript",
    ".ts":   "typescript",
    ".tsx":  "typescript",
    ".mts":  "typescript",
    ".cts":  "typescript",
    ".java": "java",
    ".kt":   "kotlin",
    ".go":   "go",
    ".rb":   "ruby",
    ".rs":   "rust",
    ".cs":   "csharp",
    ".cpp":  "cpp",
    ".c":    "c",
    ".swift":"swift",
    ".php":  "php",
    ".vue":  "vue",
    ".svelte":"svelte",
}


class EnvironmentDetector:
    def detect(self) -> dict:

        try:
            repo_root = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                text=True, stderr=subprocess.DEVNULL
            ).strip()
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                text=True, stderr=subprocess.DEVNULL
            ).strip()
        except subprocess.CalledProcessError:
            repo_root = str(Path.cwd())
            branch = "unknown"

        language = self._detect_language(repo_root)
        framework = self._detect_framework(repo_root, language)

        return {
            "language": language,
            "repository": os.path.basename(repo_root),
            "branch": branch,
            "project_root": repo_root,
            "framework": framework
        }

    def _detect_framework(self, root: str, language: str) -> str:
        # Check Node / JS / TS frameworks via package.json
        pkg_json_path = os.path.join(root, "package.json")
        if os.path.exists(pkg_json_path):
            try:
                import json
                with open(pkg_json_path, "r", encoding="utf-8") as f:
                    pkg = json.load(f)
                all_deps = {}
                all_deps.update(pkg.get("dependencies", {}))
                all_deps.update(pkg.get("devDependencies", {}))
                
                if "@nestjs/core" in all_deps or "@nestjs/common" in all_deps:
                    return "NestJS"
                if "next" in all_deps:
                    return "Next.js"
                if "react" in all_deps or "react-dom" in all_deps or "react-native" in all_deps:
                    return "React"
                if "vue" in all_deps:
                    return "Vue"
                if "@angular/core" in all_deps:
                    return "Angular"
                if "svelte" in all_deps:
                    return "Svelte"
                if "express" in all_deps:
                    return "Express"
                if "fastify" in all_deps:
                    return "Fastify"
                return "Node.js"
            except Exception:
                pass

        # Check Python frameworks
        if language == "python":
            req_files = ["requirements.txt", "pyproject.toml", "Pipfile", "setup.py"]
            for rf in req_files:
                rf_path = os.path.join(root, rf)
                if os.path.exists(rf_path):
                    try:
                        with open(rf_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read().lower()
                        if "fastapi" in content:
                            return "FastAPI"
                        if "django" in content:
                            return "Django"
                        if "flask" in content:
                            return "Flask"
                    except Exception:
                        pass

        # Check Go frameworks
        if language == "go":
            go_mod = os.path.join(root, "go.mod")
            if os.path.exists(go_mod):
                try:
                    with open(go_mod, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read().lower()
                    if "gin-gonic/gin" in content:
                        return "Gin"
                    if "gofiber/fiber" in content:
                        return "Fiber"
                except Exception:
                    pass

        return "unknown"

    def _detect_language(self, root: str) -> str:
        """
        Walk the project root (up to 2 levels deep) and count files per
        language. Return the dominant language, defaulting to 'unknown'.
        """
        counts: dict[str, int] = {}

        for dirpath, _, filenames in os.walk(root):
            # Skip venv, .git, __pycache__, node_modules
            dirpath_obj = Path(dirpath)
            if any(part in {".git", "venv", "__pycache__", "node_modules", ".tox"}
                   for part in dirpath_obj.parts):
                continue

            # Limit depth to 2 levels below root
            depth = len(dirpath_obj.relative_to(root).parts)
            if depth > 2:
                continue

            for fname in filenames:
                ext = Path(fname).suffix.lower()
                lang = _EXTENSION_MAP.get(ext)
                if lang:
                    counts[lang] = counts.get(lang, 0) + 1

        if not counts:
            return "unknown"

        return max(counts, key=lambda k: counts[k])
