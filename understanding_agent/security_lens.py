"""Security lens: classify the security relevance of a change and shape questions.

Grounded in the research: ~45% of AI-generated code samples fail security tests
(Veracode 2025 GenAI report, 100+ LLMs across Java/Python/C#/JS), security
performance is flat across model generations, and developers using AI
assistants write more insecure code while feeling more confident about it
(Perry et al., CCS 2023). An oral defense question at commit time attacks
exactly that belief-reality gap.

This module is deterministic pattern classification — no LLM, no network.
It produces a security_profile that question_generator weights questions with.
"""

import re
from typing import Dict, Any, List


# Pattern class -> (label, severity 1-5, what to ask about)
# Severity drives question weighting, never a hard block.
_SECURITY_PATTERNS = [
    # --- SQL / injection sinks ---
    (r"SELECT\b[^;`]*\$\{[^}]+\}", "sql-injection-risk", 5, "how untrusted input reaches the query construction"),
    (r"(?:SELECT|INSERT|UPDATE|DELETE)\s.*\bf?%\s|\+\s*(?:user|req|request|params|query|input|data|name|id)",
     "sql-injection-risk", 5, "how untrusted input reaches the query construction"),
    (r"execute\s*\(\s*[\"'f]|cursor\.execute\(\s*f?[\"']",
     "sql-injection-risk", 5, "whether the query is parameterized"),
    (r"string\.Format\s*\(.*(?:SELECT|INSERT|UPDATE|DELETE)|CommandText\s*=",
     "sql-injection-risk", 5, "whether the SQL string is built from validated input"),
    (r"\bexec\s*\(|\beval\s*\(|eval\(.*input|new\s+Function\(",
     "code-execution-risk", 5, "why dynamic execution is needed and what constrains the input"),

    # --- XSS / output encoding ---
    (r"dangerouslySetInnerHTML|innerHTML\s*=|document\.write\(",
     "xss-risk", 5, "what sanitization happens before rendering user content"),
    (r"\bHtml\.Raw\(|Html\.Decode\(",
     "xss-risk", 5, "why raw HTML output is trusted"),

    # --- Auth / session ---
    (r"\bauth\b.*(?:password|token|session|jwt)|verify_password|createSession|sign.?in|login|isAuthenticated",
     "auth-flow", 4, "the failure path when credentials are invalid"),
    (r"jsonwebtoken|jwt\.sign|jwt\.verify|Bearer\s|validateToken|antiforgery",
     "token-handling", 4, "token expiry, signature validation, and storage"),
    (r"CompareHashAndPassword|bcrypt|argon2|password_hash|PasswordHasher",
     "password-handling", 4, "the hashing scheme and why it's appropriate"),

    # --- Secrets ---
    (r"(?:api[_-]?key|secret|password|passwd|token)\s*[:=]\s*[\"'][^\"']{8,}",
     "hardcoded-secret", 5, "where this credential should come from instead"),
    (r"AKIA[0-9A-Z]{16}|-----BEGIN\s(?:RSA\s)?PRIVATE KEY-----",
     "hardcoded-secret", 5, "where this credential should come from instead"),

    # --- Crypto ---
    (r"\bDES\b|\bMD5\b|\bSHA1\b|ECB|Cipher\.getInstance\(.*ECB|Math\.random\(\).*token|secrets\.token|random\.random\(\).*(?:token|id|key)",
     "weak-crypto", 4, "the cryptographic primitive choice and its adequacy"),

    # --- Input trust boundaries ---
    (r"body\.\w+|req\.(?:body|query|params)|request\.(?:GET|POST|data)|@RequestBody|FromBody|FromQuery|Request\.Form",
     "input-boundary", 3, "validation and sanitization at the trust boundary"),
    (r"pickle\.loads?|yaml\.load\((?!.*Loader)|marshal\.loads|ObjectInputStream",
     "unsafe-deserialization", 5, "the provenance of the deserialized data"),

    # --- SSRF / file / command ---
    (r"requests\.get\(.*(?:url|uri).*request|urlopen\(\s*(?:user|request)|fetch\(\s*(?:user|request).*\)|HttpClient.*GetAsync\(.*request",
     "ssrf-risk", 4, "whether the destination URL is from a trusted source"),
    (r"os\.system|subprocess\.(?:call|run|Popen)\(.*shell\s*=\s*True|child_process\.exec\(|Process\.Start\(|new\s+ProcessStartInfo\(",
     "command-injection-risk", 5, "how the command string is constructed and validated"),
    (r"open\(.*(?:os\.path\.join\([^)]*(?:user|request)|Path\.Combine\([^)]*(?:user|request))",
     "path-traversal-risk", 4, "path normalization before file access"),

    # --- LLM/agent-specific (OWASP LLM Top 10 adjacent) ---
    (r"(?:prompt|system_prompt|persona)\s*[:=].*\+|messages\.push\(\s*user|completion\.choices\[0\]\.message\.content|llm\.invoke|chain\.run\(",
     "llm-output-trust", 3, "whether LLM output is validated before being acted on"),
    (r"agent.*tool|execute_tool|function_call|run_agent",
     "llm-agency", 4, "the blast radius if the LLM output is adversarial"),

    # --- CORS / headers / cookies ---
    (r"Access-Control-Allow-Origin|AllowAnyOrigin|CorsPolicy|cors\(",
     "cors-config", 3, "which origins are allowed and why"),
    (r"cookie|Set-Cookie|HttpCookie|SameSite|Secure\s*=\s*true",
     "cookie-flags", 3, "cookie security flags in production"),
    (r"verify\s*=\s*False|InsecureRequestWarning|check_hostname\s*=\s*False|ServicePointManager.*ServerCertificateValidationCallback",
     "tls-verification-disabled", 5, "why TLS verification is disabled"),
]

