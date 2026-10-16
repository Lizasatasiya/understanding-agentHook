import os
import json
import sqlite3
import time
import subprocess
from typing import List, Dict, Any, Optional
from .commit_context import (
    get_head_commit,
    get_current_commit_message,
    compute_attempt_key
)

# Pokerface P3 (PLAN_POKERFACE_JUDAS.md, decision B): the local session store
# is SQLite (.git/understanding_sessions.db, 0600) — plaintext JSON readable
# by anyone was overruled. The public API (save_session/load_sessions/
# get_sessions_for_commit/get_session_by_id) keeps its exact semantics; a
# legacy JSON file is imported once on first open and renamed to
# .imported (kept as backup, never deleted). JSON remains an EXPORT format
# (export_sessions). If the .git dir is unusable for sqlite, save/load
# degrade to the legacy JSON path so a commit is never blocked by storage.

_DB_NAME = "understanding_sessions.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    payload        TEXT NOT NULL,
    timestamp      TEXT,
    attempt_number INTEGER,
    head_commit    TEXT,
    diff_hash      TEXT,
    author         TEXT,
    author_email   TEXT,
    commit_id      TEXT,
    status         TEXT,
    score          REAL DEFAULT 0
)
"""


def get_sessions_file(repo_root: str) -> str:
    """Legacy JSON path (kept for migration + export; the live store is SQLite)."""
    git_dir = os.path.join(repo_root, ".git")
    if os.path.isdir(git_dir):
        return os.path.join(git_dir, "understanding_sessions.json")
    return os.path.join(repo_root, "understanding_sessions.json")


def get_sessions_db(repo_root: str) -> str:
    """SQLite store path, preferring .git directory if present."""
    git_dir = os.path.join(repo_root, ".git")
    if os.path.isdir(git_dir):
        return os.path.join(git_dir, _DB_NAME)
    return os.path.join(repo_root, _DB_NAME)


def _read_json_sessions(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
    except Exception:
        pass
    return []


def _migrate_legacy_json(repo_root: str, conn) -> None:
    """One-time import of the legacy JSON store into SQLite. The JSON file is
    RENAMED to <name>.imported (kept as a local backup, never deleted)."""
    for path in (get_sessions_file(repo_root),
                 os.path.join(repo_root, "understanding_sessions.json")):
        if not os.path.exists(path) or path.endswith(".imported"):
            continue
        imported = 0
        for s in _read_json_sessions(path):
            sid = s.get("session_id")
            if not sid:
                continue
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO sessions "
                    "(session_id, payload, timestamp, attempt_number, head_commit, diff_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (sid, json.dumps(s), s.get("timestamp", ""),
                     s.get("attempt_number", 1), s.get("head_commit", ""),
                     s.get("diff_hash", "")),
                )
                imported += 1
            except Exception:
                continue
        if imported:
            conn.commit()
        try:
            os.rename(path, path + ".imported")
        except Exception:
            pass  # leave the JSON in place; IGNORE-dedupe makes re-import safe


def _migrate_schema_columns(conn) -> None:
    """Add author, commit, status, and score columns to existing DB if missing."""
    try:
        cursor = conn.execute("PRAGMA table_info(sessions)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        for col_name, col_type in [
            ("author", "TEXT"),
            ("author_email", "TEXT"),
            ("commit_id", "TEXT"),
            ("status", "TEXT"),
            ("score", "REAL DEFAULT 0"),
        ]:
            if col_name not in existing_cols:
                try:
                    conn.execute(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_type}")
                except Exception:
                    pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_author_email ON sessions(author_email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_commit_id ON sessions(commit_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_head_commit ON sessions(head_commit)")
    except Exception:
        pass


def _backfill_author_data(repo_root: str, conn) -> None:
    """Ensure existing rows have author, author_email, commit_id, status, and score populated."""
    try:
        rows = conn.execute(
            "SELECT session_id, payload, head_commit, author, author_email, commit_id, status, score "
            "FROM sessions WHERE author IS NULL OR author = '' OR commit_id IS NULL OR commit_id = ''"
        ).fetchall()
        if not rows:
            return

        default_author = ""
        default_email = ""
        try:
            default_author = subprocess.check_output(
                ["git", "config", "user.name"], cwd=repo_root, text=True, stderr=subprocess.DEVNULL
            ).strip()
            default_email = subprocess.check_output(
                ["git", "config", "user.email"], cwd=repo_root, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            pass

        updates = []
        for sid, payload_str, head_commit, curr_author, curr_email, curr_cid, curr_status, curr_score in rows:
            try:
                data = json.loads(payload_str)
            except Exception:
                data = {}

            cid = data.get("commit_id") or curr_cid or ""
            author = data.get("author") or (data.get("identity") or {}).get("user_name") or curr_author or ""
            email = data.get("author_email") or (data.get("identity") or {}).get("user_email") or curr_email or ""

            target_commit = cid if (cid and cid != "staged") else head_commit
            if (not author or not email) and target_commit:
                try:
                    git_meta = subprocess.check_output(
                        ["git", "log", "-1", "--pretty=format:%an|%ae", target_commit],
                        cwd=repo_root, text=True, stderr=subprocess.DEVNULL
                    ).strip()
                    if "|" in git_meta:
                        g_author, g_email = git_meta.split("|", 1)
                        author = author or g_author
                        email = email or g_email
                except Exception:
                    pass

            author = author or default_author or "Developer"
            email = (email or default_email or "developer@local").lower()
            status = data.get("status") or curr_status or "PASSED"
            try:
                score = float(data.get("score") if data.get("score") is not None else (curr_score or 0.0))
            except (ValueError, TypeError):
                score = 0.0

            changed = False
            if not data.get("author"):
                data["author"] = author
                changed = True
            if not data.get("author_email"):
                data["author_email"] = email
                changed = True
            if not data.get("commit_id") and cid:
                data["commit_id"] = cid
                changed = True

            new_payload = json.dumps(data) if changed else payload_str
            updates.append((author, email, cid, status, score, new_payload, sid))

        if updates:
            conn.executemany(
                "UPDATE sessions SET author = ?, author_email = ?, commit_id = ?, status = ?, score = ?, payload = ? WHERE session_id = ?",
                updates
            )
            conn.commit()
    except Exception:
        pass


def _connect(repo_root: str):
    path = get_sessions_db(repo_root)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.execute(_SCHEMA)
    _migrate_schema_columns(conn)
    _migrate_legacy_json(repo_root, conn)
    _backfill_author_data(repo_root, conn)
    try:
        os.chmod(path, 0o600)  # developer answers are personal data
    except Exception:
        pass
    return conn


def _row_from_session(s: Dict[str, Any]) -> tuple:
    return (
        s.get("session_id"),
        json.dumps(s),
        s.get("timestamp", ""),
        s.get("attempt_number", 1),
        s.get("head_commit", ""),
        s.get("diff_hash", ""),
        s.get("author", ""),
        s.get("author_email", ""),
        s.get("commit_id", ""),
        s.get("status", "PASSED"),
        float(s.get("score", 0.0) or 0.0),
    )


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


def _load_sessions_db(repo_root: str) -> List[Dict[str, Any]]:
    conn = _connect(repo_root)
    try:
        rows = conn.execute("SELECT payload FROM sessions ORDER BY rowid").fetchall()
    finally:
        conn.close()
    sessions = []
    for (payload,) in rows:
        try:
            sessions.append(json.loads(payload))
        except Exception:
            continue
    return sessions


def load_sessions(repo_root: str) -> List[Dict[str, Any]]:
    """Load all persisted sessions for the given repository and reconcile completed commits."""
    try:
        sessions = _load_sessions_db(repo_root)
    except Exception:
        # Storage unusable (read-only .git, locked db): read legacy JSON only.
        sessions = _read_json_sessions(get_sessions_file(repo_root))
        fallback_path = os.path.join(repo_root, "understanding_sessions.json")
        if os.path.exists(fallback_path):
            existing_ids = {s.get("session_id") for s in sessions if s.get("session_id")}
            for item in _read_json_sessions(fallback_path):
                if item.get("session_id") not in existing_ids:
                    sessions.append(item)
        return sessions

    if reconcile_sessions(repo_root, sessions):
        try:
            conn = _connect(repo_root)
            with conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO sessions "
                    "(session_id, payload, timestamp, attempt_number, head_commit, diff_hash, author, author_email, commit_id, status, score) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [_row_from_session(s) for s in sessions],
                )
            conn.close()
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
    """Keep only the most recent N sessions (by timestamp, then attempt_number).

    Timestamp is the primary key — attempt_number RESETS per commit cycle, so
    ranking by attempt_number first would prune a brand-new session (attempt 1)
    in favor of old sessions that happened to accrue high attempt numbers.
    attempt_number is only the within-a-second tiebreaker.
    """
    limit = _max_sessions()
    if not limit or len(sessions) <= limit:
        return sessions
    ranked = sorted(
        sessions,
        key=lambda s: (s.get("timestamp", ""), s.get("attempt_number", 1)),
    )
    return ranked[-limit:]


def _prune_db(conn) -> None:
    """SQLite-side prune mirroring _prune_sessions (timestamp, then attempt)."""
    limit = _max_sessions()
    if not limit:
        return
    count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    if count <= limit:
        return
    conn.execute(
        "DELETE FROM sessions WHERE session_id NOT IN ("
        "  SELECT session_id FROM sessions"
        "  ORDER BY timestamp DESC, attempt_number DESC LIMIT ?"
        ")",
        (limit,),
    )


def save_session(repo_root: str, session_data: Dict[str, Any]) -> Dict[str, Any]:
    """Save or update a session in the repository's SQLite store."""
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

    # Ensure author and email on session_data
    if not session_data.get("author") or not session_data.get("author_email"):
        ident = session_data.get("identity") or {}
        author = ident.get("user_name") or session_data.get("author")
        email = ident.get("user_email") or session_data.get("author_email")
        if not author:
            try:
                author = subprocess.check_output(
                    ["git", "config", "user.name"], cwd=repo_root, text=True, stderr=subprocess.DEVNULL
                ).strip()
            except Exception:
                author = "Developer"
        if not email:
            try:
                email = subprocess.check_output(
                    ["git", "config", "user.email"], cwd=repo_root, text=True, stderr=subprocess.DEVNULL
                ).strip()
            except Exception:
                email = "developer@local"
        session_data["author"] = author
        session_data["author_email"] = (email or "").lower()

    try:
        conn = _connect(repo_root)
        try:
            # If staged attempt, ensure unique sequential attempt_number scoped
            # strictly to: the current HEAD commit (attempts towards the next commit).
            if session_data.get("commit_id") == "staged":
                rows = conn.execute(
                    "SELECT attempt_number, head_commit, session_id FROM sessions"
                ).fetchall()
                other_matching_attempts = {
                    r[0] for r in rows
                    if r[2] != sid
                    and (r[1] or "").strip() == (head_commit or "").strip()
                    # legacy staged shape: commit_id lives inside the payload
                    and _is_staged_row(conn, r[2])
                }
                curr_no = session_data.get("attempt_number", 1)
                if curr_no in other_matching_attempts:
                    session_data["attempt_number"] = max(other_matching_attempts) + 1

            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO sessions "
                    "(session_id, payload, timestamp, attempt_number, head_commit, diff_hash, author, author_email, commit_id, status, score) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    _row_from_session(session_data),
                )
                _prune_db(conn)
        finally:
            conn.close()
        return session_data
    except Exception:
        pass

    # Fallback: legacy JSON store (read-only .git, sqlite failure of any kind)
    path = get_sessions_file(repo_root)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    sessions = _read_json_sessions(path)
    # staged-attempt uniqueness (same contract as above)
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

    updated = False
    for i, s in enumerate(sessions):
        if s.get("session_id") == sid:
            sessions[i] = session_data
            updated = True
            break
    if not updated:
        sessions.append(session_data)
    sessions = _prune_sessions(sessions)

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2)
    except Exception:
        alt_path = os.path.join(repo_root, "understanding_sessions.json")
        with open(alt_path, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2)

    return session_data


