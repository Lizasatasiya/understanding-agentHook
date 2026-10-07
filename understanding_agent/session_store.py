import os
import json
import time
import subprocess
from typing import List, Dict, Any, Optional
from .commit_context import (
    get_head_commit,
    get_current_commit_message,
    compute_attempt_key
)


def get_sessions_file(repo_root: str) -> str:
    """Return the path to the sessions file, preferring .git directory if present."""
    git_dir = os.path.join(repo_root, ".git")
    if os.path.isdir(git_dir):
        return os.path.join(git_dir, "understanding_sessions.json")
    return os.path.join(repo_root, "understanding_sessions.json")


def reconcile_sessions(repo_root: str, sessions: List[Dict[str, Any]]) -> bool:
    """
    Reconcile sessions against git log history.
    If a session was recorded as 'staged', but its base head or diff was committed,
    re-associate it with that commit hash and set is_staged=False.
    Returns True if any session was modified.
    """
    try:
        out = subprocess.check_output(
            ["git", "log", "-n", "30", "--pretty=format:%H|%P|%s|%aI"],
            text=True, cwd=repo_root, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return False

    if not out:
        return False

    commits = []
    for line in out.splitlines():
        parts = line.split("|", 3)
        if len(parts) >= 3:
            commits.append({
                "hash": parts[0],
                "parents": parts[1].split(),
                "subject": parts[2],
                "date": parts[3] if len(parts) > 3 else ""
            })

    if not commits:
        return False

    head_commit = commits[0]["hash"]
    modified = False

    for s in sessions:
        if s.get("commit_id") != "staged":
            continue

        s_head = s.get("head_commit")
        s_ts = s.get("timestamp", "")

        # Case 1: Session has head_commit, and HEAD has moved past it
        if s_head and s_head != head_commit:
            # Find the commit whose parent is s_head
            found_child = None
            for c in commits:
                if s_head in c["parents"]:
                    found_child = c["hash"]
                    break
            if found_child:
                s["commit_id"] = found_child
                s["is_staged"] = False
                modified = True
            else:
                # Associated commit not in immediate parents, link to HEAD or mark archived
                s["commit_id"] = head_commit
                s["is_staged"] = False
                modified = True

        # Case 2: Older session without head_commit
        elif not s_head and s_ts:
            # Check if timestamp is earlier than recent commits
            for c in reversed(commits):
                c_date = c.get("date", "")
                if c_date and s_ts <= c_date[:19]:
                    s["commit_id"] = c["hash"]
                    s["is_staged"] = False
                    modified = True
                    break

    return modified


def load_sessions(repo_root: str) -> List[Dict[str, Any]]:
    """Load all persisted sessions for the given repository and reconcile completed commits."""
    path = get_sessions_file(repo_root)
    sessions = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    sessions = data
                elif isinstance(data, dict):
                    sessions = [data]
        except Exception:
            sessions = []

    # Also check if secondary file exists in root (if path was inside .git)
    fallback_path = os.path.join(repo_root, "understanding_sessions.json")
    if os.path.exists(fallback_path) and fallback_path != path:
        try:
            with open(fallback_path, "r", encoding="utf-8") as f:
                extra = json.load(f)
                if isinstance(extra, list):
                    existing_ids = {s.get("session_id") for s in sessions if s.get("session_id")}
                    for item in extra:
                        if item.get("session_id") not in existing_ids:
                            sessions.append(item)
        except Exception:
            pass

    # Reconcile completed commits so old staged sessions do not linger as active staged attempts
    if reconcile_sessions(repo_root, sessions):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(sessions, f, indent=2)
        except Exception:
            pass

    return sessions


def _seed_from_state(repo_root: str, state_data: dict, attempt_number: Optional[int] = None) -> Optional[dict]:
    """Build an initial real session from an existing understanding_agent_state.json."""
    diff_hash = state_data.get("diff_hash", "")
    attempts = attempt_number or state_data.get("attempts", 1)
    raw_questions = state_data.get("questions", [])
    if not raw_questions:
        return None

    head_commit = state_data.get("head_commit") or get_head_commit(repo_root)
    commit_msg = state_data.get("commit_message") or get_current_commit_message(repo_root)
    attempt_key = state_data.get("attempt_key") or compute_attempt_key(head_commit, diff_hash, commit_msg)

    # Get branch
    branch = "main"
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            text=True, cwd=repo_root, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        pass

    # Get staged files
    staged_files = []
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-status"],
            text=True, cwd=repo_root, stderr=subprocess.DEVNULL
        ).strip()
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                status_char, fpath = parts
                status = "added" if status_char == "A" else "deleted" if status_char == "D" else "modified"
                staged_files.append({"path": fpath, "status": status})
    except Exception:
        pass

    # Run real standards checker
    standards_report = []
    try:
        from .coding_standards import CodingStandardsChecker
        checker = CodingStandardsChecker(repo_root)
        standards_report = checker.check({"files": staged_files})
    except Exception:
        pass

    failed_standards = [s for s in standards_report if not s.get("is_good", True)]

    # Format questions
    formatted_questions = []
    for q in raw_questions:
        q_id = q.get("question_id", "q1")
        ans = q.get("answer", "")
        q_status = "answered" if q.get("answered") or ans else "pending"
        score = q.get("best_score", 0)
        feedback = "Awaiting developer defense in pre-commit hook." if not q.get("answered") and not ans else "Answer evaluated."
        resp_time = q.get("response_time_seconds")

        formatted_questions.append({
            "question_id": q_id,
            "question": q.get("question", ""),
            "type": q.get("type", "General"),
            "expected_concepts": q.get("expected_concepts", []),
            "evaluation_criteria": q.get("evaluation_criteria", []),
            "time_limit_seconds": q.get("time_limit", 60),
            "answer": ans,
            "response_time_seconds": resp_time,
            "status": q_status,
            "evaluation": {
                "score": score,
                "passed": q.get("passed", False),
                "qualitative_feedback": feedback
            }
        })

    session_id = f"sess_staged_{diff_hash[:8] if diff_hash else '1'}_{attempts}"
    return {
        "session_id": session_id,
        "attempt_id": session_id,
        "attempt_number": attempts,
        "commit_id": "staged",
        "head_commit": head_commit,
        "commit_message": commit_msg,
        "attempt_key": attempt_key,
        "diff_hash": diff_hash,
        "branch": branch,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "FAILED" if failed_standards else "PASSED",
        "score": 0,
        "violations_count": len(failed_standards),
        "standards_report": standards_report,
        "changed_files": staged_files,
        "change_summary": {
            "what_changed": "Staged changes awaiting pre-commit verification.",
            "impact": "Code modifications staged for commit.",
            "why_it_matters": "Enforces coding standards and architecture awareness.",
            "key_risks": f"{len(failed_standards)} coding standards violations detected." if failed_standards else "None detected."
        },
        "questions": formatted_questions
    }


