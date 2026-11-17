"""Language registry and function extraction for languages without a Python AST.

Python uses the native `ast` module (fast path). For JavaScript/TypeScript, C#,
Go, Rust, Java, Kotlin, Ruby, PHP, and C/C++ we use line-anchored definition
patterns with a keyword blacklist — precise enough to target questions at the
right function, without pulling in tree-sitter as a hard dependency.

Function spans use a "next definition wins" heuristic: a function's span runs
from its definition line to the line before the next definition (or +400 lines
cap). For question targeting this is accurate enough; it is not a compiler.
"""

import re
from typing import List, Dict, Any, Set


EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript",
    ".cs": "csharp",
    ".fs": "fsharp",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".c": "c", ".h": "c",
    ".swift": "swift",
    # Data science / docs / config formats (content-aware, not function-level)
    ".sql": "sql", ".ipynb": "notebook",
}


def language_for(path: str) -> str:
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return EXTENSION_TO_LANGUAGE.get(ext, "")


# Statement keywords that must never be treated as function names
_KEYWORD_BLACKLIST = {
    "if", "else", "for", "foreach", "while", "switch", "case", "catch", "finally",
    "return", "throw", "new", "typeof", "sizeof", "await", "yield", "lock", "using",
    "fixed", "checked", "unchecked", "do", "try", "match", "when", "with", "in",
    "of", "and", "or", "not", "assert", "print", "require", "import", "from",
    "describe", "it", "test", "expect",
}

_ACCESS_MODIFIERS = {
    "public", "private", "protected", "internal", "static", "sealed", "override",
    "virtual", "async", "partial", "extern", "new", "readonly", "final",
    "abstract", "synchronized", "native", "open", "suspend", "inline", "operator",
    "infix", "tailrec", "const", "unsafe", "get", "set", "mut",
}


# Per-language definition patterns: (name_group, pattern)
_DEFINITION_PATTERNS = {
    "python": [
        r"^\s*(?:async\s+)?def\s+(\w+)\s*\(",
        r"^\s*class\s+(\w+)",
    ],
    "javascript": [
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*\(",
        r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>)",
        r"^\s+(?:(?:public|private|protected|readonly|static|abstract|override|async|get|set)\s+)+(\w+)\s*\(",
    ],
    "typescript": [
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*\(",
        r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>)",
        r"^\s+(?:(?:public|private|protected|readonly|static|abstract|override|async|get|set)\s+)+(\w+)\s*(?:<[^>]+>)?\s*\(",
    ],
    "csharp": [
        r"^\s*(?:(?:public|private|protected|internal|static|sealed|override|virtual|async|partial|extern|new)\s+)+(?:[\w<>\[\],\s\.\?]+?\s+)?(\w+)\s*(?:<[^>]+>)?\s*\(",
    ],
    "go": [
        r"^func\s+(?:\([^)]+\)\s*)?(\w+)\s*\(",
    ],
    "rust": [
        r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:extern(?:\s+\"[^\"]*\")?\s+)?fn\s+(\w+)",
    ],
    "java": [
        r"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized|default|native)\s+)+(?:[\w<>\[\],\s\.\?]+?\s+)?(\w+)\s*(?:<[^>]+>)?\s*\(",
    ],
    "kotlin": [
        r"^\s*(?:@\w[\w(\"',\.\s)]*\s*)*(?:(?:private|internal|public|protected|open|override|final|abstract|suspend|inline|operator|infix|tailrec)\s+)*fun\s+(?:<[^>]+>\s+)?(?:[\w\.]+\.)?(\w+)\s*\(",
    ],
    "ruby": [
        r"^\s*def\s+(?:self\.)?(\w+)",
    ],
    "php": [
        r"^\s*(?:(?:public|private|protected|static)\s+)*function\s+(\w+)\s*\(",
    ],
    "cpp": [
        r"^\s*[\w:<>,\s\*&]+\s+(\w+)\s*\([^;{}]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:->\s*[\w:<>\*&]+\s*)?\{",
        r"^\s*(?:[\w:<>,\s\*&]+\s+)?(\w+)\s*\([^;{}]*\)\s*(?:const)?\s*\{",
    ],
    "c": [
        r"^\s*[\w:<>,\s\*&]+\s+(\w+)\s*\([^;{}]*\)\s*\{",
    ],
    "swift": [
        r"^\s*(?:@\w+\s+)*(?:(?:public|private|internal|fileprivate|open|final|override|static|class)\s+)*(?:func)\s+(\w+)\s*\(",
    ],
    "fsharp": [
        r"^\s*(?:(?:\[<[^>]*>\])?\s*)?(?:let\s+(?:mutable\s+)?|member\s+(?:\w+\.)?)(\w+)",
    ],
}

_SPAN_CAP = 400


def extract_functions(source: str, language: str) -> List[Dict[str, Any]]:
    """Return [{name, start_line, end_line}] (1-indexed, inclusive) for the language.

    Heuristic spans: from a definition line up to (excluding) the next definition
    line, capped at _SPAN_CAP lines. Python callers should prefer the ast path.
    """
    if not source or language not in _DEFINITION_PATTERNS:
        return []

    compiled = [(re.compile(p),) for p in _DEFINITION_PATTERNS[language]]
    matches = []
    lines = source.splitlines()
    for idx, line in enumerate(lines):
        for (pat,) in compiled:
            m = pat.match(line)
            if not m:
                continue
            name = m.group(1)
            if name in _KEYWORD_BLACKLIST or name in _ACCESS_MODIFIERS:
                continue
            matches.append({"name": name, "start_line": idx + 1})
            break

    result = []
    for i, fn in enumerate(matches):
        if i + 1 < len(matches):
            end = matches[i + 1]["start_line"] - 1
        else:
            end = len(lines)
        fn["end_line"] = min(fn["start_line"] + _SPAN_CAP, end, len(lines))
        if fn["end_line"] < fn["start_line"]:
            fn["end_line"] = fn["start_line"]
        result.append(fn)
    return result


def functions_touching_lines(
    source: str, language: str, changed_lines: Set[int]
) -> List[Dict[str, Any]]:
    """Return function dicts (name, change_type, added_calls) overlapping changed lines."""
    if not source or not changed_lines:
        return []
    touched = []
    for fn in extract_functions(source, language):
        span = set(range(fn["start_line"], fn["end_line"] + 1))
        if span & changed_lines:
            touched.append({
                "name": fn["name"],
                "change_type": "modified",
                "added_calls": extract_calls(source, language, fn),
            })
    return touched


def extract_calls(source: str, language: str, fn: Dict[str, Any]) -> List[str]:
    """Best-effort call extraction within a function span (non-Python languages).

    Deliberately conservative: used only to enrich dependency summaries; misses
    are harmless, noise is filtered by the graph's definition resolution.
    """
    lines = source.splitlines()
    body = lines[fn["start_line"] - 1: fn["end_line"]]
    calls = []
    seen = set()
    for line in body:
        stripped = line.strip()
        if stripped.startswith(("//", "*", "#", "/*", "*")):
            continue
        for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", line):
            name = m.group(1)
            if name in _KEYWORD_BLACKLIST or name in _ACCESS_MODIFIERS or name == fn["name"]:
                continue
            if name not in seen:
                seen.add(name)
                calls.append(name)
        if len(calls) >= 12:
            break
    return calls
