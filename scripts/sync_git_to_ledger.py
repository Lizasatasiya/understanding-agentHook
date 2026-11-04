import os
import sys
import json
import subprocess
import datetime
from sqlalchemy import create_engine, select, text
from service.models import User, Repository, SessionRecord

DATABASE_URL = os.environ.get(
    "SERVICE_DATABASE_URL",
    "postgresql+psycopg://understanding:understanding@127.0.0.1:5434/understanding_ledger"
)

engine = create_engine(DATABASE_URL)

def get_git_commits(repo_dir, rev_range):
    out = subprocess.check_output(
        ['git', 'log', '--format=%H|%an|%ae|%at|%s', rev_range],
        cwd=repo_dir
    ).decode('utf-8', errors='replace').strip()
    if not out:
        return []
    res = []
    for line in out.splitlines():
        parts = line.split('|', 4)
        if len(parts) == 5:
            chash, author_name, author_email, ts, msg = parts
            try:
                files_out = subprocess.check_output(
                    ['git', 'show', '--name-status', '--format=', chash],
                    cwd=repo_dir
                ).decode('utf-8', errors='replace').strip()
                files = []
                for f_line in files_out.splitlines():
                    if f_line.strip():
                        f_parts = f_line.strip().split(None, 1)
                        if len(f_parts) == 2:
                            st_code, pth = f_parts
                            status_str = 'added' if st_code.startswith('A') else ('deleted' if st_code.startswith('D') else 'modified')
                            files.append({'path': pth, 'status': status_str, 'additions': 0, 'deletions': 0})
            except Exception:
                files = []
            res.append({
                'hash': chash,
                'author_name': author_name.strip(),
                'author_email': author_email.strip().lower(),
                'timestamp': datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc),
                'message': msg.strip(),
                'files': files
            })
    return res