# Patterns that downgrade severity when the change TOUCHES tests or docs only
_TEST_PATH_HINTS = ("test", "spec", "mock", "fixture", "__tests__")


class SecurityLens:
    """Deterministic security-relevance classifier for staged changes."""

    def profile_change(self, context: dict) -> dict:
        """Build a security profile from the analysis context.

        Returns: {
            risk_score: 0-100 aggregate,
            surfaces: [{file, function, label, severity, ask_about}],
            is_security_relevant: bool,
            top_surface: highest-severity surface or None
        }
        """
        surfaces = []
        for change in context.get("structured_changes", []):
            path = change.get("file", "")
            if any(h in path.lower() for h in _TEST_PATH_HINTS):
                continue
            diff = change.get("diff", "")
            if not diff:
                continue
            # Only scan added/changed lines, not context
            changed_lines = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
            added = "\n".join(changed_lines)
            found = self._scan(added)
            for label, severity, asks in found:
                surfaces.append({
                    "file": path,
                    "function": change.get("function", ""),
                    "label": label,
                    "severity": severity,
                    "ask_about": asks,
                })

        risk_score = self._aggregate(surfaces)
        top = max(surfaces, key=lambda s: s["severity"]) if surfaces else None
        return {
            "risk_score": risk_score,
            "surfaces": surfaces,
            "is_security_relevant": risk_score >= 20,
            "top_surface": top,
        }

    def _scan(self, added_code: str) -> List[tuple]:
        """Return (label, severity, ask_about) per label, merging all matching patterns."""
        by_label: Dict[str, tuple] = {}
        for pattern, label, severity, ask_about in _SECURITY_PATTERNS:
            if not re.search(pattern, added_code, re.IGNORECASE):
                continue
            if label in by_label:
                prev_sev, prev_asks = by_label[label][1], by_label[label][2]
                merged = prev_asks if ask_about in prev_asks else prev_asks + [ask_about]
                by_label[label] = (label, max(prev_sev, severity), merged)
            else:
                by_label[label] = (label, severity, [ask_about])
        return list(by_label.values())

    def _aggregate(self, surfaces: List[dict]) -> int:
        if not surfaces:
            return 0
        # Diminishing returns: top severity dominates, duplicates dampen
        severities = sorted((s["severity"] for s in surfaces), reverse=True)
        score = 0
        for i, sev in enumerate(severities[:5]):
            score += sev * (0.6 ** i)
        return min(100, int(score * 4))


def security_question_hint(profile: dict) -> Dict[str, Any]:
    """Convert a security profile into question-generator guidance."""
    if not profile.get("is_security_relevant"):
        return {"include_security_question": False}

    top = profile.get("top_surface")
    surfaces = profile.get("surfaces", [])
    focus_labels = list({s["label"] for s in surfaces})
    ask_about: List[str] = []
    for s in surfaces[:3]:
        for a in (s.get("ask_about") or []):
            if a not in ask_about:
                ask_about.append(a)
    return {
        "include_security_question": True,
        "focus_areas": focus_labels,
        "ask_about": ask_about,
        "top_file": (top or {}).get("file", ""),
        "top_function": (top or {}).get("function", ""),
        "risk_score": profile.get("risk_score", 0),
    }
