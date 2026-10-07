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

import math
import os
import re
import subprocess
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

    # --- Transport (moved from coding_standards._check_secure_transport, A0.1) ---
    (r"['\"]http://(?!localhost|127\.0\.0\.1|www\.w3\.org)[a-zA-Z0-9\-_.]+",
     "cleartext-transport", 4, "why this endpoint uses cleartext HTTP instead of HTTPS"),

    # --- CORS / headers / cookies ---
    (r"Access-Control-Allow-Origin|AllowAnyOrigin|CorsPolicy|cors\(",
     "cors-config", 3, "which origins are allowed and why"),
    (r"cookie|Set-Cookie|HttpCookie|SameSite|Secure\s*=\s*true",
     "cookie-flags", 3, "cookie security flags in production"),
    (r"verify\s*=\s*False|InsecureRequestWarning|check_hostname\s*=\s*False|ServicePointManager.*ServerCertificateValidationCallback",
     "tls-verification-disabled", 5, "why TLS verification is disabled"),
]

# Paths where example-shaped content legitimately lives: tests, fixtures,
# documentation, and *.example/*.sample/*.template config files. Exact vendor
# formats STILL block in these paths (a real key is real anywhere); only the
# generic/example-shaped tiers are exempt here.
_TEST_PATH_HINTS = ("test", "spec", "mock", "fixture", "__tests__")
_DOC_PATH_HINTS = (".md", ".rst", ".txt", "docs/", "readme")
_EXAMPLE_FILE_SUFFIXES = (".example", ".sample", ".template", ".tpl")

# Deterministic, catastrophic findings: known live-credential formats.
# These BLOCK the commit outright (before questions) — no answer the developer
# could give makes committing a real credential safe, and git history is
# permanent. Test/doc files are exempt (that's where example keys legitimately
# live). Distinct from _SECURITY_PATTERNS, which are heuristic and stay
# questions.
#
# The blocking line is deliberately narrow and defensible: "stealable as-is in
# the next 10 minutes" hard-blocks (vendor-pinned token formats, key material,
# creded connection strings, key FILES, high-entropy secret assignments).
# Judgment-call findings (weak crypto, CORS, Math.random tokens) stay as
# severity-weighted questions in _SECURITY_PATTERNS.
_CRITICAL_PATTERNS = [
    # --- Cloud / infra providers ---
    (r"AKIA[0-9A-Z]{16}", "aws-access-key", "AWS access key ID"),
    (r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----",
     "private-key", "Private key material"),
    (r"\bgsk_[A-Za-z0-9]{30,}\b", "groq-api-token", "Groq API token"),
    (r"\bsk-nous-[A-Za-z0-9_-]{20,}\b", "nous-api-token", "Nous Research API token"),
    (r"\bghp_[A-Za-z0-9]{30,}\b", "github-token", "GitHub access token"),
    (r"\bgho_[A-Za-z0-9]{30,}\b", "github-token", "GitHub access token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{60,}\b", "github-token", "GitHub fine-grained access token"),
    (r"\bsk-(?:proj-)?(?!ant-|or-|nous-)[A-Za-z0-9_-]{40,}\b", "openai-api-token", "OpenAI API token"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "slack-token", "Slack token"),
    (r"\bAIza[0-9A-Za-z_-]{35}\b", "google-api-key", "Google API key"),
    (r"\b0{8}[a-z0-9]{10}\b", "gcp-oauth", "GCP OAuth access token"),

    # --- AI / model providers ---
    # NOTE: must not swallow sk-ant- / sk-or- (checked before the generic sk- form)
    (r"\bsk-ant-[A-Za-z0-9_-]{20,}\b", "anthropic-api-token", "Anthropic API token"),
    (r"\bsk-or-[A-Za-z0-9-]{30,}\b", "openrouter-api-token", "OpenRouter API token"),
    (r"\bhf_[A-Za-z0-9]{30,}\b", "huggingface-token", "Hugging Face token"),

    # --- SaaS / payments ---
    (r"\bsk_live_[0-9a-zA-Z]{20,}\b", "stripe-secret-key", "Stripe live secret key"),
    (r"\brk_live_[0-9a-zA-Z]{20,}\b", "stripe-restricted-key", "Stripe live restricted key"),
    (r"\bwhsec_[A-Za-z0-9]{20,}\b", "stripe-webhook-secret", "Stripe webhook signing secret"),
    (r"\bSG\.[\w-]{22}\.[\w-]{43}\b", "sendgrid-api-key", "SendGrid API key"),
    (r"\bSK[0-9a-f]{32}\b", "twilio-api-key", "Twilio API key"),
    (r"\b\d{9,10}:[A-Za-z0-9_-]{35}\b", "telegram-bot-token", "Telegram bot token"),

    # --- Package registries ---
    (r"\bnpm_[A-Za-z0-9]{36}\b", "npm-token", "npm access token"),
    (r"\bglpat-[A-Za-z0-9_-]{20,}\b", "gitlab-token", "GitLab personal access token"),
    (r"\bpypi-A[A-Za-z0-9]{60,}vcmc\b", "pypi-upload-token", "PyPI upload token"),
]

