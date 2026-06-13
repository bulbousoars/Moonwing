"""Multi-stage pipeline primitives for source-hunt and campaign workflows.

A pipeline is an ordered list of ``Stage`` instances driven by ``Pipeline.run``.
Stages share a ``StageContext`` that carries accumulated data and per-step
hooks. The framework is pure-Python — DB writes, prompt construction, and
LLM calls all live in concrete stages so tests can stub them.
"""

from __future__ import annotations

from .runner import (
    Pipeline,
    PipelineError,
    PipelineResult,
    Stage,
    StageContext,
    StageOutcome,
    StageResult,
    StageSkipped,
)

__all__ = [
    "Pipeline",
    "PipelineError",
    "PipelineResult",
    "Stage",
    "StageContext",
    "StageOutcome",
    "StageResult",
    "StageSkipped",
]
