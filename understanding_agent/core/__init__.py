# Core package: CLI defense loop, hook stages, push guard, and identity detection
from .cli import main
from .hook_stages import is_already_verified_for_commit
from .push_guard import main as push_guard_main
from .identity import IdentityDetector

__all__ = [
    "main",
    "is_already_verified_for_commit",
    "push_guard_main",
    "IdentityDetector",
]
