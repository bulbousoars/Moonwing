"""Worker-side source-hunt executor: full pipeline drive with scripted adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from moonwing.core.llm import LLMResponse, LLMUsage
from moonwing.db.base import Base
from moonwing.db.models import Credential, Run, RuntimeProfileRecord, Target, User
from moonwing.worker.source_hunt_executor import execute_source_hunt_pipeline


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def run_row(session):
    user = User(id=uuid4(), email="a@b.c", display_name="u", role="admin", status="active")
    cred = Credential(id=uuid4(), scope="user", provider="openai", display_name="k", secret_ref="v")
    profile = RuntimeProfileRecord(
        id=uuid4(),
        name="sh-agentic",
        allow_exploits=False,
        settings={"agentic_mode": True},
    )
    target = Target(
        id=uuid4(),
        target_type="source_repo",
        display_name="local repo",
        source_metadata={},
    )
    run = Run(
        id=uuid4(),
        job_family="source_hunt",
        target_id=target.id,
        runtime_profile_id=profile.id,
        credential_id=cred.id,
        user_id=user.id,
        status="running",
        provider="openai",
        model="gpt-x",
        execution_snapshot={},
    )
    session.add_all([user, cred, profile, target, run])
    session.commit()
    return run


class ScriptedAdapter:
    provider = "openai"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)

    def list_models(self, **kwargs):
        return []


def _install_adapter(monkeypatch, adapter):
    monkeypatch.setattr(
        "moonwing.worker.source_hunt_executor.get_adapter", lambda provider: adapter
    )


def test_executor_drives_pipeline_and_returns_findings(
    monkeypatch, session, run_row, tmp_path
):
    repo = tmp_path / "code"
    repo.mkdir()
    (repo / "main.py").write_text("def x(): pass\n")
    (repo / "auth.py").write_text("ADMIN = 'admin'\n")

    rank = json.dumps({
        "ranked": [
            {"path": "auth.py", "concern": "auth", "reason": "credentials"}
        ]
    })
    finding = {
        "title": "hardcoded admin literal",
        "severity": "high",
        "evidence": ["auth.py:1"],
    }
    adapter = ScriptedAdapter(
        [
            LLMResponse(text=rank, usage=LLMUsage(input_tokens=20, output_tokens=10)),
            LLMResponse(
                text=json.dumps({"findings": [finding]}),
                usage=LLMUsage(input_tokens=30, output_tokens=15),
            ),
        ]
    )
    _install_adapter(monkeypatch, adapter)

    stage_events: list[tuple[str, dict]] = []
    hunter_events: list[tuple[str, dict]] = []

    result = execute_source_hunt_pipeline(
        session=session,
        run=run_row,
        workdir=str(tmp_path / "workdir"),
        source_ref=str(repo),
        input_kind="path",
        api_key="k",
        profile_settings={
            "agentic_mode": True,
            "agentic": {"top_n": 2, "hunter_max_files": 5},
        },
        on_stage_event=lambda kind, payload: stage_events.append((kind, payload)),
        on_hunter_event=lambda kind, payload: hunter_events.append((kind, payload)),
    )

    assert result.raw_payload["findings"][0]["title"] == "hardcoded admin literal"
    assert result.raw_payload["findings"][0]["source_file"] == "auth.py"
    assert result.usage["pipeline_ok"] is True
    assert result.usage["merged_findings"] == 1
    assert result.usage["ranked_count"] == 1

    # Lifecycle events visible.
    stage_kinds = [k for k, _ in stage_events]
    assert stage_kinds.count("stage_start") == 4
    assert stage_kinds.count("stage_end") == 4

    hunter_kinds = [k for k, _ in hunter_events]
    assert hunter_kinds == ["file_start", "file_end"]


def test_executor_returns_empty_findings_on_acquire_failure(
    monkeypatch, session, run_row, tmp_path
):
    adapter = ScriptedAdapter([])  # adapter never called — acquire fails first
    _install_adapter(monkeypatch, adapter)

    result = execute_source_hunt_pipeline(
        session=session,
        run=run_row,
        workdir=str(tmp_path / "wd"),
        source_ref=str(tmp_path / "does_not_exist"),
        input_kind="path",
        api_key="k",
        profile_settings={"agentic_mode": True},
    )

    assert result.raw_payload == {"findings": []}
    assert result.usage["pipeline_ok"] is False
    assert result.usage["halted_at"] == "acquire"
    assert "error" in result.usage
    assert adapter.calls == []
