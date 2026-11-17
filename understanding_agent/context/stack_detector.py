"""Detect the project's stack: languages, frameworks, and domains from manifest files.

Reads package.json, pyproject.toml, requirements.txt, *.csproj/*.sln (and other
manifests) to determine what the project is built with. Everything downstream
(question generation, security weighting, practice packs) keys off this.
"""

import json
import os
import re
from pathlib import Path


# package.json dependency name -> framework label
_JS_FRAMEWORKS = {
    "react": "react",
    "react-dom": "react",
    "next": "next.js",
    "vue": "vue",
    "svelte": "svelte",
    "@angular/core": "angular",
    "express": "express",
    "@nestjs/core": "nestjs",
    "@nestjs/common": "nestjs",
    "fastify": "fastify",
    "electron": "electron",
    "jest": "jest",
    "typescript": "typescript",
}

# python dependency -> framework/library label
_PY_FRAMEWORKS = {
    "django": "django",
    "flask": "flask",
    "fastapi": "fastapi",
    "torch": "pytorch",
    "tensorflow": "tensorflow",
    "sklearn": "scikit-learn",
    "scikit-learn": "scikit-learn",
    "pandas": "pandas",
    "numpy": "numpy",
    "transformers": "huggingface",
    "langchain": "langchain",
    "openai": "openai",
    "mlflow": "mlflow",
    "airflow": "airflow",
    "pyspark": "pyspark",
    "sqlalchemy": "sqlalchemy",
    "celery": "celery",
    "pytest": "pytest",
    "boto3": "aws-sdk",
    "scrapy": "scrapy",
}

# .NET PackageReference / SDK -> framework label
_CSHARP_PACKAGES = {
    "Microsoft.AspNetCore": "asp.net core",
    "Microsoft.AspNetCore.App": "asp.net core",
    "Microsoft.EntityFrameworkCore": "entity-framework-core",
    "Microsoft.Extensions.DependencyInjection": "asp.net core",
    "NUnit": "nunit",
    "xunit": "xunit",
    "MSTest": "mstest",
    "Moq": "moq",
    "Dapper": "dapper",
    "Newtonsoft.Json": "json.net",
    "Serilog": "serilog",
    "NLog": "nlog",
    "MediatR": "mediatr",
    "FluentValidation": "fluentvalidation",
    "AutoMapper": "automapper",
    "Swashbuckle.AspNetCore": "swashbuckle",
    "Pomelo.EntityFrameworkCore.MySql": "entity-framework-core",
    "Npgsql.EntityFrameworkCore.PostgreSQL": "entity-framework-core",
    "Microsoft.Data.SqlClient": "sqlclient",
}

_DOMAIN_BY_FRAMEWORK = {
    "react": "web-frontend",
    "next.js": "web-frontend",
    "vue": "web-frontend",
    "svelte": "web-frontend",
    "angular": "web-frontend",
    "electron": "desktop",
    "express": "web-backend",
    "nestjs": "web-backend",
    "fastify": "web-backend",
    "django": "web-backend",
    "flask": "web-backend",
    "fastapi": "web-backend",
    "asp.net core": "web-backend",
    "pytorch": "ml-data",
    "tensorflow": "ml-data",
    "scikit-learn": "ml-data",
    "pandas": "ml-data",
    "numpy": "ml-data",
    "mlflow": "ml-data",
    "airflow": "data-pipeline",
    "pyspark": "data-pipeline",
    "transformers": "ml-data",
    "huggingface": "ml-data",
}

_ML_MARKER_LIBS = {
    "torch", "pytorch", "tensorflow", "sklearn", "scikit-learn", "pandas",
    "numpy", "transformers", "huggingface", "mlflow", "pyspark", "keras",
    "xgboost", "lightgbm", "jupyter", "langchain",
}

_SKIP_DIRS = {".git", "node_modules", "bin", "obj", "venv", ".venv", "__pycache__",
              ".tox", "dist", "build", ".next", "vendor"}


def _iter_files(root: Path, max_depth: int = 3):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        p = Path(dirpath)
        depth = len(p.relative_to(root).parts)
        if depth > max_depth:
            dirnames[:] = []
            continue
        for fname in filenames:
            yield p / fname


