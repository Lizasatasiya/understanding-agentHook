import os
import subprocess
from pathlib import Path


# Language detection: maps file extension → language name
_EXTENSION_MAP = {
    ".py":   "python",
    ".js":   "javascript",
    ".ts":   "typescript",
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

        return {
            "language": language,
            "repository": os.path.basename(repo_root),
            "branch": branch,
            "project_root": repo_root,
            "framework": "unknown"
        }

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
