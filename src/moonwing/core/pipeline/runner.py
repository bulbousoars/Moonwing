"""Pipeline + Stage primitives."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Protocol


logger = logging.getLogger("moonwing.core.pipeline")


class StageOutcome(StrEnum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


class PipelineError(RuntimeError):
    """Raised when a non-skippable stage fails and halts the pipeline."""

    def __init__(self, stage_name: str, cause: BaseException | str) -> None:
        super().__init__(f"stage {stage_name!r} failed: {cause}")
        self.stage_name = stage_name
        self.cause = cause


class StageSkipped(Exception):
    """Stages raise this to opt-out cleanly (e.g. input kind mismatch)."""


@dataclass
class StageContext:
    """Per-pipeline shared state.

    ``data`` accumulates stage outputs by name. ``extras`` is the escape
    hatch for one-off dependencies stages need (DB session, LLM adapter,
    tool registry) without forcing them all onto the typed surface.
    """

    run_id: Any  # UUID, loose to avoid import cycles
    workdir: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)
    on_event: Callable[[str, dict[str, Any]], None] | None = None

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:  # pragma: no cover — defensive
                logger.exception("pipeline event hook crashed")

    # convenience: get/put typed accessors
    def put(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass
class StageResult:
    name: str
    outcome: StageOutcome
    output: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == StageOutcome.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "outcome": self.outcome.value,
            "output_summary": _summarize(self.output),
            "error": self.error,
        }


def _summarize(value: Any) -> Any:
    """Reduce a stage's output into something safe to drop into the snapshot."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return {"_kind": "list", "len": len(value)}
    if isinstance(value, dict):
        return {"_kind": "dict", "keys": sorted(value.keys())[:20]}
    return {"_kind": type(value).__name__}


class Stage(Protocol):
    """Concrete pipeline stage.

    Implementations are typically small classes with a ``name`` attribute
    and a ``run`` method. Raising ``StageSkipped`` short-circuits to a
    skipped result without halting the pipeline; any other exception is
    captured and wrapped as a failed result.
    """

    name: str

    def run(self, ctx: StageContext) -> Any:
        ...


@dataclass
class PipelineResult:
    stage_results: list[StageResult]
    ok: bool
    halted_at: str | None = None  # name of stage that failed, if any
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "halted_at": self.halted_at,
            "error": self.error,
            "stages": [r.to_dict() for r in self.stage_results],
        }


class Pipeline:
    def __init__(self, stages: list[Stage], *, name: str = "pipeline") -> None:
        if not stages:
            raise ValueError("pipeline requires at least one stage")
        self._stages = list(stages)
        self.name = name

    def run(
        self,
        ctx: StageContext,
        *,
        halt_on_failure: bool = True,
    ) -> PipelineResult:
        """Execute stages in order. Returns a ``PipelineResult``.

        ``halt_on_failure``: when True (default), the first failed stage
        ends the run and subsequent stages are not executed. When False,
        every stage is attempted and failures are recorded individually —
        useful for diagnostic pipelines.
        """
        ctx.emit("pipeline_start", {"name": self.name, "stages": [s.name for s in self._stages]})
        results: list[StageResult] = []
        halted_at: str | None = None
        error: str | None = None

        for stage in self._stages:
            ctx.emit("stage_start", {"name": stage.name})
            try:
                output = stage.run(ctx)
            except StageSkipped as exc:
                result = StageResult(
                    name=stage.name, outcome=StageOutcome.SKIPPED, error=str(exc) or None
                )
                results.append(result)
                ctx.emit("stage_end", result.to_dict())
                continue
            except Exception as exc:
                logger.exception("stage %s crashed", stage.name)
                result = StageResult(
                    name=stage.name,
                    outcome=StageOutcome.FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                )
                results.append(result)
                ctx.emit("stage_end", result.to_dict())
                if halt_on_failure:
                    halted_at = stage.name
                    error = result.error
                    break
                continue

            result = StageResult(
                name=stage.name, outcome=StageOutcome.SUCCESS, output=output
            )
            ctx.put(stage.name, output)
            results.append(result)
            ctx.emit("stage_end", result.to_dict())

        ok = all(r.outcome != StageOutcome.FAILED for r in results)
        ctx.emit(
            "pipeline_end",
            {"ok": ok, "halted_at": halted_at, "stage_count": len(results)},
        )
        return PipelineResult(
            stage_results=results, ok=ok, halted_at=halted_at, error=error
        )
