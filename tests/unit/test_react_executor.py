"""End-to-end test: ReAct executor with a scripted adapter touches KG + findings."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from moonwing.core.llm import LLMResponse, LLMToolCall, LLMUsage
from moonwing.core.kg_kinds import EdgeKind, NodeKind
from moonwing.db.base import Base
from moonwing.db.models import (
    Credential,
    KGNode,
    Run,
    RuntimeProfileRecord,
    Target,
    User,
)
from moonwing.services.kg import find_node, host_key, upsert_node
from moonwing.worker.react_executor import execute_network_react


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
    cred = Credential(
        id=uuid4(), scope="user", provider="openai", display_name="k", secret_ref="v"
    )
    profile = RuntimeProfileRecord(
        id=uuid4(),
        name="agentic",
        allow_exploits=False,
        settings={"agentic_mode": True},
    )
    target = Target(
        id=uuid4(),
        target_type="network_host",
        display_name="10.0.0.5",
        source_metadata={"address": "10.0.0.5"},
    )
    run = Run(
        id=uuid4(),
        job_family="network_scan",
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
        "moonwing.worker.react_executor.get_adapter", lambda provider: adapter
    )


def test_executor_round_trips_kg_query_and_emits_findings_json(monkeypatch, session, run_row):
    # Seed the KG so the agent's kg_query has something to find.
    upsert_node(
        session,
        kind=NodeKind.HOST,
        stable_key=host_key("10.0.0.5"),
        attrs={"prior_scan": "2026-06-01"},
    )
    session.commit()

    final_payload = {
        "findings": [
            {
                "title": "Open Redis on 10.0.0.5:6379",
                "severity": "high",
                "affected_hosts": ["10.0.0.5"],
                "evidence": ["kg shows prior scan; nmap step skipped"],
                "references": ["CVE-2022-9999"],
            }
        ]
    }

    adapter = ScriptedAdapter(
        [
            LLMResponse(
                text="checking memory first",
                tool_calls=[
                    LLMToolCall(
                        id="c1",
                        name="kg_query",
                        arguments={
                            "kind": "host",
                            "stable_key": host_key("10.0.0.5"),
                        },
                    )
                ],
                usage=LLMUsage(input_tokens=50, output_tokens=10),
            ),
            LLMResponse(
                text=json.dumps(final_payload),
                tool_calls=[],
                usage=LLMUsage(input_tokens=80, output_tokens=40),
            ),
        ]
    )
    _install_adapter(monkeypatch, adapter)

    notes: list[str] = []
    result = execute_network_react(
        session=session,
        run=run_row,
        target_address="10.0.0.5",
        api_key="k",
        profile_settings={"agentic_mode": True, "agentic": {"max_iterations": 5}},
        on_note=notes.append,
    )

    assert result.raw_payload == final_payload
    assert result.usage["iterations"] == 2
    assert result.usage["stop_reason"] == "final_answer"

    # The kg_query call observed the seeded host node.
    first_tool_invocation = result.transcript.steps[0].tool_invocations[0]
    assert first_tool_invocation.result.ok
    assert (
        first_tool_invocation.result.output["node"]["attrs"]["prior_scan"]
        == "2026-06-01"
    )


def test_executor_filters_sandbox_tools_by_default(monkeypatch, session, run_row):
    """Sandbox-required tools must not appear in the offered toolset."""
    adapter = ScriptedAdapter(
        [
            LLMResponse(
                text=json.dumps({"findings": []}),
                tool_calls=[],
                usage=LLMUsage(input_tokens=10, output_tokens=5),
            )
        ]
    )
    _install_adapter(monkeypatch, adapter)

    execute_network_react(
        session=session,
        run=run_row,
        target_address="10.0.0.5",
        api_key="k",
        profile_settings={"agentic_mode": True},
    )

    # The first (and only) adapter call must not have offered the sandbox tool.
    offered = {t["function"]["name"] for t in adapter.calls[0]["tools"]}
    assert "semgrep_scan" not in offered
    # nmap and the network-egress recon tools are fine — no sandbox needed.
    assert "nmap_scan" in offered
    assert "nuclei_scan" in offered  # retagged network_egress; available by default


def test_executor_routes_meta_note_to_callback(monkeypatch, session, run_row):
    adapter = ScriptedAdapter(
        [
            LLMResponse(
                text="planning",
                tool_calls=[
                    LLMToolCall(
                        id="n1",
                        name="meta_note",
                        arguments={"note": "remember to check TLS"},
                    )
                ],
                usage=LLMUsage(input_tokens=20, output_tokens=5),
            ),
            LLMResponse(
                text=json.dumps({"findings": []}),
                tool_calls=[],
                usage=LLMUsage(input_tokens=20, output_tokens=5),
            ),
        ]
    )
    _install_adapter(monkeypatch, adapter)

    captured: list[str] = []
    execute_network_react(
        session=session,
        run=run_row,
        target_address="10.0.0.5",
        api_key="k",
        profile_settings={"agentic_mode": True},
        on_note=captured.append,
    )
    assert captured == ["remember to check TLS"]


def test_executor_recovers_from_messy_final_text(monkeypatch, session, run_row):
    """If the model wraps its JSON in prose + fences, we still extract findings."""
    payload = {"findings": [{"title": "x", "severity": "low"}]}
    adapter = ScriptedAdapter(
        [
            LLMResponse(
                text=(
                    "Here is my final answer:\n```json\n"
                    + json.dumps(payload)
                    + "\n```\nLet me know if you need more."
                ),
                tool_calls=[],
                usage=LLMUsage(input_tokens=10, output_tokens=5),
            )
        ]
    )
    _install_adapter(monkeypatch, adapter)

    result = execute_network_react(
        session=session,
        run=run_row,
        target_address="10.0.0.5",
        api_key="k",
        profile_settings={"agentic_mode": True},
    )
    assert result.raw_payload == payload


def test_executor_budget_caps_propagate_from_profile(monkeypatch, session, run_row):
    # Loop forever via repeated tool calls; budget should clamp at 2 iterations.
    spam = LLMResponse(
        text="thinking",
        tool_calls=[LLMToolCall(id="c", name="meta_note", arguments={"note": "x"})],
        usage=LLMUsage(input_tokens=5, output_tokens=5),
    )
    adapter = ScriptedAdapter([spam] * 10)
    _install_adapter(monkeypatch, adapter)

    result = execute_network_react(
        session=session,
        run=run_row,
        target_address="10.0.0.5",
        api_key="k",
        profile_settings={
            "agentic_mode": True,
            "agentic": {"max_iterations": 2},
        },
    )
    assert result.usage["stop_reason"] == "budget_exhausted"
    assert result.usage["iterations"] == 2


def test_cli_mode_drives_loop_via_cli_adapter(monkeypatch, session, run_row):
    """execute_network_react(cli_mode=True) routes the SAME loop through the
    CLI adapter: a scripted CLI does a tool round then a final answer."""
    import json as _json
    import subprocess as _subprocess

    import moonwing.worker.cli_agent_adapter as cca

    responses = [
        _json.dumps({"thought": "note the plan", "tool_call": {
            "name": "meta_note", "arguments": {"note": "planning recon"}}}),
        _json.dumps({"thought": "done", "final": {"findings": [
            {"title": "Open SSH on 10.0.0.5:22", "severity": "info",
             "affected_hosts": ["10.0.0.5"]}]}}),
    ]
    seq = iter(responses)

    def fake_run(command, *a, **k):
        envelope = _json.dumps({"type": "result", "result": next(seq)})
        return _subprocess.CompletedProcess(
            args=command, returncode=0, stdout=envelope, stderr="")

    monkeypatch.setattr(cca.subprocess, "run", fake_run)

    notes: list[str] = []
    result = execute_network_react(
        session=session,
        run=run_row,
        target_address="10.0.0.5",
        api_key="",
        profile_settings={"agentic_mode": True},
        on_note=notes.append,
        cli_mode=True,
    )

    assert "planning recon" in notes  # tool actually dispatched by Moonwing
    assert result.usage["stop_reason"] == "final_answer"
    findings = result.raw_payload["findings"]
    assert len(findings) == 1
    assert findings[0]["title"] == "Open SSH on 10.0.0.5:22"
