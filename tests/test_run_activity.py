from datetime import datetime, timezone
from types import SimpleNamespace

from moonwing.services.runs import create_run_snapshot
from moonwing.services.run_activity import (
    ACTIVE_RUN_STATUSES,
    append_run_activity,
    record_run_failure,
    serialize_run_activity,
)


def test_append_run_activity_preserves_existing_snapshot_and_adds_timestamped_entry():
    run = SimpleNamespace(execution_snapshot={"target": {"name": "secops"}})
    now = datetime(2026, 5, 2, 12, 30, tzinfo=timezone.utc)

    append_run_activity(run, stage="running", message="Executing scan", now=now)

    assert run.execution_snapshot["target"] == {"name": "secops"}
    assert run.execution_snapshot["activity"] == [
        {
            "timestamp": "2026-05-02T12:30:00Z",
            "stage": "running",
            "message": "Executing scan",
            "level": "info",
        }
    ]


def test_append_run_activity_trims_old_entries():
    run = SimpleNamespace(execution_snapshot={})

    for index in range(205):
        append_run_activity(run, stage="running", message=f"line {index}", limit=200)

    activity = run.execution_snapshot["activity"]
    assert len(activity) == 200
    assert activity[0]["message"] == "line 5"
    assert activity[-1]["message"] == "line 204"


def test_serialize_run_activity_reports_active_state_and_activity():
    run = SimpleNamespace(
        id="run-1",
        status="running",
        execution_snapshot={
            "activity": [
                {"timestamp": "now", "stage": "running", "message": "Still working", "level": "info"}
            ]
        },
    )

    payload = serialize_run_activity(run)

    assert payload["run_id"] == "run-1"
    assert payload["status"] == "running"
    assert payload["active"] is True
    assert payload["terminal"] is False
    assert payload["activity"][0]["message"] == "Still working"
    assert "running" in ACTIVE_RUN_STATUSES


def test_record_run_failure_persists_sanitized_reason_and_activity_entry():
    run = SimpleNamespace(execution_snapshot={})

    record_run_failure(run, RuntimeError("scanner exited with code 1: token=secret\nsecond line"))

    assert run.execution_snapshot["failure"]["type"] == "RuntimeError"
    assert run.execution_snapshot["failure"]["message"] == "scanner exited with code 1: token=[redacted] second line"
    assert run.execution_snapshot["activity"][-1]["stage"] == "failed"
    assert run.execution_snapshot["activity"][-1]["message"] == (
        "Run failed: scanner exited with code 1: token=[redacted] second line"
    )


def test_serialize_run_activity_includes_failure_reason():
    run = SimpleNamespace(
        id="run-1",
        status="failed",
        execution_snapshot={
            "failure": {
                "type": "ExecutionError",
                "message": "scanner stdout is not valid JSON",
            }
        },
    )

    payload = serialize_run_activity(run)

    assert payload["failure"]["message"] == "scanner stdout is not valid JSON"


def test_create_run_snapshot_keeps_production_api_compatibility():
    run = SimpleNamespace(
        id="run-1",
        job_family="network_scan",
        status="queued",
        provider="openai",
        model="gpt-5.5",
        execution_mode="api",
        target_id=None,
        created_at=None,
    )

    snapshot = create_run_snapshot(run)

    assert snapshot["id"] == "run-1"
    assert snapshot["status"] == "queued"
    assert snapshot["execution_mode"] == "api"
