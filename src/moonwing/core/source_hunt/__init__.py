"""Staged source-hunt pipeline — agentic vulnerability discovery in source.

Pipeline stages:
  1. acquire — clone repo / validate path / extract sbom
  2. inventory — walk tree, gather files, basic stats
  3. rank — LLM picks the top-N most security-relevant files
  4. hunt — per-file ReAct loop with read_file/grep tools, emits findings
"""

from __future__ import annotations

from .acquire import AcquireStage, SourceAcquisitionError
from .hunt import HuntStage, HuntResult, FileHuntResult
from .inventory import InventoryStage
from .pipeline import build_source_hunt_pipeline
from .rank import RankStage, RankedFile, RankResult

__all__ = [
    "AcquireStage",
    "FileHuntResult",
    "HuntResult",
    "HuntStage",
    "InventoryStage",
    "RankResult",
    "RankStage",
    "RankedFile",
    "SourceAcquisitionError",
    "build_source_hunt_pipeline",
]