def sync():
    with engine.begin() as conn:
        # 1. Clean up stale or non-commit staged entries in sessions
        conn.execute(text("DELETE FROM sessions WHERE commit_hash = 'staged' OR commit_hash = '' OR commit_hash IS NULL;"))

        # Find or create users
        users_cache = {}
        for u in conn.execute(select(User.id, User.email, User.full_name)).fetchall():
            users_cache[u[1].lower()] = u[0]

        def get_or_create_user(name, email):
            email_lower = email.lower().strip()
            if email_lower in users_cache:
                return users_cache[email_lower]
            res = conn.execute(
                text("INSERT INTO users (email, full_name, system_username, first_seen_at, last_active_at) "
                     "VALUES (:email, :full_name, :system_username, NOW(), NOW()) RETURNING id;"),
                {"email": email_lower, "full_name": name, "system_username": name}
            ).fetchone()
            uid = res[0]
            users_cache[email_lower] = uid
            return uid

        # -------------------------------------------------------------
        # Sync understanding-agentHook (repo_id = 4)
        # -------------------------------------------------------------
        hook_dir = '/Users/liza/Documents/PRE-COMMIT-DEMO/understanding-agent-hook'
        repo_id = 4

        # Branches to ingest with their exact commit ranges
        branches = [
            ("cost-est", "dev..cost-est"),
            ("dev", "main..dev"),
            ("test-york-llm", "main..test-york-llm"),
            ("main", "main"),
        ]

        print("Syncing understanding-agentHook...")
        for branch_name, rev_range in branches:
            commits = get_git_commits(hook_dir, rev_range)
            print(f"  Branch {branch_name} ({rev_range}): {len(commits)} commits")
            for c in commits:
                chash = c['hash']
                uid = get_or_create_user(c['author_name'], c['author_email'])

                # Check if session exists for this commit
                existing = conn.execute(
                    select(SessionRecord.id, SessionRecord.status, SessionRecord.branch, SessionRecord.identity, SessionRecord.change_summary)
                    .where(SessionRecord.repo_id == repo_id, SessionRecord.commit_hash == chash)
                ).fetchall()

                if existing:
                    # Update branch and commit message if needed, keep status and Q&A!
                    for row in existing:
                        s_id, s_status, s_branch, s_ident, s_csum = row
                        new_ident = dict(s_ident or {})
                        new_ident["commit_message"] = c['message']
                        new_ident["branches"] = [branch_name]
                        new_csum = dict(s_csum or {})
                        new_csum["commit_message"] = c['message']
                        conn.execute(
                            text("UPDATE sessions SET branch = :branch, user_id = :user_id, identity = :ident, change_summary = :csum WHERE id = :id;"),
                            {"branch": branch_name, "user_id": uid, "ident": json.dumps(new_ident), "csum": json.dumps(new_csum), "id": s_id}
                        )
                else:
                    # Insert unaudited session record
                    sid = f"git_{chash[:12]}"
                    ident = {
                        "user_name": c['author_name'],
                        "user_email": c['author_email'],
                        "commit_message": c['message'],
                        "branches": [branch_name],
                    }
                    csum = {
                        "commit_message": c['message'],
                        "what_changed": c['message'],
                    }
                    conn.execute(
                        text("""
                            INSERT INTO sessions (
                                session_id, schema_version, user_id, repo_id, branch,
                                commit_hash, head_commit, diff_hash, attempt_number,
                                status, verification, score, violations_count, machine_id,
                                changed_files, change_summary, standards_report, evidence, analysis,
                                created_at, identity
                            ) VALUES (
                                :session_id, 2, :user_id, :repo_id, :branch,
                                :commit_hash, :head_commit, '', 0,
                                'UNAUDITED', 'no-defense-recorded', 0.0, 0, 'git',
                                :changed_files, :change_summary, '[]', '{}', '{}',
                                :created_at, :identity
                            )
                        """),
                        {
                            "session_id": sid,
                            "user_id": uid,
                            "repo_id": repo_id,
                            "branch": branch_name,
                            "commit_hash": chash,
                            "head_commit": chash,
                            "changed_files": json.dumps(c['files']),
                            "change_summary": json.dumps(csum),
                            "created_at": c['timestamp'],
                            "identity": json.dumps(ident),
                        }
                    )

        # -------------------------------------------------------------
        # Sync code-understanding-demo (repo_id = 3)
        # -------------------------------------------------------------
        demo_dir = '/Users/liza/Documents/PRE-COMMIT-DEMO/code-understanding-demo'
        demo_repo_id = 3
        print("\nSyncing code-understanding-demo...")

        demo_branches = [
            ("cost-est", "feat/test-1..cost-est"),
            ("feat/test-1", "feat/test-1"),
        ]

        for branch_name, rev_range in demo_branches:
            demo_commits = get_git_commits(demo_dir, rev_range)
            print(f"  Branch {branch_name} ({rev_range}): {len(demo_commits)} commits")
            for c in demo_commits:
                chash = c['hash']
                uid = get_or_create_user(c['author_name'], c['author_email'])

                existing = conn.execute(
                    select(SessionRecord.id, SessionRecord.status, SessionRecord.branch, SessionRecord.identity, SessionRecord.change_summary)
                    .where(SessionRecord.repo_id == demo_repo_id, SessionRecord.commit_hash == chash)
                ).fetchall()

                if existing:
                    for row in existing:
                        s_id, s_status, s_branch, s_ident, s_csum = row
                        new_ident = dict(s_ident or {})
                        new_ident["commit_message"] = c['message']
                        new_ident["branches"] = [branch_name]
                        new_csum = dict(s_csum or {})
                        new_csum["commit_message"] = c['message']
                        conn.execute(
                            text("UPDATE sessions SET branch = :branch, user_id = :user_id, identity = :ident, change_summary = :csum WHERE id = :id;"),
                            {"branch": branch_name, "user_id": uid, "ident": json.dumps(new_ident), "csum": json.dumps(new_csum), "id": s_id}
                        )
                else:
                    sid = f"git_demo_{chash[:10]}"
                    ident = {
                        "user_name": c['author_name'],
                        "user_email": c['author_email'],
                        "commit_message": c['message'],
                        "branches": [branch_name],
                    }
                    csum = {
                        "commit_message": c['message'],
                        "what_changed": c['message'],
                    }
                    conn.execute(
                        text("""
                            INSERT INTO sessions (
                                session_id, schema_version, user_id, repo_id, branch,
                                commit_hash, head_commit, diff_hash, attempt_number,
                                status, verification, score, violations_count, machine_id,
                                changed_files, change_summary, standards_report, evidence, analysis,
                                created_at, identity
                            ) VALUES (
                                :session_id, 2, :user_id, :repo_id, :branch,
                                :commit_hash, :head_commit, '', 0,
                                'UNAUDITED', 'no-defense-recorded', 0.0, 0, 'git',
                                :changed_files, :change_summary, '[]', '{}', '{}',
                                :created_at, :identity
                            )
                        """),
                        {
                            "session_id": sid,
                            "user_id": uid,
                            "repo_id": demo_repo_id,
                            "branch": branch_name,
                            "commit_hash": chash,
                            "head_commit": chash,
                            "changed_files": json.dumps(c['files']),
                            "change_summary": json.dumps(csum),
                            "created_at": c['timestamp'],
                            "identity": json.dumps(ident),
                        }
                    )

    print("\nSync completed successfully!")

if __name__ == '__main__':
    sync()
