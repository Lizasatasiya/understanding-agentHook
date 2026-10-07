import os
import urllib.request
import json


class ServerClient:
    """Sends session audit payloads to a self-hosted dashboard.

    Opt-in and disabled by default: nothing is sent unless
    UNDERSTANDING_AGENT_TELEMETRY_URL is set to your server's endpoint
    (e.g. http://dashboard.internal:8000/understanding-session).
    Failures are logged but never affect the commit.
    """

    def send(self, session_data: dict):
        url = os.environ.get("UNDERSTANDING_AGENT_TELEMETRY_URL", "").strip()
        if not url or url.lower() in ("off", "none", "disabled", "false"):
            return

        data = json.dumps(session_data).encode('utf-8')

        req = urllib.request.Request(
            url,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                if response.status != 200:
                    pass
        except Exception:
            # Do not fail or disrupt the commit if the server is offline
            pass
