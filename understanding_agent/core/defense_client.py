"""HTTP client for the server-side oral defense.

The model key never lives here. When /etc/understanding-agent/config.env
exists, it is the only configuration the hook trusts, so a developer cannot
point the hook at a different server by exporting an environment variable.
"""

import hmac
import hashlib
import json
import os
import urllib.error
import urllib.request

_CONFIG_PATH = "/etc/understanding-agent/config.env"
_START_TIMEOUT = 120
_ANSWER_TIMEOUT = 90


class DefenseUnavailable(Exception):
    """The defense service could not be reached or refused to run the model."""


class DefenseRejected(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _parse_env_file(path: str) -> dict:
    found = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                key, value = line.split("=", 1)
                value = value.strip().strip('"').strip("'")
                if key.strip() and value:
                    found[key.strip()] = value
    except Exception:
        return {}
    return found


def load_server_config() -> dict:
    file_cfg = _parse_env_file(_CONFIG_PATH)
    if file_cfg.get("UNDERSTANDING_AGENT_SERVER_URL"):
        return file_cfg
    return {
        "UNDERSTANDING_AGENT_SERVER_URL": os.environ.get("UNDERSTANDING_AGENT_SERVER_URL", "").strip(),
        "UNDERSTANDING_AGENT_TEAM_TOKEN": os.environ.get("UNDERSTANDING_AGENT_TEAM_TOKEN", "").strip(),
        "UNDERSTANDING_AGENT_TEAM_SECRET": os.environ.get("UNDERSTANDING_AGENT_TEAM_SECRET", "").strip(),
    }


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class DefenseClient:
    def __init__(self, config: dict | None = None):
        self.config = config if config is not None else load_server_config()

    def _base(self) -> str:
        return (self.config.get("UNDERSTANDING_AGENT_SERVER_URL") or "").strip().rstrip("/")

    def _post(self, path: str, payload: dict, timeout: int) -> dict:
        base = self._base()
        if not base:
            raise DefenseUnavailable(
                "No defense server is configured. An admin must set the server URL."
            )
        body = _canonical(payload)
        headers = {"Content-Type": "application/json"}
        token = (self.config.get("UNDERSTANDING_AGENT_TEAM_TOKEN") or "").strip()
        if token:
            headers["X-Team-Token"] = token
        secret = (self.config.get("UNDERSTANDING_AGENT_TEAM_SECRET") or "").strip()
        if secret:
            headers["X-Signature"] = _sign(secret, body)
        request = urllib.request.Request(
            base + path, data=body, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(detail)
                detail = parsed.get("detail") or detail
            except Exception:
                pass
            if exc.code >= 500:
                raise DefenseUnavailable(str(detail) or "Defense service failed") from exc
            raise DefenseRejected(exc.code, str(detail)) from exc
        except DefenseUnavailable:
            raise
        except Exception as exc:
            raise DefenseUnavailable("Defense service is unavailable") from exc
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise DefenseUnavailable("Defense service returned an unreadable response") from exc
        if not isinstance(data, dict):
            raise DefenseUnavailable("Defense service returned an unreadable response")
        return data

    def start(self, payload: dict) -> dict:
        return self._post("/api/v1/defense/start", payload, _START_TIMEOUT)

    def answer(self, defense_id: str, question_id: str, answer_text: str) -> dict:
        return self._post(
            f"/api/v1/defense/{defense_id}/answer",
            {"question_id": question_id, "answer": answer_text or ""},
            _ANSWER_TIMEOUT,
        )