def _is_staged_row(conn, session_id: str) -> bool:
    """True if the row's payload records a staged (or legacy staged) attempt."""
    row = conn.execute(
        "SELECT payload FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not row:
        return False
    try:
        s = json.loads(row[0])
        return (s.get("commit_id") == "staged" or s.get("is_staged")
                or not s.get("commit_id"))
    except Exception:
        return False


def export_sessions(repo_root: str, export_path: str) -> int:
    """Export all sessions to a JSON file (portability/inspection). Returns count."""
    sessions = load_sessions(repo_root)
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=2)
    return len(sessions)


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


def get_db_users(repo_root: str) -> List[Dict[str, Any]]:
    """Return all unique developers/users from the DB with aggregate commit & attempt metrics."""
    conn = _connect(repo_root)
    try:
        rows = conn.execute(
            "SELECT author, author_email, commit_id, status, score, timestamp, payload FROM sessions"
        ).fetchall()
    finally:
        conn.close()

    users_map: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        author, email, commit_id, status, score, timestamp, payload = row
        if not author and not email:
            try:
                d = json.loads(payload)
                author = d.get("author") or (d.get("identity") or {}).get("user_name")
                email = d.get("author_email") or (d.get("identity") or {}).get("user_email")
            except Exception:
                pass

        email_clean = (email or "").strip().lower()
        author_clean = (author or "").strip()
        user_key = email_clean or author_clean.lower() or "developer@local"

        if user_key not in users_map:
            users_map[user_key] = {
                "name": author_clean or email_clean or "Developer",
                "email": email_clean or "developer@local",
                "commits": set(),
                "attempts_count": 0,
                "passed_attempts": 0,
                "failed_attempts": 0,
                "scores": [],
                "latest_activity": timestamp or "",
            }

        u = users_map[user_key]
        u["attempts_count"] += 1
        if status == "PASSED":
            u["passed_attempts"] += 1
        else:
            u["failed_attempts"] += 1
        if score is not None:
            try:
                u["scores"].append(float(score))
            except (ValueError, TypeError):
                pass
        if commit_id and commit_id != "staged":
            u["commits"].add(commit_id)
        if timestamp and (not u["latest_activity"] or timestamp > u["latest_activity"]):
            u["latest_activity"] = timestamp

    # Augment commit counts with git log history to ensure all author commits are included
    try:
        git_log = subprocess.check_output(
            ["git", "log", "-n", "100", "--pretty=format:%H|%an|%ae|%aI"],
            cwd=repo_root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        for line in git_log.splitlines():
            parts = line.split("|", 3)
            if len(parts) >= 3:
                h, an, ae = parts[0], parts[1].strip(), parts[2].strip().lower()
                ts = parts[3] if len(parts) > 3 else ""
                key = ae or an.lower()
                if key not in users_map:
                    users_map[key] = {
                        "name": an,
                        "email": ae,
                        "commits": set(),
                        "attempts_count": 0,
                        "passed_attempts": 0,
                        "failed_attempts": 0,
                        "scores": [],
                        "latest_activity": ts,
                    }
                users_map[key]["commits"].add(h)
                if ts and (not users_map[key]["latest_activity"] or ts > users_map[key]["latest_activity"]):
                    users_map[key]["latest_activity"] = ts
    except Exception:
        pass

    result = []
    for u in users_map.values():
        total_att = u["attempts_count"]
        pass_rate = round((u["passed_attempts"] / total_att * 100), 1) if total_att > 0 else 100.0
        avg_score = round(sum(u["scores"]) / len(u["scores"]), 1) if u["scores"] else 0.0
        result.append({
            "name": u["name"],
            "email": u["email"],
            "commit_count": len(u["commits"]),
            "attempts_count": total_att,
            "passed_count": u["passed_attempts"],
            "pass_rate": pass_rate,
            "avg_score": avg_score,
            "latest_activity": u["latest_activity"],
        })

    result.sort(key=lambda x: (-x["commit_count"], -x["attempts_count"], x["name"]))
    return result


def get_db_commits(
    repo_root: str,
    user: Optional[str] = None,
    limit: int = 50,
    include_staged: bool = False
) -> List[Dict[str, Any]]:
    """Retrieve commits directly from the Database, optionally filtered by user."""
    all_sessions = load_sessions(repo_root)
    commits_list = []

    # Map commit_id to sessions in DB
    sessions_by_commit: Dict[str, List[Dict[str, Any]]] = {}
    for s in all_sessions:
        cid = s.get("commit_id") or "staged"
        sessions_by_commit.setdefault(cid, []).append(s)

    # Staged changes (if requested)
    if include_staged:
        try:
            staged_out = subprocess.check_output(
                ["git", "diff", "--cached", "--name-status"],
                cwd=repo_root, text=True, stderr=subprocess.DEVNULL
            ).strip()
            if staged_out:
                staged_sessions = sessions_by_commit.get("staged", [])
                latest_status = staged_sessions[-1].get("status", "FAILED") if staged_sessions else "PENDING"
                current_author = ""
                current_email = ""
                try:
                    current_author = subprocess.check_output(
                        ["git", "config", "user.name"], cwd=repo_root, text=True, stderr=subprocess.DEVNULL
                    ).strip()
                    current_email = subprocess.check_output(
                        ["git", "config", "user.email"], cwd=repo_root, text=True, stderr=subprocess.DEVNULL
                    ).strip()
                except Exception:
                    pass

                include_this = True
                if user and user.strip():
                    u_filter = user.strip().lower()
                    include_this = (
                        u_filter in (current_author or "").lower() or
                        u_filter in (current_email or "").lower()
                    )

                if include_this:
                    branch = "main"
                    try:
                        branch = subprocess.check_output(
                            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, text=True, stderr=subprocess.DEVNULL
                        ).strip()
                    except Exception:
                        pass
                    commits_list.append({
                        "id": "staged",
                        "hash": "staged",
                        "short_hash": "staged",
                        "author": current_author or "Current Working Tree",
                        "author_email": current_email,
                        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "message": "Pending Staged Changes (Uncommitted)",
                        "branch": branch,
                        "is_staged": True,
                        "status": latest_status,
                        "attempts_count": len(staged_sessions),
                        "files_count": len(staged_out.splitlines())
                    })
        except Exception:
            pass

    # Historical commits backed by DB sessions and git history
    try:
        log_out = subprocess.check_output(
            ["git", "log", f"-n{limit}", "--pretty=format:%H|%h|%an|%ae|%aI|%s"],
            cwd=repo_root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        log_out = ""

    current_branch = "main"
    try:
        current_branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        pass

    for line in log_out.splitlines():
        parts = line.split("|", 5)
        if len(parts) == 6:
            full_h, short_h, author, email, date_str, subject = parts
            commit_sessions = get_sessions_for_commit(repo_root, full_h)

            # Apply user filter if present
            if user and user.strip():
                u_filter = user.strip().lower()
                if u_filter not in (author or "").lower() and u_filter not in (email or "").lower():
                    continue

            commits_list.append({
                "id": full_h,
                "hash": full_h,
                "short_hash": short_h,
                "author": author,
                "author_email": email,
                "date": date_str,
                "message": subject,
                "branch": current_branch,
                "is_staged": False,
                "status": "PASSED",
                "attempts_count": len(commit_sessions)
            })

    return commits_list

