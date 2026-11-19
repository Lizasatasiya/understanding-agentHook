# Context package: change detection, stack analysis, language AST, and context building.
# Summary and master analysis live in the service. The hook only builds the diff context.
from .change_detector import ChangeDetector
from .change_kind import classify_change
from .context_builder import ContextBuilder
from .code_graph import CodeGraph
from .commit_context import find_git_commit_ancestor
from .environment_detector import EnvironmentDetector
from .evidence_collector import collect_evidence, evidence_question_hint
from .formatting_detector import is_commit_formatting_only, is_formatting_change
from .language_support import extract_functions, language_for
from .stack_detector import StackDetector

__all__ = [
    "ChangeDetector",
    "classify_change",
    "ContextBuilder",
    "CodeGraph",
    "find_git_commit_ancestor",
    "EnvironmentDetector",
    "collect_evidence",
    "evidence_question_hint",
    "is_commit_formatting_only",
    "is_formatting_change",
    "extract_functions",
    "language_for",
    "StackDetector",
]
