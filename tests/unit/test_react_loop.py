from __future__ import annotations

from typing import Any

import pytest

from moonwing.core.llm import LLMError, LLMResponse, LLMToolCall, LLMUsage
from moonwing.core.react import Budget, ReActAgent, ReActStopReason
from moonwing.core.tools import (
    Capability,
    ToolContext,
    ToolDomain,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class ScriptedAdapter:
    """Returns one canned LLMResponse per chat_with_tools call, in order."""

    provider = "fake"

    def __init__(self, responses: list[LLMResponse], *, raise_on=None):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self._raise_on = raise_on

    def chat_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_on is not None and len(self.calls) == self._raise_on:
            raise LLMError("provider blew up", status_code=500, provider="fake")
        if not self._responses:
            raise AssertionError("scripted adapter ran out of responses")
        return self._responses.pop(0)

    def list_models(self, **kwargs):
        return []


def _echo_spec(name: str = "echo") -> ToolSpec:
    def handler(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, output={"echo": args})

    return ToolSpec(
        name=name,
        domain=ToolDomain.META,
        description="echo back the args",
        params_schema={"type": "object", "properties": {}, "additionalProperties": True},
        capabilities=frozenset(),
        handler=handler,
    )


def _sandbox_spec(name: str = "needs_sandbox") -> ToolSpec:
    def handler(args, ctx):
        return ToolResult(ok=True, output={})

    return ToolSpec(
        name=name,
        domain=ToolDomain.SCAN,
        description="x",
        params_schema={"type": "object", "additionalProperties": True},
        capabilities=frozenset({Capability.NEEDS_SANDBOX}),
        handler=handler,
    )


def _resp(text: str = "", tool_calls=None, in_tokens=10, out_tokens=5) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=tool_calls or [],
        usage=LLMUsage(input_tokens=in_tokens, output_tokens=out_tokens),
    )


# ---------------------------------------------------------------------------
# Loop behavior
# ---------------------------------------------------------------------------


def test_final_answer_stops_loop():
    reg = ToolRegistry()
    reg.register(_echo_spec())
    adapter = ScriptedAdapter([_resp(text="here is my answer")])
    agent = ReActAgent(adapter=adapter, registry=reg, budget=Budget(max_iterations=5))

    transcript = agent.run(
        system_prompt="you are an agent",
        user_prompt="go",
        model="m",
        api_key="k",
        ctx=ToolContext(run_id=None),
    )

    assert transcript.stop_reason == ReActStopReason.FINAL_ANSWER
    assert transcript.final_text == "here is my answer"
    assert len(transcript.steps) == 1
    assert agent.budget.iterations_used == 1


def test_tool_call_then_final_answer():
    reg = ToolRegistry()
    reg.register(_echo_spec())

    adapter = ScriptedAdapter([
        _resp(tool_calls=[LLMToolCall(id="c1", name="echo", arguments={"a": 1})]),
        _resp(text="done"),
    ])
    agent = ReActAgent(adapter=adapter, registry=reg, budget=Budget(max_iterations=5))

    t = agent.run(
        system_prompt="s",
        user_prompt="u",
        model="m",
        api_key="k",
        ctx=ToolContext(run_id=None),
    )

    assert t.stop_reason == ReActStopReason.FINAL_ANSWER
    assert t.final_text == "done"
    assert len(t.steps) == 2

    invocation = t.steps[0].tool_invocations[0]
    assert invocation.call.name == "echo"
    assert invocation.result.ok is True
    assert invocation.result.output == {"echo": {"a": 1}}

    # Second turn must see the tool result.
    second_msgs = adapter.calls[1]["messages"]
    roles = [m["role"] for m in second_msgs]
    assert "tool" in roles


def test_unknown_tool_yields_error_result_not_crash():
    reg = ToolRegistry()
    adapter = ScriptedAdapter([
        _resp(tool_calls=[LLMToolCall(id="c1", name="nope", arguments={})]),
        _resp(text="bailing"),
    ])
    agent = ReActAgent(adapter=adapter, registry=reg, budget=Budget(max_iterations=5))

    t = agent.run(
        system_prompt="s",
        user_prompt="u",
        model="m",
        api_key="k",
        ctx=ToolContext(run_id=None),
    )
    invocation = t.steps[0].tool_invocations[0]
    assert invocation.result.ok is False
    assert "not registered" in invocation.result.error


def test_budget_iteration_cap_stops_loop():
    reg = ToolRegistry()
    reg.register(_echo_spec())
    # Adapter would loop forever; budget caps it.
    adapter = ScriptedAdapter(
        [_resp(tool_calls=[LLMToolCall(id="c", name="echo", arguments={})])] * 10
    )
    agent = ReActAgent(adapter=adapter, registry=reg, budget=Budget(max_iterations=3))

    t = agent.run(
        system_prompt="s",
        user_prompt="u",
        model="m",
        api_key="k",
        ctx=ToolContext(run_id=None),
    )
    assert t.stop_reason == ReActStopReason.BUDGET_EXHAUSTED
    assert agent.budget.iterations_used == 3
    assert "iterations" in (t.error or "")


