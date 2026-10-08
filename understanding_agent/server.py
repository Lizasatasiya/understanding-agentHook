import os
import re
import sys
import json
import time
import subprocess
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from .coding_standards import CodingStandardsChecker
from .session_store import (
    load_sessions,
    save_session,
    get_sessions_for_commit,
    get_session_by_id,
    get_sessions_file
)


def get_default_repo_root() -> str:
    """Find the most relevant git repository root for serving commit data."""
    env_root = os.environ.get("UNDERSTANDING_AGENT_REPO_ROOT")
    if env_root and os.path.exists(env_root):
        return os.path.abspath(env_root)

    cwd = os.getcwd()
    # Check if cwd is a git repo
    if os.path.exists(os.path.join(cwd, ".git")):
        return cwd

    parent = os.path.dirname(cwd)
    if os.path.exists(os.path.join(parent, ".git")):
        return parent

    return cwd


def _run_git(args: List[str], repo_root: str) -> str:
    """Run a git command in the repository root and return stdout string."""
    try:
        return subprocess.check_output(
            ["git"] + args,
            text=True,
            stderr=subprocess.DEVNULL,
            cwd=repo_root
        ).strip()
    except Exception:
        return ""


def _parse_violations(standards_report: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert raw standards report items into rich, structured violation objects."""
    violations = []
    for item in standards_report:
        if item.get("is_good", True):
            continue
        standard_name = item.get("name", "Coding Standard")
        category = item.get("category", "General")
        rule_details = item.get("details", "")

        # Determine severity based on standard/category
        lower_name = standard_name.lower()
        lower_cat = category.lower()
        if any(w in lower_name or w in lower_cat for w in ["security", "xss", "secret", "injection"]):
            severity = "CRITICAL"
        elif any(w in lower_name or w in lower_cat for w in ["immutability", "hook", "timer", "cleanup", "dom", "exception"]):
            severity = "HIGH"
        else:
            severity = "MEDIUM"

        raw_violations = item.get("violations", [])
        if not raw_violations and rule_details:
            raw_violations = [rule_details]

        for v_str in raw_violations:
            # Expected format: "filepath:lineno: description (`actual_code`)"
            file_path = ""
            line_no = None
            description = v_str
            actual_code = ""

            m = re.match(r'^([^:]+):(\d+):\s*(.*)$', v_str)
            if m:
                file_path = m.group(1).strip()
                try:
                    line_no = int(m.group(2).strip())
                except ValueError:
                    line_no = None
                description = m.group(3).strip()

            # Extract actual code inside backticks if present
            code_m = re.search(r'`([^`]+)`', description)
            if code_m:
                actual_code = code_m.group(1).strip()

            violations.append({
                "standard": standard_name,
                "category": category,
                "severity": severity,
                "file": file_path,
                "line": line_no,
                "actual_code": actual_code,
                "description": description,
                "details": rule_details,
                "recommendation": f"Review {standard_name} guidelines to ensure clean, compliant patterns."
            })
    return violations


def _get_staged_changes(repo_root: str) -> Dict[str, Any]:
    """Retrieve actual staged files and stat counts."""
    numstat_out = _run_git(["diff", "--cached", "--numstat"], repo_root)
    status_out = _run_git(["diff", "--cached", "--name-status"], repo_root)

    status_map = {}
    for line in status_out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            s_char, fpath = parts
            status_map[fpath] = "added" if s_char == "A" else "deleted" if s_char == "D" else "modified"

    files = []
    total_added = 0
    total_deleted = 0
    for line in numstat_out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            add_str, del_str, fpath = parts
            adds = int(add_str) if add_str.isdigit() else 0
            dels = int(del_str) if del_str.isdigit() else 0
            total_added += adds
            total_deleted += dels
            files.append({
                "path": fpath,
                "status": status_map.get(fpath, "modified"),
                "additions": adds,
                "deletions": dels
            })
    return {
        "files": files,
        "total_added": total_added,
        "total_deleted": total_deleted
    }


def _get_commit_files(commit_hash: str, repo_root: str) -> List[Dict[str, Any]]:
    """Retrieve actual changed files for a historical commit."""
    numstat_out = _run_git(["show", "--numstat", "--pretty=", commit_hash], repo_root)
    status_out = _run_git(["show", "--name-status", "--pretty=", commit_hash], repo_root)

    status_map = {}
    for line in status_out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            s_char, fpath = parts
            status_map[fpath] = "added" if s_char == "A" else "deleted" if s_char == "D" else "modified"

    files = []
    for line in numstat_out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            add_str, del_str, fpath = parts
            adds = int(add_str) if add_str.isdigit() else 0
            dels = int(del_str) if del_str.isdigit() else 0
            files.append({
                "path": fpath,
                "status": status_map.get(fpath, "modified"),
                "additions": adds,
                "deletions": dels
            })
    return files


def _get_change_summary(commit_id: str, files: List[Dict[str, Any]], repo_root: str) -> Dict[str, str]:
    """Derive real change summary from git commit information."""
    if commit_id == "staged":
        # Attempt to run ChangeSummary if available
        try:
            from .change_detector import ChangeDetector
            from .code_graph import CodeGraph
            from .context_builder import ContextBuilder
            from .change_summary import ChangeSummary

            detector = ChangeDetector()
            changes = detector.detect()
            if changes.get("files"):
                graph = CodeGraph()
                graph.build(changes.get("files") or [])
                context = ContextBuilder().build(changes, graph)
                return ChangeSummary().generate(context)
        except Exception:
            pass
        return {
            "what_changed": f"Staged modifications across {len(files)} files.",
            "impact": "Code pending commit in current workspace.",
            "why_it_matters": "Pre-commit stage verifies coding standards and comprehension.",
            "key_risks": "Uncommitted work subject to pre-commit hook checks."
        }

    # Historical commit: build a meaningful summary from actual diff data
    raw_msg = _run_git(["log", "-1", "--pretty=format:%B", commit_id], repo_root)
    title = raw_msg.splitlines()[0].strip() if raw_msg else f"Commit {commit_id[:7]}"
    body = "\n".join(raw_msg.splitlines()[1:]).strip() if len(raw_msg.splitlines()) > 1 else ""

    # -- What Changed: derive from diff stat and changed symbols --
    total_added = sum(f.get("additions", 0) for f in files)
    total_deleted = sum(f.get("deletions", 0) for f in files)

    # Categorise changed files by type
    ext_groups: Dict[str, List[str]] = {}
    for f in files:
        ext = os.path.splitext(f["path"])[1].lstrip(".").lower() or "other"
        ext_groups.setdefault(ext, []).append(os.path.basename(f["path"]))

    # Collect names of changed functions/classes from the diff skeleton or added definitions
    diff_text = _run_git(["show", "--unified=0", commit_id], repo_root)
    changed_symbols: List[str] = []
    for line in diff_text.splitlines():
        m = re.search(r'@@.*@@ (.+)', line)
        if m:
            raw = m.group(1).strip()
            # Keep only the first token (function/class name) if it looks like an identifier
            token = re.split(r'[\s(:{]', raw)[0]
            if token and re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', token) and token not in changed_symbols:
                changed_symbols.append(token)
        elif line.startswith('+') and not line.startswith('+++'):
            clean_line = line[1:].strip()
            decl_m = re.match(
                r'^(?:export\s+)?(?:type|interface|class|def|function|enum|input|scalar|table)\s+([A-Za-z_][A-Za-z0-9_]*)',
                clean_line
            )
            if decl_m:
                sym = decl_m.group(1)
                if sym not in changed_symbols and len(changed_symbols) < 8:
                    changed_symbols.append(sym)

    file_names = ", ".join([os.path.basename(f["path"]) for f in files[:4]])
    if len(files) > 4:
        file_names += f" and {len(files) - 4} others"

    action_verb = "added" if all(f.get("status") == "added" for f in files) else "modified"
    if changed_symbols:
        symbols_str = ", ".join(f"`{s}`" for s in changed_symbols[:5])
        what = f"{len(files)} file(s) {action_verb} ({file_names}): defined {symbols_str}. +{total_added} / -{total_deleted} lines."
    else:
        what = f"{len(files)} file(s) {action_verb} ({file_names}). +{total_added} / -{total_deleted} lines."

    # -- Why It Matters: infer from conventional-commit prefix or body --
    prefix_match = re.match(r'^(feat|fix|refactor|perf|test|docs|chore|style|build|ci)\b[:(]?', title, re.IGNORECASE)
    if prefix_match:
        prefix = prefix_match.group(1).lower()
        _why_map = {
            "feat":     "Introduces new functionality that expands the feature set available to users or downstream systems.",
            "fix":      "Resolves a defect or incorrect behaviour, improving reliability and correctness.",
            "refactor": "Restructures existing code without changing external behaviour, improving maintainability.",
            "perf":     "Optimises performance, reducing latency or resource consumption.",
            "test":     "Adds or updates tests, increasing confidence in correctness and preventing regressions.",
            "docs":     "Updates documentation, improving developer understanding and onboarding.",
            "chore":    "Handles maintenance tasks (dependencies, tooling, configs) that keep the project healthy.",
            "style":    "Applies code-style or formatting changes with no functional impact.",
            "build":    "Modifies the build system or external dependencies.",
            "ci":       "Changes CI/CD pipeline configuration."
        }
        why = _why_map.get(prefix, body or "Implements required changes to the codebase.")
    elif body:
        # Use commit body if present
        why = body[:280] + ("…" if len(body) > 280 else "")
    else:
        why = "Implements required changes to the codebase."

    # -- Impact: file types and net line delta --
    type_summary = ", ".join(
        f"{len(v)} {k} file(s)" for k, v in sorted(ext_groups.items(), key=lambda x: -len(x[1]))
    ) or f"{len(files)} file(s)"
    impact = f"Modified {type_summary} (+{total_added} lines added, -{total_deleted} lines removed)."

    # -- Key Risks: based on what kind of files changed --
    all_paths_lower = " ".join(f["path"].lower() for f in files)
    risk_hints: List[str] = []
    if any(k in ("jsx", "tsx", "js", "ts") for k in ext_groups):
        risk_hints.append("Verify UI rendering and event handling in affected components")
    if any(k in ("graphql", "gql") for k in ext_groups):
        risk_hints.append("Verify GraphQL schema modifications maintain client compatibility and align with backend resolvers")
    if any(k in ("py",) for k in ext_groups) and any(kw in all_paths_lower for kw in ("test", "spec")):
        risk_hints.append("Confirm test coverage passes without regressions")
    elif "py" in ext_groups:
        risk_hints.append("Run the test suite to confirm no regressions in Python logic")
    if any(kw in all_paths_lower for kw in ("auth", "login", "token", "secret", "password", "crypt")):
        risk_hints.append("Review security implications of authentication/credential-related changes")
    if any(kw in all_paths_lower for kw in ("migration", "schema", "model", "db", "database")):
        risk_hints.append("Validate database schema migrations and data integrity")
    if any(kw in all_paths_lower for kw in ("config", "env", "settings", ".yaml", ".yml", ".toml")):
        risk_hints.append("Check environment-specific configuration values before deploying")
    if not risk_hints:
        risk_hints.append("Perform code review and run the full test suite before merging")

    key_risks = "; ".join(risk_hints) + "."

    return {
        "what_changed": what,
        "impact": impact,
        "why_it_matters": why,
        "key_risks": key_risks
    }


class SessionPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    repository: Optional[str] = "unknown"
    branch: Optional[str] = "unknown"
    commit_hash: Optional[str] = ""
    diff_hash: Optional[str] = ""
    attempt_number: Optional[int] = 1
    status: Optional[str] = "PASSED"
    score: Optional[float] = 0.0
    changed_files: Optional[List[Dict[str, Any]]] = []
    change_summary: Optional[Dict[str, Any]] = {}
    standards_report: Optional[List[Dict[str, Any]]] = []
    violations_count: Optional[int] = 0
    questions: Optional[List[Dict[str, Any]]] = []


def create_app(repo_root: Optional[str] = None) -> FastAPI:
    """Create and configure the FastAPI web application serving the UI and API."""
    effective_repo_root = os.path.abspath(repo_root or get_default_repo_root())
    app = FastAPI(title="Understanding Agent - Real Data Pre-Commit Dashboard")

    ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

    @app.middleware("http")
    async def add_no_cache_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static") or request.url.path in ("/ui", "/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # Serve static assets (CSS, JS)
    if os.path.isdir(ui_dir):
        app.mount("/static", StaticFiles(directory=ui_dir), name="static")

    # -------------------------------------------------------------------------
    # UI Web Routes
    # -------------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/ui")

    @app.get("/ui", response_class=HTMLResponse)
    def serve_ui():
        index_file = os.path.join(ui_dir, "index.html")
        if os.path.exists(index_file):
            return FileResponse(
                index_file,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                }
            )
        return HTMLResponse("<h2>UI index.html not found</h2>", status_code=404)

    # -------------------------------------------------------------------------
    # API Routes
    # -------------------------------------------------------------------------

    @app.get("/api/commits")
    @app.get("/commits")
    def list_commits(limit: int = 50, include_staged: bool = False):
        """List passed commits from git history with their pre-commit attempts count."""
        all_sessions = load_sessions(effective_repo_root)
        commits_list = []

        if include_staged:
            # Check for uncommitted staged changes only when explicitly requested
            staged_info = _get_staged_changes(effective_repo_root)
            if staged_info["files"]:
                staged_sessions = get_sessions_for_commit(effective_repo_root, "staged")
                latest_status = "PENDING"
                if staged_sessions:
                    latest_status = staged_sessions[-1].get("status", "FAILED")

                current_branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], effective_repo_root) or "main"
                commits_list.append({
                    "id": "staged",
                    "hash": "staged",
                    "short_hash": "staged",
                    "author": "Current Working Tree",
                    "author_email": "",
                    "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "message": "Pending Staged Changes (Uncommitted)",
                    "branch": current_branch,
                    "is_staged": True,
                    "status": latest_status,
                    "attempts_count": len(staged_sessions),
                    "files_count": len(staged_info["files"])
                })

        # Real git log commits (only passed committed ones)
        log_out = _run_git(
            ["log", f"-n{limit}", "--pretty=format:%H|%h|%an|%ae|%aI|%s"],
            effective_repo_root
        )

        for line in log_out.splitlines():
            parts = line.split("|", 5)
            if len(parts) == 6:
                full_h, short_h, author, email, date_str, subject = parts
                commit_sessions = get_sessions_for_commit(effective_repo_root, full_h)
                
                # Real commits in history are committed and passed
                status = "PASSED"

                commits_list.append({
                    "id": full_h,
                    "hash": full_h,
                    "short_hash": short_h,
                    "author": author,
                    "author_email": email,
                    "date": date_str,
                    "message": subject,
                    "branch": _run_git(["rev-parse", "--abbrev-ref", "HEAD"], effective_repo_root) or "main",
                    "is_staged": False,
                    "status": status,
                    "attempts_count": len(commit_sessions)
                })

        return commits_list

    @app.get("/api/commits/{commit_id}")
    @app.get("/commits/{commit_id}")
    def get_commit_details(commit_id: str):
        """Get full details for a commit, including changed files, real summary, and attempts."""
        if commit_id == "staged":
            staged = _get_staged_changes(effective_repo_root)
            files = staged["files"]
            if not files:
                raise HTTPException(status_code=404, detail="No staged changes currently present.")

            branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], effective_repo_root) or "main"
            author = _run_git(["config", "user.name"], effective_repo_root) or "Developer"
            email = _run_git(["config", "user.email"], effective_repo_root) or ""
            attempts = get_sessions_for_commit(effective_repo_root, "staged")
            summary = None
            for att in reversed(attempts):
                s = att.get("change_summary")
                if isinstance(s, dict) and s.get("what_changed") and s.get("what_changed") != "Staged changes awaiting pre-commit verification.":
                    summary = s
                    break
            if not summary:
                summary = _get_change_summary("staged", files, effective_repo_root)

            # Real coding standards audit on staged files
            checker = CodingStandardsChecker(effective_repo_root)
            standards_report = checker.check({"files": files})
            failed_standards = [s for s in standards_report if not s.get("is_good", True)]

            latest_status = "FAILED" if failed_standards else "PASSED"
            if attempts:
                latest_status = attempts[-1].get("status", latest_status)

            return {
                "commit_id": "staged",
                "hash": "staged",
                "short_hash": "staged",
                "message": "Pending Staged Changes (Uncommitted)",
                "author": f"{author} <{email}>" if email else author,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "branch": branch,
                "status": latest_status,
                "is_staged": True,
                "files": files,
                "summary": summary,
                "standards_report": standards_report,
                "violations_count": len(failed_standards),
                "attempts": [
                    {
                        "attempt_id": att.get("attempt_id") or att.get("session_id"),
                        "attempt_number": att.get("attempt_number", idx + 1),
                        "status": att.get("status", "FAILED"),
                        "score": att.get("score", 0),
                        "violations_count": att.get("violations_count", len(failed_standards)),
                        "timestamp": att.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S")),
                        "questions_count": len(att.get("questions", []))
                    }
                    for idx, att in enumerate(attempts)
                ]
            }

        # Historical commit
        commit_meta = _run_git(["log", "-1", "--pretty=format:%H|%h|%an|%ae|%aI|%B", commit_id], effective_repo_root)
        if not commit_meta:
            raise HTTPException(status_code=404, detail=f"Commit '{commit_id}' not found.")

        meta_parts = commit_meta.split("|", 5)
        full_h, short_h, author, email, date_str = meta_parts[:5]
        full_message = meta_parts[5] if len(meta_parts) > 5 else _run_git(["log", "-1", "--pretty=format:%B", commit_id], effective_repo_root)

        files = _get_commit_files(commit_id, effective_repo_root)
        branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], effective_repo_root) or "main"

        # Check coding standards for this commit diff
        checker = CodingStandardsChecker(effective_repo_root)
        standards_report = checker.check({"files": files}, commit_hash=commit_id)
        failed_standards = [s for s in standards_report if not s.get("is_good", True)]

        attempts = get_sessions_for_commit(effective_repo_root, commit_id)

        # 1. Prefer AI-generated summary from recorded pre-commit defense session
        summary = None
        for att in reversed(attempts):
            s = att.get("change_summary")
            if isinstance(s, dict) and s.get("what_changed") and s.get("what_changed") != "Staged changes awaiting pre-commit verification.":
                summary = s
                break
        # 2. Fall back to diff-derived change summary
        if not summary:
            summary = _get_change_summary(commit_id, files, effective_repo_root)

        status = "PASSED"

        return {
            "commit_id": full_h,
            "hash": full_h,
            "short_hash": short_h,
            "message": full_message,
            "author": f"{author} <{email}>" if email else author,
            "timestamp": date_str,
            "branch": branch,
            "status": status,
            "is_staged": False,
            "files": files,
            "summary": summary,
            "standards_report": standards_report,
            "violations_count": len(failed_standards),
            "attempts": [
                {
                    "attempt_id": att.get("attempt_id") or att.get("session_id"),
                    "attempt_number": att.get("attempt_number", idx + 1),
                    "status": att.get("status", "FAILED"),
                    "score": att.get("score", 0),
                    "violations_count": att.get("violations_count", 0),
                    "timestamp": att.get("timestamp", date_str),
                    "questions_count": len(att.get("questions", []))
                }
                for idx, att in enumerate(attempts)
            ]
        }

    @app.get("/api/commits/{commit_id}/attempts")
    @app.get("/commits/{commit_id}/attempts")
    def get_commit_attempts(commit_id: str):
        """Retrieve attempts associated with a commit."""
        sessions = get_sessions_for_commit(effective_repo_root, commit_id)
        return [
            {
                "attempt_id": att.get("attempt_id") or att.get("session_id"),
                "attempt_number": att.get("attempt_number", idx + 1),
                "status": att.get("status", "FAILED"),
                "score": att.get("score", 0),
                "violations_count": att.get("violations_count", 0),
                "timestamp": att.get("timestamp", ""),
                "questions_count": len(att.get("questions", []))
            }
            for idx, att in enumerate(sessions)
        ]

    @app.get("/api/attempts/{attempt_id}")
    @app.get("/attempts/{attempt_id}")
    def get_attempt_details(attempt_id: str):
        """Retrieve full details of an attempt (questions, answers, evaluations, violations)."""
        session = get_session_by_id(effective_repo_root, attempt_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Attempt '{attempt_id}' not found.")

        commit_id = session.get("commit_id") or "staged"
        commit_attempts = get_sessions_for_commit(effective_repo_root, commit_id)
        
        # Calculate navigation (previous / next)
        prev_id = None
        next_id = None
        current_idx = -1
        for idx, att in enumerate(commit_attempts):
            sid = att.get("attempt_id") or att.get("session_id")
            if sid == attempt_id or sid == session.get("session_id"):
                current_idx = idx
                break

        if current_idx > 0:
            prev_id = commit_attempts[current_idx - 1].get("attempt_id") or commit_attempts[current_idx - 1].get("session_id")
        if current_idx >= 0 and current_idx < len(commit_attempts) - 1:
            next_id = commit_attempts[current_idx + 1].get("attempt_id") or commit_attempts[current_idx + 1].get("session_id")

        standards_report = session.get("standards_report", [])
        # If standards_report wasn't attached, evaluate now
        if not standards_report:
            try:
                files = session.get("changed_files", [])
                checker = CodingStandardsChecker(effective_repo_root)
                standards_report = checker.check({"files": files})
            except Exception:
                standards_report = []

        violations = _parse_violations(standards_report)

        # Track origin attempt for each question (e.g. if answered in attempt n, carry origin over)
        raw_questions = session.get("questions", [])
        attempt_number = session.get("attempt_number", current_idx + 1 if current_idx >= 0 else 1)
        enriched_questions = []

        for q in raw_questions:
            q_copy = dict(q)
            origin_att = q_copy.get("answered_in_attempt")
            ans_text = (q_copy.get("answer") or "").strip()

            if not origin_att and ans_text:
                q_text = (q_copy.get("question") or "").strip().lower()
                q_id = q_copy.get("question_id")
                # Search previous commit attempts chronologically up to current_idx
                for prev_att in commit_attempts[:current_idx + 1]:
                    prev_num = prev_att.get("attempt_number")
                    for pq in prev_att.get("questions", []):
                        pq_text = (pq.get("question") or "").strip().lower()
                        pq_id = pq.get("question_id")
                        if (q_id and q_id == pq_id) or (q_text and q_text == pq_text):
                            pq_ans = (pq.get("answer") or "").strip()
                            pq_score = (pq.get("evaluation") or {}).get("score", pq.get("score", 0)) if isinstance(pq.get("evaluation"), dict) else pq.get("score", 0)
                            if pq_ans and (pq_score >= 70 or pq.get("status") == "answered"):
                                origin_att = pq.get("answered_in_attempt") or prev_num
                                break
                    if origin_att:
                        break
                if not origin_att and (q_copy.get("status") == "answered" or ((q_copy.get("evaluation") or {}).get("score", 0) >= 70 if isinstance(q_copy.get("evaluation"), dict) else q_copy.get("score", 0) >= 70)):
                    origin_att = attempt_number

            if origin_att:
                q_copy["answered_in_attempt"] = origin_att
            enriched_questions.append(q_copy)

        return {
            "attempt_id": session.get("attempt_id") or session.get("session_id"),
            "attempt_number": attempt_number,
            "commit_id": commit_id,
            "timestamp": session.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S")),
            "status": session.get("status", "FAILED"),
            "score": session.get("score", 0),
            "violations_count": len(violations),
            "violations": violations,
            "standards_report": standards_report,
            "questions": enriched_questions,
            "change_summary": session.get("change_summary", {}),
            "changed_files": session.get("changed_files", []),
            "navigation": {
                "previous_attempt_id": prev_id,
                "next_attempt_id": next_id,
                "current_index": current_idx + 1 if current_idx >= 0 else 1,
                "total_attempts": len(commit_attempts) if commit_attempts else 1
            }
        }

    @app.post("/understanding-session")
    def receive_session(payload: Dict[str, Any]):
        """Receive session audit payloads from pre-commit hook and persist them."""
        # Ensure session_id and timestamp
        if not payload.get("session_id"):
            att_no = payload.get("attempt_number", 1)
            payload["session_id"] = f"sess_{int(time.time()*1000)}_{att_no}"
        if not payload.get("timestamp"):
            payload["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        saved = save_session(effective_repo_root, payload)
        print(f"\n[Dashboard API] Session received: {saved.get('session_id')} ({saved.get('status')})", flush=True)
        return {"status": "success", "session_id": saved.get("session_id")}

    return app


def run():
    """CLI runner for understanding-agent-ui."""
    import uvicorn
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    run()
