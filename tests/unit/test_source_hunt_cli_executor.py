from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from moonwing.worker.executor import ExecutionResult
from moonwing.worker.source_hunt_cli_executor import execute_source_hunt_cli_pipeline


def test_cli_source_hunt_runs_staged_pipeline_and_stamps_findings(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text("SECRET = 'admin'\n")
    (repo / "README.md").write_text("docs\n")

    run = SimpleNamespace(id=uuid4(), provider="openai", model="gpt-x")
    calls: list[dict] = []

    def fake_execute_clearwing(*, command, env=None, timeout=600, cwd=None):
        calls.append({"command": command, "env": env, "timeout": timeout, "cwd": cwd})
        return ExecutionResult(
            raw_payload={
                "findings": [
                    {
                        "title": "hardcoded secret",
                        "severity": "high",
                        "evidence": ["auth.py:1"],
                    }
                ]
            },
            stdout="{}",
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(
        "moonwing.worker.source_hunt_cli_executor.execute_clearwing",
        fake_execute_clearwing,
    )
    monkeypatch.setattr(
        "moonwing.worker.source_hunt_cli_executor.build_ai_cli_command",
        lambda *, provider, model, prompt, source_tools=False: ["cli", prompt],
    )

    stage_events: list[tuple[str, dict]] = []
    hunter_events: list[tuple[str, dict]] = []

    result = execute_source_hunt_cli_pipeline(
        session=None,
        run=run,
        workdir=str(tmp_path / "work"),
        source_ref=str(repo),
        input_kind="path",
        profile_settings={
            "agentic_mode": True,
            "agentic": {"top_n": 3, "hunter_max_files": 1},
        },
        on_stage_event=lambda kind, payload: stage_events.append((kind, payload)),
        on_hunter_event=lambda kind, payload: hunter_events.append((kind, payload)),
        timeout_seconds=300,
    )

    assert result.raw_payload["findings"][0]["title"] == "hardcoded secret"
    assert result.raw_payload["findings"][0]["source_file"] == "auth.py"
    assert result.raw_payload["findings"][0]["concern"] == "auth"
    assert result.usage["pipeline_ok"] is True
    assert result.usage["ranked_count"] == 1
    assert result.usage["merged_findings"] == 1
    assert calls[0]["cwd"] == str(repo.resolve())
    assert "Focus file: auth.py" in calls[0]["command"][1]
    assert [kind for kind, _ in hunter_events] == ["file_start", "file_end"]
    assert stage_events[0] == (
        "pipeline_start",
        {"name": "source_hunt_cli", "stages": ["acquire", "inventory", "rank", "hunt"]},
    )


def test_cli_source_hunt_returns_empty_payload_when_acquire_fails(monkeypatch, tmp_path):
    run = SimpleNamespace(id=uuid4(), provider="openai", model="gpt-x")
    monkeypatch.setattr(
        "moonwing.worker.source_hunt_cli_executor.execute_clearwing",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not call CLI")),
    )

    result = execute_source_hunt_cli_pipeline(
        session=None,
        run=run,
        workdir=str(tmp_path / "work"),
        source_ref=str(tmp_path / "missing"),
        input_kind="path",
        profile_settings={"agentic_mode": True},
    )

    assert result.raw_payload == {"findings": []}
    assert result.usage["pipeline_ok"] is False
    assert result.usage["halted_at"] == "acquire"


def test_cli_source_hunt_continues_after_one_hunter_fails(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "auth.py").write_text("password = request.form['password']\n")
    (repo / "admin.py").write_text("def dashboard(): pass\n")

    run = SimpleNamespace(id=uuid4(), provider="openai", model="gpt-x")
    calls = 0

    def fake_execute_clearwing(*, command, env=None, timeout=600, cwd=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("cli unavailable")
        return ExecutionResult(
            raw_payload={
                "findings": [
                    {
                        "title": "admin issue",
                        "severity": "medium",
                        "evidence": ["admin.py:1"],
                    }
                ]
            },
            stdout="{}",
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(
        "moonwing.worker.source_hunt_cli_executor.execute_clearwing",
        fake_execute_clearwing,
    )
    monkeypatch.setattr(
        "moonwing.worker.source_hunt_cli_executor.build_ai_cli_command",
        lambda *, provider, model, prompt, source_tools=False: ["cli", prompt],
    )

    hunter_events: list[tuple[str, dict]] = []
    result = execute_source_hunt_cli_pipeline(
        session=None,
        run=run,
        workdir=str(tmp_path / "work"),
        source_ref=str(repo),
        input_kind="path",
        profile_settings={
            "agentic_mode": True,
            "agentic": {"top_n": 2, "hunter_max_files": 2},
        },
        on_hunter_event=lambda kind, payload: hunter_events.append((kind, payload)),
    )

    assert calls == 2
    assert result.usage["pipeline_ok"] is True
    assert result.usage["hunter_errors"] == [
        {"path": "admin.py", "error": "cli unavailable"}
    ]
    assert [f["title"] for f in result.raw_payload["findings"]] == ["admin issue"]
    assert ("file_end", {"path": "admin.py", "ok": False, "error": "cli unavailable", "findings": 0, "iterations": 1}) in hunter_events