# Connection strings WITH inline credentials → hard block. Bare
# scheme://host forms (no user:pass@) stay in the heuristic tier.
_CONNECTION_STRING_PATTERN = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|rediss|amqps?|ftp|mssql|oracle)"
    r"://[^\s@'\"]{1,64}:[^\s@'\"]{1,64}@", re.IGNORECASE)
_CONNECTION_LABEL = "creded-connection-string"
_CONNECTION_DESC = "Connection string with inline credentials"

# Generic scheme://user:pass@ basic-auth URL (any scheme) → hard block.
_BASIC_AUTH_URL_PATTERN = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]{1,20}://[^\s@'\":/]{1,64}:[^\s@'\"]{1,64}@")
_BASIC_AUTH_LABEL = "basic-auth-url"
_BASIC_AUTH_DESC = "URL with inline basic-auth credentials"

# Filenames whose very presence staged is a finding — contents are never read
# (privacy-cheap; the name alone is the signal). A real private key added as a
# file is the worst case the content scan can miss today.
_CRITICAL_FILE_EXTENSIONS = (".pem", ".p12", ".pfx", ".key", ".keystore", ".kdbx",
                             ".sqlcipher", ".jks", ".pfx")
_CRITICAL_FILE_NAMES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "id_gpg",
                        ".npmrc", ".netrc", ".kube/config", "credentials")
# Names that look like examples/keys, not real key material.
_FILE_EXEMPT_SUFFIXES = (".example", ".sample", ".template", ".tpl")

# Generic secret assignments gated on Shannon entropy (PLAN.md B2). Regex alone
# cannot block password="..." — fixtures, placeholders and doc examples all
# die. A high-entropy value bound to a secret-named key is a real credential
# with near-certainty; a low-entropy one is a placeholder and stays heuristic.
_GENERIC_SECRET_PATTERN = re.compile(
    r"""['\"]?\b(?:password|passwd|pwd|secret|secret_?key|api[_-]?key|apikey|"""
    r"""access_?key|access_?token|auth_?token|private_?key|client_?secret|"""
    r"""signing_?key|jwt_?secret|session_?key)\b['\"]?\s*[:=]\s*['\"]([^'\"]{20,})['\"]""",
    re.IGNORECASE)
_PLACEHOLDER_HINTS = ("example", "sample", "placeholder", "changeme", "change-me",
                      "xxx", "your", "dummy", "fake", "test", "todo", "fixme",
                      # redaction markers — no real credential contains these,
                      # so they exempt everywhere, not just tests/docs
                      "***", "…", "...", "[redacted]", "<", "»")
_SECRET_ESCAPE_COMMENTS = ("# pragma: allow-secret", "# example", "# allow-secret")

_ENTROPY_BITS_THRESHOLD = 3.5  # per char, over values ≥ 20 chars
_ENTROPY_CLASSES_MIN = 3       # of [lower, upper, digit, symbol]


def security_gate_enabled() -> bool:
    """Critical-finding gate is ON by default; UNDERSTANDING_AGENT_SECURITY_GATE=off disables."""
    return os.environ.get("UNDERSTANDING_AGENT_SECURITY_GATE", "").strip().lower() != "off"


