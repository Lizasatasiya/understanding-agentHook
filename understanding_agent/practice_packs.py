"""Domain practice packs: best-practice question guidance per stack and domain.

Each pack converts a detected stack into concrete question TOPICS with
expected_concepts the evaluator grades against. Packs are declarative —
they shape the prompt, they never hard-block a commit.

Research grounding: best practices split into (a) mechanical rules linters
already cover (deliberately excluded), and (b) semantic/behavioral practices
only a human reviewer — or an oral defense — can verify (what these packs
encode). Source domains: OWASP ASVS/cheat sheets for security, React/Nest
official docs for framework invariants, and ML reproducibility literature
(papers with code norms, mlflow/dvc conventions) for data/ML.
"""

from typing import Dict, Any


# ---- Web frontend ----
_REACT_TOPICS = [
    {
        "topic": "state management and effect dependencies",
        "prompt_hint": "Ask about the state the component adds/changes and why that owner, or the dependency array of an effect",
        "expected_concepts": ["state co-location", "effect cleanup", "stale closure avoidance", "re-render triggers"],
    },
    {
        "topic": "list keys and rendering user data",
        "prompt_hint": "Ask what happens when the list data changes or how user-provided strings are rendered",
        "expected_concepts": ["stable keys", "no array index keys on reorders", "output encoding"],
    },
    {
        "topic": "derived data and memoization boundaries",
        "prompt_hint": "Ask when a computation re-runs and whether that matters",
        "expected_concepts": ["deriving during render", "when to memoize", "referential equality of props"],
    },
]

_ANGULAR_VUE_TOPICS = [
    {
        "topic": "reactive state flow",
        "prompt_hint": "Ask how the changed component reacts to input/prop changes",
        "expected_concepts": ["unidirectional data flow", "change detection", "input immutability"],
    },
]

# ---- Web backend ----
_NESTJS_TOPICS = [
    {
        "topic": "validation at the trust boundary (DTO/pipe)",
        "prompt_hint": "Ask what happens when the request body is malformed or adversarial",
        "expected_concepts": ["DTO validation", "class-validator/global pipes", "never trusting client input"],
    },
    {
        "topic": "async error propagation and exception filters",
        "prompt_hint": "Ask what the client sees when the service throws",
        "expected_concepts": ["exception filters", "error status mapping", "no internal detail leakage"],
    },
    {
        "topic": "dependency injection boundaries",
        "prompt_hint": "Ask why the dependency is injected and what it costs to test",
        "expected_concepts": ["inversion of control", "testability via injection tokens", "request scoping"],
    },
]

_EXPRESS_TOPICS = [
    {
        "topic": "middleware ordering and error handling",
        "prompt_hint": "Ask what happens to an error thrown after the response starts",
        "expected_concepts": ["error middleware arity", "async handler wrapping", "status code choice"],
    },
    {
        "topic": "request validation",
        "prompt_hint": "Ask where untrusted input is checked before use",
        "expected_concepts": ["schema validation at the edge", "type coercion pitfalls"],
    },
]

_FASTAPI_DJANGO_FLASK_TOPICS = [
    {
        "topic": "request schema validation",
        "prompt_hint": "Ask what happens with an out-of-range or malformed payload",
        "expected_concepts": ["pydantic/serializer validation", "4xx on bad input", "default handling"],
    },
    {
        "topic": "query construction and ORM pitfalls",
        "prompt_hint": "Ask about the query the change adds and its performance/security characteristics",
        "expected_concepts": ["parameterized queries", "N+1 awareness", "select_related/joinedload"],
    },
]

# ---- .NET / C# ----
DOTNET_TOPICS = [
    {
        "topic": "model validation at the controller boundary",
        "prompt_hint": "Ask what happens when the bound model fails validation",
        "expected_concepts": ["data annotations/FluentValidation", "ModelState checks", "400 vs 422 semantics"],
    },
    {
        "topic": "async/await correctness and cancellation",
        "prompt_hint": "Ask about the async path: is ConfigureAwait needed, is CancellationToken honored, any .Result/.Wait()",
        "expected_concepts": ["no sync-over-async", "CancellationToken propagation", "async all the way"],
    },
    {
        "topic": "EF Core query behavior",
        "prompt_hint": "Ask whether the query executes client-side or server-side and about tracking",
        "expected_concepts": ["IQueryable vs IEnumerable", "AsNoTracking for reads", "parameterized LINQ"],
    },
    {
        "topic": "dependency injection and service lifetimes",
        "prompt_hint": "Ask the service lifetime registered and what breaks if it's wrong",
        "expected_concepts": ["transient/scoped/singleton", "captive dependencies", "scope validation"],
    },
    {
        "topic": "nullability and exception strategy",
        "prompt_hint": "Ask where nulls come from and what the top-level exception handler does",
        "expected_concepts": ["nullable reference types", "guard clauses", "global exception middleware"],
    },
]

