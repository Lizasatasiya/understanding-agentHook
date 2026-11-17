"""Zero-touch identity resolution (Pokerface P3, from the centralized DB doc).

Developers never log in or configure tokens: git itself mandates author
metadata, so the hook harvests it at commit time. The central service
JIT-provisions users/repos from these fields on first sight.

Privacy posture (docs/LLM_POLICY.md + the amended DB architecture doc):
  - user_email is the identity PRIMARY KEY (it is already public in every
    commit the author pushes).
  - machine_id is a one-way hash of the hostname (12 hex chars) — enough to
    correlate a developer's multiple workstations, never a raw hostname.
  - Nothing here reads secrets, credentials, or anything outside git config
    and OS user name.
"""

import getpass
import hashlib
import os
import re
import socket
import subprocess


class IdentityDetector:
    """Extracts developer/repository identity without interactive prompts."""

    @staticmethod
    def _run_git(args: list, cwd: str) -> str:
        try:
            return subprocess.check_output(
                ["git"] + args, cwd=cwd, text=True, stderr=subprocess.DEVNULL
            ).strip()
        except Exception:
            return ""

    @classmethod
    def resolve(cls, repo_root: str) -> dict:
        email = cls._run_git(["config", "user.email"], repo_root)
        name = cls._run_git(["config", "user.name"], repo_root) or getpass.getuser()
        remote_url = cls._run_git(["config", "--get", "remote.origin.url"], repo_root)

        platform = "custom"
        if "github.com" in remote_url:
            platform = "github"
        elif "gitlab.com" in remote_url:
            platform = "gitlab"
        elif "bitbucket.org" in remote_url:
            platform = "bitbucket"

        repo_slug = ""
        if remote_url:
            m = re.search(r"[:/]([^/:]+/[^/:]+?)(?:\.git)?$", remote_url)
            if m:
                repo_slug = m.group(1)
        if not repo_slug:
            repo_slug = os.path.basename(os.path.abspath(repo_root)) or "unknown/repo"

        host_hash = hashlib.sha256(
            socket.gethostname().encode("utf-8")
        ).hexdigest()[:12]

        ci_actor = (
            os.environ.get("GITHUB_ACTOR")
            or os.environ.get("GITLAB_USER_LOGIN")
            or os.environ.get("BITBUCKET_STEP_TRIGGERER_UUID")
            or ""
        )

        if not email:
            sys_user = getpass.getuser()
            email = f"{sys_user}@{socket.gethostname().lower()}.local"

        return {
            "user_email": email.lower().strip(),
            "user_name": name.strip(),
            "system_user": getpass.getuser(),
            "machine_id": host_hash,
            "repo_slug": repo_slug,
            "git_platform": platform,
            "remote_url": remote_url,
            "ci_actor": ci_actor,
        }
