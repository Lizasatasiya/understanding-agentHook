"""Session sync client: local → central service (Pokerface P3).

Two transports, both opt-in and fail-silent (a commit is NEVER blocked or
slowed by sync problems):

  1. Central sync — set UNDERSTANDING_AGENT_SYNC_URL to the central service
     BASE url (e.g. http://understanding.internal:8000). Sessions POST to
     /api/v1/sessions; when the service is unreachable the payload is
     queued to .git/understanding_sync_queue.json and drained (batch) on
     the next hook run. This is the Judas ledger feed.
       - UNDERSTANDING_AGENT_TEAM_KEY: shared team token (X-Team-Token header)
       - UNDERSTANDING_AGENT_TEAM_SECRET: HMAC-SHA256 key; the canonical
         payload is signed and sent as X-Signature. Ceiling (documented in
         the DB architecture doc): this proves "someone holding the team
         secret", not "this developer answered honestly".
  2. Legacy telemetry — UNDERSTANDING_AGENT_TELEMETRY_URL is a FULL endpoint;
     single POST, no queue (unchanged behavior for existing setups).

Sync budget: 2.5s. Offline rule: network failure enqueues and the commit
proceeds — identical to the offline-first rule in the central DB doc.
"""

import hmac
import hashlib
import json
import os
import time
import urllib.request

SYNC_TIMEOUT_SECONDS = 2.5
_QUEUE_NAME = "understanding_sync_queue.json"


def _load_repo_config(repo_root: str | None = None) -> dict:
    """Safely load project-level configuration from repository root or current dir."""
    candidate_dirs = []
    if repo_root and os.path.isdir(repo_root):
        candidate_dirs.append(repo_root)
    cwd = os.getcwd()
    if cwd not in candidate_dirs:
        candidate_dirs.append(cwd)

    for directory in candidate_dirs:
        for filename in (".understanding-agent.json", ".understanding-agent.yaml", ".understanding-agent.yml"):
            cfg_path = os.path.join(directory, filename)
            if os.path.isfile(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        if filename.endswith(".json"):
                            data = json.load(f)
                        else:
                            # Simple key: value parser for yaml without third-party dependencies
                            data = {}
                            for line in f:
                                line = line.strip()
                                if line and not line.startswith("#") and ":" in line:
                                    k, v = line.split(":", 1)
                                    data[k.strip()] = v.strip().strip("'\"")
                        if isinstance(data, dict):
                            return data
                except Exception:
                    pass
    return {}


DEFAULT_CENTRAL_SYNC_URL = "http://localhost:8088"
DEFAULT_TEAM_KEY = "team-token-secret-123"
DEFAULT_TEAM_SECRET = "team-hmac-secret-456"


def _resolve_config(repo_root: str | None = None) -> tuple[str, str, str]:
    """Securely resolve (sync_url, team_token, team_secret).

    Precedence:
      1. Environment variables (UNDERSTANDING_AGENT_SYNC_URL, etc.)
      2. Repo-level config file (.understanding-agent.json / .yaml)
      3. Built-in central server defaults (active globally across all projects)
    """
    repo_cfg = _load_repo_config(repo_root)

    sync_url = (
        os.environ.get("UNDERSTANDING_AGENT_SYNC_URL")
        or repo_cfg.get("sync_url")
        or DEFAULT_CENTRAL_SYNC_URL
    ).strip().rstrip("/")

    team_key = (
        os.environ.get("UNDERSTANDING_AGENT_TEAM_KEY")
        or repo_cfg.get("team_key")
        or repo_cfg.get("team_token")
        or DEFAULT_TEAM_KEY
    ).strip()

    team_secret = (
        os.environ.get("UNDERSTANDING_AGENT_TEAM_SECRET")
        or repo_cfg.get("team_secret")
        or DEFAULT_TEAM_SECRET
    ).strip()

    return sync_url, team_key, team_secret


def _sync_base_url(repo_root: str | None = None) -> str:
    url, _, _ = _resolve_config(repo_root)
    return url


def _legacy_telemetry_url() -> str:
    url = os.environ.get("UNDERSTANDING_AGENT_TELEMETRY_URL", "").strip()
    if url.lower() in ("off", "none", "disabled", "false"):
        return ""
    return url


def _canonical_payload(session_data: dict) -> bytes:
    return json.dumps(session_data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _headers(session_data: dict, body: bytes, team_key: str = "", team_secret: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if team_key:
        headers["X-Team-Token"] = team_key
    if team_secret:
        headers["X-Signature"] = _sign(team_secret, body)
    return headers


def _queue_path(repo_root: str) -> str:
    git_dir = os.path.join(repo_root or os.getcwd(), ".git")
    if os.path.isdir(git_dir):
        return os.path.join(git_dir, _QUEUE_NAME)
    return os.path.join(repo_root or os.getcwd(), _QUEUE_NAME)


def _load_queue(repo_root: str) -> list:
    path = _queue_path(repo_root)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _write_queue(repo_root: str, queue: list) -> None:
    path = _queue_path(repo_root)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(queue, f)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _post(url: str, session_data: dict, team_key: str = "", team_secret: str = "") -> bool:
    """One POST with the sync budget. True on HTTP 2xx."""
    body = _canonical_payload(session_data)
    req = urllib.request.Request(
        url,
        data=body,
        headers=_headers(session_data, body, team_key=team_key, team_secret=team_secret),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=SYNC_TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


class ServerClient:
    """Sends session payloads to the central ledger (or legacy telemetry)."""

    def send(self, session_data: dict, repo_root: str | None = None):
        repo_root = repo_root or os.getcwd()
        base, team_key, team_secret = _resolve_config(repo_root)

        if base:
            # 1. Drain any queued sessions from earlier offline runs FIRST.
            queue = _load_queue(repo_root)
            if queue:
                if _post(f"{base}/api/v1/sessions/batch", {"sessions": queue}, team_key=team_key, team_secret=team_secret):
                    _write_queue(repo_root, [])
            # 2. Deliver this session; enqueue on failure.
            if _post(f"{base}/api/v1/sessions", session_data, team_key=team_key, team_secret=team_secret):
                return
            queue = _load_queue(repo_root)
            queue.append(session_data)
            _write_queue(repo_root, queue[-500:])  # bounded, like the session store
            return

        # Legacy single-endpoint telemetry (no queue, unchanged).
        url = _legacy_telemetry_url()
        if not url:
            return
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(session_data).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as response:
                if response.status != 200:
                    pass
        except Exception:
            # Do not fail or disrupt the commit if the server is offline
            pass