class StackDetector:
    """Detect languages, frameworks, and domains from project manifests."""

    def detect(self, repo_root: str | None = None) -> dict:
        root = Path(repo_root) if repo_root else Path.cwd()
        languages = set()
        frameworks = set()
        manifests = []

        for f in _iter_files(root):
            name = f.name.lower()
            try:
                if name == "package.json":
                    langs, fws = self._parse_package_json(f)
                    languages.update(langs)
                    frameworks.update(fws)
                    manifests.append(str(f.relative_to(root)))
                elif name in ("pyproject.toml", "pipfile", "setup.py", "setup.cfg"):
                    languages.add("python")
                    manifests.append(str(f.relative_to(root)))
                elif name.startswith("requirements") and name.endswith(".txt"):
                    languages.add("python")
                    frameworks.update(self._parse_requirements(f))
                    manifests.append(str(f.relative_to(root)))
                elif name == "tsconfig.json":
                    languages.add("typescript")
                    manifests.append(str(f.relative_to(root)))
                elif name.endswith((".csproj", ".fsproj", ".vbproj")):
                    languages.add("csharp" if name.endswith(".csproj") else "dotnet")
                    frameworks.update(self._parse_csproj(f))
                    manifests.append(str(f.relative_to(root)))
                elif name in ("global.json", "packages.config"):
                    languages.add("csharp")
                    manifests.append(str(f.relative_to(root)))
                elif name.endswith(".sln"):
                    languages.add("csharp")
                    manifests.append(str(f.relative_to(root)))
                elif name == "go.mod":
                    languages.add("go")
                    manifests.append(str(f.relative_to(root)))
                elif name == "cargo.toml":
                    languages.add("rust")
                    manifests.append(str(f.relative_to(root)))
                elif name in ("pom.xml",) or name.endswith(".gradle"):
                    languages.add("java")
                    manifests.append(str(f.relative_to(root)))
                elif name == "gemfile":
                    languages.add("ruby")
                    manifests.append(str(f.relative_to(root)))
                elif name == "composer.json":
                    languages.add("php")
                    manifests.append(str(f.relative_to(root)))
                elif name == "mix.exs":
                    languages.add("elixir")
                    manifests.append(str(f.relative_to(root)))
            except Exception:
                continue

        # TypeScript implies JavaScript tooling familiarity, keep them separate but list both
        if "typescript" in languages:
            languages.add("javascript")

        domains = set()
        for fw in frameworks:
            if fw in _DOMAIN_BY_FRAMEWORK:
                domains.add(_DOMAIN_BY_FRAMEWORK[fw])
        if not domains and "ml-data" not in domains:
            if frameworks & _ML_MARKER_LIBS:
                domains.add("ml-data")

        ordered = sorted(languages, key=lambda l: (l != "csharp", l))
        return {
            "languages": ordered,
            "primary_language": ordered[0] if ordered else "unknown",
            "frameworks": sorted(frameworks),
            "domains": sorted(domains),
            "manifests": manifests,
            "is_dotnet": ("csharp" in languages) or ("dotnet" in languages),
        }

    def _parse_package_json(self, path: Path):
        langs, fws = set(), set()
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return {"javascript"}, set()
        langs.add("javascript")
        deps = {}
        deps.update(data.get("dependencies", {}) or {})
        deps.update(data.get("devDependencies", {}) or {})
        for dep_name, fw in _JS_FRAMEWORKS.items():
            if dep_name in deps:
                fws.add(fw)
        return langs, fws

    def _parse_requirements(self, path: Path):
        fws = set()
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                pkg = re.split(r"[<>=!\[ ]", line)[0].strip().lower()
                if pkg in _PY_FRAMEWORKS:
                    fws.add(_PY_FRAMEWORKS[pkg])
        except Exception:
            pass
        return fws

    def _parse_csproj(self, path: Path):
        fws = set()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "Microsoft.NET.Sdk.Web" in text:
                fws.add("asp.net core")
            for pkg, fw in _CSHARP_PACKAGES.items():
                if pkg in text:
                    fws.add(fw)
        except Exception:
            pass
        return fws
