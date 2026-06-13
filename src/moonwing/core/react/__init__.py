"""ReAct loop — the autonomous reasoning engine for agentic scans.

Drives an LLM through reason → tool-call → tool-result iterations using
the Phase 0 ``LLMAdapter`` and ``ToolRegistry`` seams. The agent is
DB-agnostic; the worker layer owns persistence (findings, KG, activity).
"""

from __future__ import annotations

from .budget import Budget, BudgetExceededError, BudgetState
from .loop import (
    ReActAgent,
    ReActStep,
    ReActStopReason,
    ReActToolInvocation,
    ReActTranscript,
)

__all__ = [
    "Budget",
    "BudgetExceededError",
    "BudgetState",
    "ReActAgent",
    "ReActStep",
    "ReActStopReason",
    "ReActToolInvocation",
    "ReActTranscript",
]
