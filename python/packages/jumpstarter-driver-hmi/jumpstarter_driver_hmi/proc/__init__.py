"""For a source that runs a child process: a command queue that cannot block its callers, and a supervised child."""

from .process import Backoff, ManagedProcess, StderrTail, pdeathsig_preexec
from .writer import COALESCE_KEYFRAME, COALESCE_MOVE, CommandWriter

__all__ = [
    "COALESCE_KEYFRAME",
    "COALESCE_MOVE",
    "Backoff",
    "CommandWriter",
    "ManagedProcess",
    "StderrTail",
    "pdeathsig_preexec",
]