class SecurityLens:
    """Deterministic security-relevance classifier for staged changes."""

    def find_critical_findings(self, context: dict) -> List[dict]:
        """Deterministic, catastrophic findings (stealable credentials) that block.

        Scans BOTH the per-function diffs in context AND the raw staged diff —
        credentials typically live at module level, outside any function, so the
        raw scan is the primary source; the function-level context scan adds
        function attribution where available. Also scans STAGED FILE NAMES —
        key material added as a file (id_rsa, *.pem) is a finding by presence,
        without reading its contents.

        Returns [{file, function, label, description, redacted}]. Empty list
        means the commit may proceed. Test paths are exempt from the generic
        entropy-gated patterns only (PLAN.md B5); exact vendor formats block
        everywhere — a real key is real in a test file too.
        """
        findings = []
        seen = set()

        def _record(path, func, label, description, secret):
            key = (path, label, secret)
            if key in seen:
                return
            seen.add(key)
            findings.append({
                "file": path,
                "function": func,
                "label": label,
                "description": description,
                "redacted": secret[:8] + "…" if len(secret) > 8 else "***",
            })

        # 0. Staged key FILES — the presence alone is the finding (PLAN.md B4)
        for path in self._staged_file_names():
            if self._is_critical_key_file(path):
                _record(path, "", "staged-key-file",
                         "Key/credential file staged for commit", "<filename>")

        # 1. Raw staged diff scan (module-level code, config files, everything)
        raw = self._raw_staged_added_lines()
        for path, added in raw.items():
            is_test = self._is_test_path(path)
            is_doc = self._is_doc_path(path)
            # Exact vendor formats block EVERYWHERE, including tests (PLAN.md B5):
            # a real leaked key is real in a test file — the classic org leak.
            for pattern, label, description in _CRITICAL_PATTERNS:
                for m in re.finditer(pattern, added):
                    _record(path, "", label, description, m.group(0))
            # Creded connection strings/basic-auth: exempt placeholder-looking
            # fixtures in test AND doc paths, block real-shaped ones.
            for m in re.finditer(_CONNECTION_STRING_PATTERN, added):
                if (is_test or is_doc) and self._looks_like_placeholder(m.group(0)):
                    continue
                _record(path, "", _CONNECTION_LABEL, _CONNECTION_DESC, m.group(0))
            for m in re.finditer(_BASIC_AUTH_URL_PATTERN, added):
                if (is_test or is_doc) and self._looks_like_placeholder(m.group(0)):
                    continue
                _record(path, "", _BASIC_AUTH_LABEL, _BASIC_AUTH_DESC, m.group(0))
            # Generic entropy-gated secrets: test paths exempt (fixtures live there)
            if not is_test:
                self._scan_generic_secrets(path, added, _record)

        # 2. Function-level context scan (adds function attribution)
        for change in context.get("structured_changes", []):
            path = change.get("file", "")
            is_test = self._is_test_path(path)
            is_doc = self._is_doc_path(path)
            diff = change.get("diff", "")
            if not diff:
                continue
            changed_lines = [l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
            added = "\n".join(changed_lines)
            # Vendor formats block everywhere (see raw scan note above).
            for pattern, label, description in _CRITICAL_PATTERNS:
                for m in re.finditer(pattern, added):
                    _record(path, change.get("function", ""), label, description, m.group(0))
            for m in re.finditer(_CONNECTION_STRING_PATTERN, added):
                if (is_test or is_doc) and self._looks_like_placeholder(m.group(0)):
                    continue
                _record(path, change.get("function", ""), _CONNECTION_LABEL,
                        _CONNECTION_DESC, m.group(0))
            for m in re.finditer(_BASIC_AUTH_URL_PATTERN, added):
                if (is_test or is_doc) and self._looks_like_placeholder(m.group(0)):
                    continue
                _record(path, change.get("function", ""), _BASIC_AUTH_LABEL,
                        _BASIC_AUTH_DESC, m.group(0))
            # Generic entropy-gated secrets: test paths exempt
            if not is_test:
                self._scan_generic_secrets(path, added, _record, func=change.get("function", ""))

        return findings

    @staticmethod
    def _is_test_path(path: str) -> bool:
        return any(h in path.lower() for h in _TEST_PATH_HINTS)

    @staticmethod
    def _is_doc_path(path: str) -> bool:
        """Doc paths AND example-named config files (config.example.yaml) —
        where redacted/placeholder-shaped examples legitimately live. Only
        the example-shaped tiers are exempt here; vendor formats still block.
        """
        p = path.lower()
        if any(h in p for h in _DOC_PATH_HINTS):
            return True
        # config.example.yaml / settings.sample.json — an example marker in
        # the filename stem, not necessarily at the end.
        stem = p.rsplit(".", 1)[0]
        return any(stem.endswith(s) for s in _EXAMPLE_FILE_SUFFIXES)

    def _staged_file_names(self) -> List[str]:
        """Return the staged file list via git (name-status). Never raises."""
        try:
            out = subprocess.check_output(
                ["git", "diff", "--cached", "--name-only"],
                text=True, stderr=subprocess.DEVNULL
            )
            return [l.strip() for l in out.splitlines() if l.strip()]
        except Exception:
            return []

    @staticmethod
    def _is_critical_key_file(path: str) -> bool:
        """A key/credential file staged for commit is a finding by name alone.

        Example-style names (id_rsa.example, ca.pem.template) are exempt —
        that's exactly where sample material legitimately lives.
        """
        name = os.path.basename(path).lower()
        if any(name.endswith(s) for s in _FILE_EXEMPT_SUFFIXES):
            return False
        if any(name == n or name == n + "_key" for n in _CRITICAL_FILE_NAMES):
            return True
        if name in (".kube/config",):
            return True
        if any(name.endswith(ext) for ext in _CRITICAL_FILE_EXTENSIONS):
            return True
        return False

    @staticmethod
    def _looks_like_placeholder(value: str) -> bool:
        v = value.lower()
        return any(h in v for h in _PLACEHOLDER_HINTS)

    @staticmethod
    def _shannon_entropy(value: str) -> float:
        if not value:
            return 0.0
        freq = {}
        for ch in value:
            freq[ch] = freq.get(ch, 0) + 1
        length = len(value)
        return -sum((c / length) * math.log2(c / length) for c in freq.values())

    @staticmethod
    def _char_classes(value: str) -> int:
        classes = 0
        if any(c.islower() for c in value):
            classes += 1
        if any(c.isupper() for c in value):
            classes += 1
        if any(c.isdigit() for c in value):
            classes += 1
        if any(not c.isalnum() for c in value):
            classes += 1
        return classes

    def _scan_generic_secrets(self, path: str, added_lines: str, _record, func: str = ""):
        """Entropy-gated generic secret detection (PLAN.md B2).

        Only blocks values that look like real credentials: ≥20 chars, entropy
        above threshold, ≥3 character classes, no placeholder hints. Escape
        hatches: a `# pragma: allow-secret` / `# example` trailing comment, and
        test-path exemption (handled by the caller for the generic tier).
        Low-entropy matches are left to the heuristic question tier.
        """
        for m in re.finditer(_GENERIC_SECRET_PATTERN, added_lines):
            value = m.group(1)
            line_start = added_lines.rfind("\n", 0, m.start()) + 1
            line_end = added_lines.find("\n", m.start())
            if line_end == -1:
                line_end = len(added_lines)
            line = added_lines[line_start:line_end]
            # Escape hatch 1: explicit inline suppression comment
            if any(esc in line for esc in _SECRET_ESCAPE_COMMENTS):
                continue
            # Escape hatch 2: clearly placeholder-looking values stay heuristic
            if self._looks_like_placeholder(value):
                continue
            if len(value) < 20:
                continue
            if self._shannon_entropy(value) < _ENTROPY_BITS_THRESHOLD:
                continue
            if self._char_classes(value) < _ENTROPY_CLASSES_MIN:
                continue
            _record(path, func, "high-entropy-secret",
                    "High-entropy value assigned to a secret-named variable", value)

    def _raw_staged_added_lines(self) -> Dict[str, str]:
        """Return {path: added-lines-text} from the raw staged diff.

        Covers ALL staged files (module-level code, configs, dotfiles) — not
        just those with detected functions.
        """
        try:
            diff = subprocess.check_output(
                ["git", "diff", "--cached", "-U0"],
                text=True, stderr=subprocess.DEVNULL
            )
        except Exception:
            return {}

        result: Dict[str, str] = {}
        current_path = None
        current_lines: List[str] = []
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                if current_path and current_lines:
                    result[current_path] = "\n".join(current_lines)
                current_path = line[6:]
                current_lines = []
            elif line.startswith("+") and not line.startswith("+++"):
                current_lines.append(line[1:])
            elif line.startswith("---"):
                continue
        if current_path and current_lines:
            result[current_path] = "\n".join(current_lines)
        return result

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