# ---- Data / ML ----
_ML_DATA_TOPICS = [
    {
        "topic": "train/test data separation",
        "prompt_hint": "Ask how the change keeps test data out of training, or how splits are made",
        "expected_concepts": ["no leakage across split boundary", "grouped/time-based splits", "fit on train only"],
    },
    {
        "topic": "reproducibility (seeds, versions, artifacts)",
        "prompt_hint": "Ask what happens if this code re-runs tomorrow: same results?",
        "expected_concepts": ["random seeds set", "data/model versioning", "deterministic preprocessing"],
    },
    {
        "topic": "schema and null handling at ingestion",
        "prompt_hint": "Ask what happens when a column is missing, null, or a new category appears",
        "expected_concepts": ["schema validation", "null strategy explicit", "unknown-category handling"],
    },
    {
        "topic": "feature/target construction correctness",
        "prompt_hint": "Ask whether any feature uses information unavailable at prediction time",
        "expected_concepts": ["no target leakage into features", "temporal validity of features"],
    },
]

_DATA_PIPELINE_TOPICS = [
    {
        "topic": "idempotency and partial-failure recovery",
        "prompt_hint": "Ask what happens if the job dies halfway and re-runs",
        "expected_concepts": ["idempotent writes", "checkpointing", "at-least-once semantics"],
    },
    {
        "topic": "data volume and memory characteristics",
        "prompt_hint": "Ask how this behaves at 10x or 1000x the current data size",
        "expected_concepts": ["streaming over full materialization", "partition awareness"],
    },
]

# ---- Database / SQL ----
_SQL_TOPICS = [
    {
        "topic": "migration safety and reversibility",
        "prompt_hint": "Ask how to roll this migration back and whether it locks the table",
        "expected_concepts": ["reversible migrations", "locking awareness", "backfill strategy"],
    },
    {
        "topic": "query parameterization",
        "prompt_hint": "Ask how the query handles a value containing quotes or hostile input",
        "expected_concepts": ["bound parameters", "no string-built SQL"],
    },
]

# ---- Universal (any change) ----
_UNIVERSAL_TOPICS = [
    {
        "topic": "error handling at the boundary",
        "prompt_hint": "Ask what the caller sees when this fails",
        "expected_concepts": ["explicit failure mode", "no silent swallow", "error context preservation"],
    },
    {
        "topic": "impact radius of the change",
        "prompt_hint": "Ask who else calls this and what breaks if the contract changes",
        "expected_concepts": ["callers identified", "backward compatibility", "deprecation path"],
    },
]


_FRAMEWORK_TO_PACK = {
    "react": _REACT_TOPICS,
    "next.js": _REACT_TOPICS,
    "vue": _ANGULAR_VUE_TOPICS,
    "svelte": _ANGULAR_VUE_TOPICS,
    "angular": _ANGULAR_VUE_TOPICS,
    "nestjs": _NESTJS_TOPICS,
}


def get_practice_topics(stack: dict, context: dict) -> list:
    """Return a merged, deduplicated list of practice topic dicts for this change."""
    topics: list = []
    seen: set = set()

    def add(topic_list):
        for t in topic_list:
            if t["topic"] not in seen:
                seen.add(t["topic"])
                topics.append(t)

    frameworks = set(stack.get("frameworks", []))
    domains = set(stack.get("domains", []))
    languages = set(stack.get("languages", []))

    # Framework-specific packs
    for fw, pack in _FRAMEWORK_TO_PACK.items():
        if fw in frameworks:
            add(pack)

    if "express" in frameworks or ("javascript" in languages and "web-backend" in domains and not frameworks):
        add(_EXPRESS_TOPICS)
    if frameworks & {"fastapi", "django", "flask"}:
        add(_FASTAPI_DJANGO_FLASK_TOPICS)
    if stack.get("is_dotnet") or "csharp" in languages:
        add(DOTNET_TOPICS)
    if "sql" in languages or frameworks & {"entity-framework-core", "dapper", "sqlalchemy"}:
        add(_SQL_TOPICS)

    # Domain packs
    if "ml-data" in domains:
        add(_ML_DATA_TOPICS)
    if "data-pipeline" in domains:
        add(_DATA_PIPELINE_TOPICS)

    # Universal topics always available
    add(_UNIVERSAL_TOPICS)

    return topics


def practice_pack_hint(stack: dict, context: dict, security_hint: dict | None = None) -> Dict[str, Any]:
    """Build question-generator guidance from the detected stack + security lens."""
    topics = get_practice_topics(stack, context)

    # Security-relevant changes always include their security topic first
    if security_hint and security_hint.get("include_security_question"):
        topics.insert(0, {
            "topic": f"security: {', '.join(security_hint.get('focus_areas', [])[:3])}",
            "prompt_hint": f"Ask about {', '.join(security_hint.get('ask_about', ['the security implications'])[:3])} in {security_hint.get('top_file', 'the changed code')}",
            "expected_concepts": security_hint.get("focus_areas", []),
        })

    return {
        "practice_topics": [t["topic"] for t in topics[:8]],
        "prompt_hints": [t["prompt_hint"] for t in topics[:8]],
        "expected_concepts": [t["expected_concepts"] for t in topics[:8]],
    }
