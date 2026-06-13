"""Drive the staged source-hunt pipeline from the worker.

Mirrors ``react_executor.execute_network_react`` — returns a normalize-able
payload so the existing downstream finding-persistence + KG hook from
Phase 0.2 work unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from moonwing.core.llm import get_adapter
from moonwing.core.pipeline import StageContext, StageOutcome
from moonwing.core.react import Budget
from moonwing.core.source_hunt import build_source_hunt_pipeline

logger = logging.getLogger("moonwing.worker.source_hunt_executor")


@dataclass
class SourceHuntExecutionResult:
    raw_payload: dict
    provider: str
    model: str
    usage: dict | None = None
    pipeline_summary: dict | None = None


def _budget_from_profile(profile_settings: dict[str, Any]) -> Budget:
    agentic = profile_settings.get("agentic") or {}
    return Budget(
        max_iterations=int(agentic.get("hunter_max_iterations") or 8),
        max_total_input_tokens=agentic.get("hunter_max_input_tokens") or 120_000,
        max_wall_seconds=agentic.get("hunter_max_wall_seconds") or 180.0,
    )


def execute_source_hunt_pipeline(
    *,
    session: Session,
    run,  # moonwing.db.models.Run — typed-loose to avoid import cycle
    workdir: str,
    source_ref: str,
    input_kind: str,
    api_key: str,
    profile_settings: dict[str, Any] | None = None,
    on_stage_event: Callable[[str, dict[str, Any]], None] | None = None,
    on_hunter_event: Callable[[str, dict[str, Any]], None] | None = None,
    on_hunter_note: Callable[[str], None] | None = None,
    ai_instruction: str | None = None,
    timeout_seconds: int = 1200,
) -> SourceHuntExecutionResult:
    """Run the source-hunt pipeline against ``source_ref`` and return a payload."""
    profile_settings = profile_settings or {}
    adapter = get_adapter(run.provider)

    agentic = profile_settings.get("agentic") or {}
    top_n = int(agentic.get("top_n") or 12)
    hunter_max_files = agentic.get("hunter_max_files")

    extras: dict[str, Any] = {
        "input_kind": input_kind,
        "source_ref": source_ref,
        "llm_adapter": adapter,
        "llm_model": run.model,
        "llm_api_key": api_key,
        "top_n": top_n,
        "hunter_budget": _budget_from_profile(profile_settings),
        "db_session": session,
        "llm_timeout": min(timeout_seconds, 300),
        "hunter_call_timeout": min(timeout_seconds, 300),
    }
    if hunter_max_files is not None:
        extras["hunter_max_files"] = int(hunter_max_files)
    if on_hunter_event is not None:
        extras["on_hunter_event"] = on_hunter_event
    if on_hunter_note is not None:
        extras["on_hunter_note"] = on_hunter_note
    if ai_instruction:
        # Carried in extras for future use; the rank/hunt prompts don't
        # consume it today but the worker captures it for replay.
        extras["operator_ai_instruction"] = ai_instruction

    ctx = StageContext(
        run_id=run.id,
        workdir=workdir,
        extras=extras,
        on_event=on_stage_event,
    )

    pipeline = build_source_hunt_pipeline()
    pipeline_result = pipeline.run(ctx)

    hunt_output = ctx.get("hunt")
    if hunt_output is not None:
        merged = list(hunt_output.merged_findings or [])
    else:
        merged = []

    raw_payload = {"findings": merged}

    rank_output = ctx.get("rank")
    inventory_output = ctx.get("inventory")
    usage: dict[str, Any] = {
        "pipeline_ok": pipeline_result.ok,
        "halted_at": pipeline_result.halted_at,
        "stages": [r.to_dict() for r in pipeline_result.stage_results],
        "ranked_count": len(rank_output.ranked) if rank_output else 0,
        "inventory_files": len(inventory_output.files) if inventory_output else 0,
        "hunter_iterations": hunt_output.total_iterations if hunt_output else 0,
        "merged_findings": len(merged),
    }
    if pipeline_result.error:
        usage["error"] = pipeline_result.error

    pipeline_summary = {
        "stage_results": [r.to_dict() for r in pipeline_result.stage_results],
        "per_file": [
            {
                "path": p.path,
                "concern": p.concern,
                "findings": len(p.findings),
                "stop_reason": p.stop_reason,
                "iterations": p.iterations,
                "error": p.error,
            }
            for p in (hunt_output.per_file if hunt_output else [])
        ],
    }

    return SourceHuntExecutionResult(
        raw_payload=raw_payload,
        provider=run.provider,
        model=run.model,
        usage=usage,
        pipeline_summary=pipeline_summary,
    )