def test_last_budgeted_iteration_forces_final_answer_turn():
    reg = ToolRegistry()
    reg.register(_echo_spec())

    class ToolUntilFinalAdapter:
        provider = "fake"

        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        def chat_with_tools(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("tools"):
                return _resp(
                    tool_calls=[LLMToolCall(id="c", name="echo", arguments={"probe": True})]
                )
            return _resp(text='{"findings": [{"title": "synthesized"}]}')

        def list_models(self, **kwargs):
            return []

    adapter = ToolUntilFinalAdapter()
    agent = ReActAgent(adapter=adapter, registry=reg, budget=Budget(max_iterations=2))

    t = agent.run(
        system_prompt="s",
        user_prompt="u",
        model="m",
        api_key="k",
        ctx=ToolContext(run_id=None),
    )

    assert t.stop_reason == ReActStopReason.FINAL_ANSWER
    assert "synthesized" in t.final_text
    assert len(adapter.calls) == 2
    assert adapter.calls[0]["tools"]
    assert adapter.calls[1]["tools"] is None
    assert "No further tool calls are available" in adapter.calls[1]["messages"][-1]["content"]


def test_provider_error_returns_partial_transcript():
    reg = ToolRegistry()
    reg.register(_echo_spec())
    adapter = ScriptedAdapter(
        [_resp(tool_calls=[LLMToolCall(id="c", name="echo", arguments={})])],
        raise_on=2,
    )
    agent = ReActAgent(adapter=adapter, registry=reg, budget=Budget(max_iterations=5))

    t = agent.run(
        system_prompt="s",
        user_prompt="u",
        model="m",
        api_key="k",
        ctx=ToolContext(run_id=None),
    )
    assert t.stop_reason == ReActStopReason.PROVIDER_ERROR
    assert len(t.steps) == 1  # first iteration completed before error


def test_denied_capabilities_filters_tools_offered_to_model():
    reg = ToolRegistry()
    reg.register(_echo_spec("safe"))
    reg.register(_sandbox_spec("dangerous"))

    adapter = ScriptedAdapter([_resp(text="ok")])
    agent = ReActAgent(adapter=adapter, registry=reg, budget=Budget(max_iterations=2))

    agent.run(
        system_prompt="s",
        user_prompt="u",
        model="m",
        api_key="k",
        ctx=ToolContext(run_id=None),
        denied_capabilities={Capability.NEEDS_SANDBOX},
    )

    offered_tools = adapter.calls[0]["tools"]
    offered_names = {t["function"]["name"] for t in offered_tools}
    assert offered_names == {"safe"}


def test_allowed_tools_restricts_offering():
    reg = ToolRegistry()
    reg.register(_echo_spec("a"))
    reg.register(_echo_spec("b"))
    adapter = ScriptedAdapter([_resp(text="ok")])
    agent = ReActAgent(adapter=adapter, registry=reg, budget=Budget(max_iterations=2))

    agent.run(
        system_prompt="s",
        user_prompt="u",
        model="m",
        api_key="k",
        ctx=ToolContext(run_id=None),
        allowed_tools=["a"],
    )
    offered = {t["function"]["name"] for t in adapter.calls[0]["tools"]}
    assert offered == {"a"}


def test_unoffered_allowed_tool_call_is_rejected_at_dispatch():
    reg = ToolRegistry()
    reg.register(_echo_spec("a"))
    reg.register(_echo_spec("b"))
    adapter = ScriptedAdapter(
        [
            _resp(tool_calls=[LLMToolCall(id="c", name="b", arguments={})]),
            _resp(text="done"),
        ]
    )
    agent = ReActAgent(adapter=adapter, registry=reg, budget=Budget(max_iterations=3))

    transcript = agent.run(
        system_prompt="s",
        user_prompt="u",
        model="m",
        api_key="k",
        ctx=ToolContext(run_id=None),
        allowed_tools=["a"],
    )

    invocation = transcript.steps[0].tool_invocations[0]
    assert invocation.result.ok is False
    assert "not allowed" in invocation.result.error


def test_denied_capability_tool_call_is_rejected_at_dispatch():
    reg = ToolRegistry()
    reg.register(_echo_spec("safe"))
    reg.register(_sandbox_spec("dangerous"))
    adapter = ScriptedAdapter(
        [
            _resp(tool_calls=[LLMToolCall(id="c", name="dangerous", arguments={})]),
            _resp(text="done"),
        ]
    )
    agent = ReActAgent(adapter=adapter, registry=reg, budget=Budget(max_iterations=3))

    transcript = agent.run(
        system_prompt="s",
        user_prompt="u",
        model="m",
        api_key="k",
        ctx=ToolContext(run_id=None),
        denied_capabilities={Capability.NEEDS_SANDBOX},
    )

    invocation = transcript.steps[0].tool_invocations[0]
    assert invocation.result.ok is False
    assert "not allowed" in invocation.result.error


def test_step_and_tool_hooks_fire_in_order():
    reg = ToolRegistry()
    reg.register(_echo_spec("e"))
    adapter = ScriptedAdapter(
        [
            _resp(tool_calls=[LLMToolCall(id="c", name="e", arguments={})]),
            _resp(text="done"),
        ]
    )
    seen_steps: list[int] = []
    seen_tools: list[str] = []

    agent = ReActAgent(
        adapter=adapter,
        registry=reg,
        budget=Budget(max_iterations=5),
        on_step=lambda step: seen_steps.append(step.iteration),
        on_tool_invocation=lambda step, inv: seen_tools.append(inv.call.name),
    )

    agent.run(
        system_prompt="s",
        user_prompt="u",
        model="m",
        api_key="k",
        ctx=ToolContext(run_id=None),
    )
    assert seen_steps == [1, 2]
    assert seen_tools == ["e"]


def test_no_progress_loop_breaks_after_two_empty_turns():
    reg = ToolRegistry()
    reg.register(_echo_spec())
    adapter = ScriptedAdapter([_resp(text=""), _resp(text="")])
    agent = ReActAgent(adapter=adapter, registry=reg, budget=Budget(max_iterations=10))
    t = agent.run(
        system_prompt="s",
        user_prompt="u",
        model="m",
        api_key="k",
        ctx=ToolContext(run_id=None),
    )
    assert t.stop_reason == ReActStopReason.NO_PROGRESS
