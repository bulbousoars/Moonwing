from __future__ import annotations

from dataclasses import dataclass

import pytest

from moonwing.core.pipeline import (
    Pipeline,
    StageContext,
    StageOutcome,
    StageSkipped,
)


@dataclass
class _Stage:
    name: str
    output: object = None
    raises: Exception | None = None

    def run(self, ctx: StageContext):
        if self.raises is not None:
            raise self.raises
        return self.output


# --------------------------- happy paths -------------------------------


def test_pipeline_runs_stages_in_order_and_collects_outputs():
    stages = [_Stage("a", output={"hits": 3}), _Stage("b", output=[1, 2, 3])]
    ctx = StageContext(run_id=None)
    result = Pipeline(stages, name="hunt").run(ctx)

    assert result.ok is True
    assert [r.name for r in result.stage_results] == ["a", "b"]
    assert all(r.outcome == StageOutcome.SUCCESS for r in result.stage_results)
    assert ctx.get("a") == {"hits": 3}
    assert ctx.get("b") == [1, 2, 3]


def test_stage_can_read_prior_stage_output_via_context():
    class _ReadingStage:
        name = "reader"

        def run(self, ctx):
            return ctx.get("producer") * 2

    pipeline = Pipeline([_Stage("producer", output=5), _ReadingStage()])
    ctx = StageContext(run_id=None)
    result = pipeline.run(ctx)
    assert result.ok
    assert ctx.get("reader") == 10


# --------------------------- skipping ----------------------------------


def test_stage_skipped_does_not_halt_pipeline():
    stages = [
        _Stage("a", output=1),
        _Stage("b", raises=StageSkipped("not applicable")),
        _Stage("c", output=3),
    ]
    ctx = StageContext(run_id=None)
    result = Pipeline(stages).run(ctx)

    assert result.ok
    outcomes = {r.name: r.outcome for r in result.stage_results}
    assert outcomes == {
        "a": StageOutcome.SUCCESS,
        "b": StageOutcome.SKIPPED,
        "c": StageOutcome.SUCCESS,
    }


# --------------------------- failure semantics -------------------------


def test_halt_on_failure_skips_remaining_stages():
    stages = [
        _Stage("a", output=1),
        _Stage("boom", raises=RuntimeError("expected blast")),
        _Stage("never", output=99),
    ]
    ctx = StageContext(run_id=None)
    result = Pipeline(stages).run(ctx, halt_on_failure=True)

    assert result.ok is False
    assert result.halted_at == "boom"
    assert [r.name for r in result.stage_results] == ["a", "boom"]
    assert "RuntimeError" in (result.error or "")


def test_continue_on_failure_runs_all_stages():
    stages = [
        _Stage("a", output=1),
        _Stage("boom", raises=RuntimeError("expected blast")),
        _Stage("c", output=3),
    ]
    ctx = StageContext(run_id=None)
    result = Pipeline(stages).run(ctx, halt_on_failure=False)

    assert result.ok is False  # still false — one stage failed
    assert result.halted_at is None
    assert [r.name for r in result.stage_results] == ["a", "boom", "c"]
    assert ctx.get("c") == 3


# --------------------------- events ------------------------------------


def test_emits_lifecycle_events_in_order():
    events: list[tuple[str, dict]] = []

    def hook(kind, payload):
        events.append((kind, payload))

    ctx = StageContext(run_id=None, on_event=hook)
    Pipeline([_Stage("a", output=1)]).run(ctx)
    kinds = [k for k, _ in events]
    assert kinds == ["pipeline_start", "stage_start", "stage_end", "pipeline_end"]
    # stage_end payload has a serializable summary, not the raw output object
    end = next(p for k, p in events if k == "stage_end")
    assert end["name"] == "a"
    assert end["outcome"] == "success"


def test_event_hook_failure_does_not_break_pipeline():
    def bad_hook(kind, payload):
        raise RuntimeError("hook crash")

    ctx = StageContext(run_id=None, on_event=bad_hook)
    result = Pipeline([_Stage("a", output=1)]).run(ctx)
    assert result.ok  # the run itself isn't affected


# --------------------------- input validation --------------------------


def test_empty_pipeline_rejected():
    with pytest.raises(ValueError):
        Pipeline([])
