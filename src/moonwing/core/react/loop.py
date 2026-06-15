"""The ReAct agent — reason → tool-call → observe → repeat."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from moonwing.core.llm import LLMAdapter, LLMError, LLMResponse, LLMToolCall
from moonwing.core.tools import (
    Capability,
    DEFAULT_REGISTRY,
    ToolContext,
    ToolRegistry,
    ToolResult,
    UnknownToolError,
)

from .budget import Budget, BudgetExceededError

logger = logging.getLogger("moonwing.core.react.loop")


class ReActStopReason(StrEnum):
    FINAL_ANSWER = "final_answer"           # model emitted only text, no tool calls
    BUDGET_EXHAUSTED = "budget_exhausted"   # iteration/token/wall limit hit
    PROVIDER_ERROR = "provider_error"       # LLMError from adapter, fatal
    NO_PROGRESS = "no_progress"             # model emitted empty text and empty tool list


@dataclass(frozen=True)
class ReActToolInvocation:
    """One tool call + its result, for the transcript."""

    call: LLMToolCall
    result: ToolResult


@dataclass
class ReActStep:
    """One iteration: assistant message + (maybe) tool calls + their results."""

    iteration: int
    assistant_text: str
    tool_invocations: list[ReActToolInvocation] = field(default_factory=list)
    usage_input_tokens: int | None = None
    usage_output_tokens: int | None = None


@dataclass
class ReActTranscript:
    steps: list[ReActStep]
    stop_reason: ReActStopReason
    final_text: str
    error: str | None = None


# Hook signatures — kept narrow so the worker can plug in DB writes
# without the agent caring about DB types.
StepHook = Callable[[ReActStep], None]
ToolHook = Callable[[ReActStep, ReActToolInvocation], None]


_NO_PROGRESS_GUARD = 2  # consecutive empty/no-call iterations before bailing
_FINAL_TURN_PROMPT = (
    "No further tool calls are available in this run. Use the evidence already "
    "collected in the conversation and return your final answer now."
)


class ReActAgent:
    """Drives a single agentic conversation against an ``LLMAdapter``.

    Pure-Python — no DB, no I/O outside the adapter and tool handlers.
    Tests can wire a fake adapter + fake registry and step through deterministically.
    """

    def __init__(
        self,
        *,
        adapter: LLMAdapter,
        registry: ToolRegistry = DEFAULT_REGISTRY,
        budget: Budget | None = None,
        on_step: StepHook | None = None,
        on_tool_invocation: ToolHook | None = None,
    ) -> None:
        self._adapter = adapter
        self._registry = registry
        self._budget = budget or Budget()
        self._on_step = on_step
        self._on_tool_invocation = on_tool_invocation

    @property
    def budget(self) -> Budget:
        return self._budget

    # ----------------------------------------------------------------

    def _resolve_tool_specs(
        self,
        *,
        allowed_tools: list[str] | None,
        denied_capabilities: set[Capability] | None,
    ) -> list[Any]:
        """Pick the tools the model is allowed to call this run."""
        if allowed_tools:
            specs = [self._registry.get(name) for name in allowed_tools]
        else:
            specs = list(iter(self._registry))
        if denied_capabilities:
            specs = [s for s in specs if not (s.capabilities & denied_capabilities)]
        return specs

    def _dispatch_tool(
        self,
        call: LLMToolCall,
        ctx: ToolContext,
        *,
        allowed_tool_names: set[str] | None = None,
    ) -> ToolResult:
        """Look up + execute one tool call, wrapping failures as ToolResult."""
        try:
            spec = self._registry.get(call.name)
        except UnknownToolError:
            return ToolResult(
                ok=False,
                output=None,
                error=f"tool {call.name!r} is not registered",
            )
        if allowed_tool_names is not None and spec.name not in allowed_tool_names:
            return ToolResult(
                ok=False,
                output=None,
                error=f"tool {call.name!r} is not allowed for this run",
            )
        try:
            return spec.handler(call.arguments or {}, ctx)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("tool %s crashed", call.name)
            return ToolResult(
                ok=False, output=None, error=f"{type(exc).__name__}: {exc}"
            )

    def _serialize_tool_result(self, result: ToolResult) -> str:
        """Marshal a ToolResult into the string content of a `tool` message."""
        if result.ok:
            try:
                return json.dumps({"ok": True, "output": result.output})
            except (TypeError, ValueError):
                return json.dumps({"ok": True, "output": str(result.output)})
        return json.dumps({"ok": False, "error": result.error or "unknown error"})

    # ----------------------------------------------------------------

    def run(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        api_key: str,
        ctx: ToolContext,
        allowed_tools: list[str] | None = None,
        denied_capabilities: set[Capability] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        timeout: int = 300,
    ) -> ReActTranscript:
        """Drive the loop. Returns once the model gives a final answer or
        a stop condition fires."""
        specs = self._resolve_tool_specs(
            allowed_tools=allowed_tools, denied_capabilities=denied_capabilities
        )
        allowed_tool_names = {s.name for s in specs}
        tools_schema = [s.to_openai_schema() for s in specs]

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        steps: list[ReActStep] = []
        no_progress_streak = 0
        final_text = ""

        while True:
            try:
                self._budget.advance()
            except BudgetExceededError as exc:
                return ReActTranscript(
                    steps=steps,
                    stop_reason=ReActStopReason.BUDGET_EXHAUSTED,
                    final_text=final_text,
                    error=str(exc),
                )

            force_final_turn = (
                self._budget.max_iterations is not None
                and self._budget.iterations_used >= self._budget.max_iterations
            )
            messages_for_turn = messages
            tools_for_turn = tools_schema or None
            if force_final_turn:
                messages_for_turn = [
                    *messages,
                    {"role": "user", "content": _FINAL_TURN_PROMPT},
                ]
                tools_for_turn = None

            try:
                response: LLMResponse = self._adapter.chat_with_tools(
                    messages=messages_for_turn,
                    tools=tools_for_turn,
                    model=model,
                    api_key=api_key,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
            except LLMError as exc:
                return ReActTranscript(
                    steps=steps,
                    stop_reason=ReActStopReason.PROVIDER_ERROR,
                    final_text=final_text,
                    error=str(exc),
                )

            input_tokens = (
                response.usage.input_tokens if response.usage else None
            )
            output_tokens = (
                response.usage.output_tokens if response.usage else None
            )
            self._budget.record_usage(
                input_tokens=input_tokens, output_tokens=output_tokens
            )

            step = ReActStep(
                iteration=self._budget.iterations_used,
                assistant_text=response.text or "",
                usage_input_tokens=input_tokens,
                usage_output_tokens=output_tokens,
            )

            # Append the assistant message to the conversation so the next
            # iteration can see what was decided. Tool calls in the assistant
            # message are serialized in OpenAI-shape; the Anthropic adapter
            # converts them to content blocks internally.
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.text or "",
            }
            if response.tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ]
            messages.append(assistant_msg)

            if not response.tool_calls:
                # Either a final answer or a no-progress turn.
                if not (response.text or "").strip():
                    no_progress_streak += 1
                else:
                    no_progress_streak = 0
                    final_text = response.text
                steps.append(step)
                if self._on_step:
                    self._on_step(step)
                if no_progress_streak >= _NO_PROGRESS_GUARD:
                    return ReActTranscript(
                        steps=steps,
                        stop_reason=ReActStopReason.NO_PROGRESS,
                        final_text=final_text,
                        error="model emitted empty turns",
                    )
                if (response.text or "").strip():
                    return ReActTranscript(
                        steps=steps,
                        stop_reason=ReActStopReason.FINAL_ANSWER,
                        final_text=final_text,
                    )
                # Empty turn but not yet at guard — go around again.
                continue

            # Execute each tool call and append its result.
            for tc in response.tool_calls:
                result = self._dispatch_tool(
                    tc, ctx, allowed_tool_names=allowed_tool_names
                )
                invocation = ReActToolInvocation(call=tc, result=result)
                step.tool_invocations.append(invocation)
                if self._on_tool_invocation:
                    self._on_tool_invocation(step, invocation)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": self._serialize_tool_result(result),
                    }
                )

            steps.append(step)
            if self._on_step:
                self._on_step(step)
            no_progress_streak = 0