def _max_sessions() -> int:
    """Cap on persisted sessions; UNDERSTANDING_AGENT_MAX_SESSIONS tunes it.

    Values < 2 disable pruning (keep everything). Default: 500.
    """
    try:
        v = int(os.environ.get("UNDERSTANDING_AGENT_MAX_SESSIONS", "500"))
        return v if v >= 2 else 0
    except ValueError:
        return 500


def _prune_sessions(sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only the most recent N sessions (by attempt_number, then timestamp)."""
    limit = _max_sessions()
    if not limit or len(sessions) <= limit:
        return sessions
    ranked = sorted(
        sessions,
        key=lambda s: (s.get("attempt_number", 1), s.get("timestamp", "")),
    )
    return ranked[-limit:]


def save_session(repo_root: str, session_data: Dict[str, Any]) -> Dict[str, Any]:
    """Save or update a session in the repository's sessions file."""
    path = get_sessions_file(repo_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    sessions = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    sessions = data
        except Exception:
            sessions = []

    # Ensure head_commit, commit_message, and attempt_key are present
    head_commit = session_data.get("head_commit") or get_head_commit(repo_root)
    commit_msg = session_data.get("commit_message") or get_current_commit_message(repo_root)
    diff_h = session_data.get("diff_hash", "")
    attempt_key = session_data.get("attempt_key") or compute_attempt_key(head_commit, diff_h, commit_msg)

    session_data["head_commit"] = head_commit
    session_data["commit_message"] = commit_msg
    session_data["attempt_key"] = attempt_key

    # Assign session_id if missing
    import uuid
    if not session_data.get("session_id"):
        attempt_no = session_data.get("attempt_number", 1)
        session_data["session_id"] = f"sess_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}_{attempt_no}"
    if not session_data.get("attempt_id"):
        session_data["attempt_id"] = session_data["session_id"]

    sid = session_data.get("session_id")

    # If staged attempt, ensure unique sequential attempt_number scoped strictly to:
    # the current HEAD commit (attempts made towards the next commit).
    if session_data.get("commit_id") == "staged":
        other_matching_attempts = {
            s.get("attempt_number") for s in sessions
            if (s.get("commit_id") == "staged" or s.get("is_staged") or not s.get("commit_id"))
            and s.get("session_id") != sid
            and (s.get("head_commit") or "").strip() == (head_commit or "").strip()
        }
        curr_no = session_data.get("attempt_number", 1)
        if curr_no in other_matching_attempts:
            session_data["attempt_number"] = max(other_matching_attempts) + 1

    # Deduplicate / update by session_id
    updated = False
    for i, s in enumerate(sessions):
        if s.get("session_id") == sid:
            sessions[i] = session_data
            updated = True
            break
    if not updated:
        sessions.append(session_data)

    # Prune BEFORE writing so the file never grows unboundedly on busy repos
    sessions = _prune_sessions(sessions)

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2)
    except Exception:
        alt_path = os.path.join(repo_root, "understanding_sessions.json")
        with open(alt_path, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2)

    return session_data


def get_sessions_for_commit(repo_root: str, commit_id: str, diff_hash: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return all attempts/sessions recorded for a specific commit or staged attempt."""
    all_sessions = load_sessions(repo_root)
    current_head = get_head_commit(repo_root)
    matches = []

    for s in all_sessions:
        s_commit = str(s.get("commit_id") or s.get("commit_hash") or "")
        s_diff = str(s.get("diff_hash") or "")
        s_head = s.get("head_commit")

        if commit_id == "staged":
            # Match only current staged sessions belonging to active HEAD
            if (s_commit == "staged" or s.get("is_staged")) and (not s_head or s_head == current_head):
                if diff_hash:
                    if s_diff == diff_hash:
                        matches.append(s)
                else:
                    matches.append(s)
        else:
            # Match specific historical commit
            if s_commit and (commit_id.startswith(s_commit) or s_commit.startswith(commit_id)):
                matches.append(s)
            elif diff_hash and s_diff == diff_hash:
                matches.append(s)

    matches.sort(key=lambda x: (x.get("attempt_number", 1), x.get("timestamp", "")))
    return matches


def get_session_by_id(repo_root: str, session_id: str) -> Optional[Dict[str, Any]]:
    """Find a specific session by its session_id or attempt_id."""
    for s in load_sessions(repo_root):
        if s.get("session_id") == session_id or s.get("attempt_id") == session_id:
            return s
    return None
