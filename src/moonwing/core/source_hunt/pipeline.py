"""Top-level source-hunt pipeline assembler."""

from __future__ import annotations

from moonwing.core.pipeline import Pipeline

from .acquire import AcquireStage
from .inventory import InventoryStage
from .rank import RankStage
from .hunt import HuntStage


def build_source_hunt_pipeline() -> Pipeline:
    """Return the default agentic source-hunt pipeline.

    Stages, in order:
      1. acquire    — clone repo or validate path
      2. inventory  — walk + classify files
      3. rank       — LLM picks top-N security-relevant files
      4. hunt       — per-file ReAct hunter, merged findings
    """
    return Pipeline(
        stages=[
            AcquireStage(),
            InventoryStage(),
            RankStage(),
            HuntStage(),
        ],
        name="source_hunt",
    )
