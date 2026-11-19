# Code Understanding Demo Agent
import sys

# Subpackages
from . import answers
from . import context
from . import core
from . import evaluation
from . import llm
from . import questions
from . import storage
from . import ui

from .context import (
    change_detector,
    change_kind,
    change_summary,
    change_intelligence,
    context_builder,
    code_graph,
    commit_context,
    environment_detector,
    evidence_collector,
    formatting_detector,
    language_support,
    stack_detector,
)
from .questions import (
    question_generator,
    followup_generator,
    practice_packs,
    checklists,
)
from .answers import (
    interaction,
    answer_hygiene,
)
from .evaluation import (
    answer_evaluator,
    evaluation_models,
    coding_standards,
    security_lens,
)
from .core import (
    cli,
    hook_stages,
    push_guard,
    identity,
)
from .llm import (
    llm_manager,
    api_utils,
)
try:
    from .ui import server
except Exception:
    server = None

# Backward-compatibility: expose moved modules under understanding_agent.<module> in sys.modules
_aliases = {
    # Context
    "change_detector": change_detector,
    "change_kind": change_kind,
    "change_summary": change_summary,
    "change_intelligence": change_intelligence,
    "context_builder": context_builder,
    "code_graph": code_graph,
    "commit_context": commit_context,
    "environment_detector": environment_detector,
    "evidence_collector": evidence_collector,
    "formatting_detector": formatting_detector,
    "language_support": language_support,
    "stack_detector": stack_detector,
    # Questions
    "question_generator": question_generator,
    "followup_generator": followup_generator,
    "practice_packs": practice_packs,
    "checklists": checklists,
    # Answers
    "interaction": interaction,
    "answer_hygiene": answer_hygiene,
    # Evaluation
    "answer_evaluator": answer_evaluator,
    "evaluation_models": evaluation_models,
    "coding_standards": coding_standards,
    "security_lens": security_lens,
    # Core
    "cli": cli,
    "hook_stages": hook_stages,
    "push_guard": push_guard,
    "identity": identity,
    # LLM
    "llm_manager": llm_manager,
    "api_utils": api_utils,
    # UI
    "server": server,
}

for _name, _mod in _aliases.items():
    if _mod is not None:
        sys.modules.setdefault(f"understanding_agent.{_name}", _mod)
