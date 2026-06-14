"""Drive a ReAct-style agentic scan from the worker.

Wraps ``ReActAgent`` so the worker can swap it in wherever it used to
call ``execute_via_api``. The return shape mirrors ``APIExecutionResult``
so downstream normalization + persistence (and the KG hook from Phase
0.2) work without changes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from moonwing.core.llm import get_adapter
from moonwing.core.react import (
    Budget,
    ReActAgent,
    ReActStep,
    ReActStopReason,
    ReActToolInvocation,
    ReActTranscript,
)
from moonwing.core.react.prompts import (
    NETWORK_REACT_SYSTEM,
    network_user_prompt,
)
from moonwing.core.tools import Capability, ToolContext
from moonwing.worker.clearwing_runner import append_operator_ai_instruction

logger = logging.getLogger("moonwing.worker.react_executor")


@dataclass
class ReActExecutionResult:
    """Mirrors ``APIExecutionResult`` so downstream code doesn't care which path ran."""

    raw_payload: dict
    provider: str
    model: str
    usage: dict | None = None
    transcript: ReActTranscript | None = None


def _parse_findings_json(text: str) -> dict:
    """Extract the findings JSON object from the agent's final message.

    The model is instructed to emit JSON-only, but a small recovery layer
    handles the common cases — fenced code blocks, leading prose — so a
    single misformatted final message doesn't lose the whole run.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return {"findings": []}
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            head, rest = cleaned.split("\n", 1)
            if head.strip().lower() in ("json", "json5"):
                cleaned = rest
    # Trim any prose before the first '{' or after the last '}'.
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last != -1 and last > first:
        cleaned = cleaned[first : last + 1]
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("ReAct final message was not valid JSON; returning empty findings")
        return {"findings": []}
    if not isinstance(parsed, dict):
        return {"findings": []}
    if "findings" not in parsed:
        parsed["findings"] = []
    return parsed


def _denied_capabilities_for_profile(profile_settings: dict[str, Any]) -> set[Capability]:
    """Map a runtime profile's settings to a deny-set of tool capabilities.

    Defaults are conservative: every sandbox-required tool is denied
    until the operator opts in. ``allow_exploits`` (already a column on
    runtime_profiles) gates the ``destructive`` capability.
    """
    denied = {Capability.NEEDS_SANDBOX}
    if not profile_settings.get("allow_network_egress_tools", True):
        denied.add(Capability.NETWORK_EGRESS)
    if not profile_settings.get("allow_destructive_tools", False):
        denied.add(Capability.DESTRUCTIVE)
    return denied


def _budget_from_profile(profile_settings: dict[str, Any]) -> Budget:
    agentic = profile_settings.get("agentic") or {}
    return Budget(
        max_iterations=int(agentic.get("max_iterations") or 12),
        max_total_input_tokens=agentic.get("max_input_tokens"),
        max_wall_seconds=agentic.get("max_wall_seconds") or 600.0,
    )


def execute_network_react(
    *,
    session: Session,
    run,  # moonwing.db.models.Run — typed-loose to avoid import cycle
    target_address: str,
    api_key: str,
    profile_settings: dict[str, Any] | None = None,
    on_step: Callable[[ReActStep], None] | None = None,
    on_tool_invocation: Callable[[ReActStep, ReActToolInvocation], None] | None = None,
    on_note: Callable[[str], None] | None = None,
    ai_instruction: str | None = None,
    timeout_seconds: int = 600,
    cli_mode: bool = False,
    cli_env: dict[str, str] | None = None,
    workdir: str | None = None,
) -> ReActExecutionResult:
    """Run an agentic network scan and return a normalize-able payload.

    ``cli_mode`` drives the same ReAct loop through a local AI CLI binary
    (claude/codex/gemini) instead of a provider HTTP API, so CLI-credentialed
    runs get full agentic parity with API mode.
    """
    profile_settings = profile_settings or {}

    if cli_mode:
        from moonwing.worker.cli_agent_adapter import CliAgentAdapter

        # Deliberately do NOT pass the scan workdir: the CLI agent runs
        # hermetically so it never auto-loads the worker's authorization
        # CLAUDE.md (which Claude Code rejects as prompt injection). The
        # recon tools run in-process, so no workspace is needed.
        adapter = CliAgentAdapter(provider=run.provider, env=cli_env)
    else:
        adapter = get_adapter(run.provider)
    budget = _budget_from_profile(profile_settings)
    agent = ReActAgent(
        adapter=adapter,
        budget=budget,
        on_step=on_step,
        on_tool_invocation=on_tool_invocation,
    )

    ctx = ToolContext(
        run_id=run.id,
        session=session,
        target_address=target_address,
        timeout_seconds=min(timeout_seconds, 300),
        extras={"on_note": on_note} if on_note else {},
    )

    user_prompt = append_operator_ai_instruction(
        network_user_prompt(target_address), ai_instruction
    )

    transcript = agent.run(
        system_prompt=NETWORK_REACT_SYSTEM,
        user_prompt=user_prompt,
        model=run.model,
        api_key=api_key,
        ctx=ctx,
        denied_capabilities=_denied_capabilities_for_profile(profile_settings),
        timeout=min(timeout_seconds, 300),
    )

    payload = _parse_findings_json(transcript.final_text)
    usage = {
        "input_tokens": budget.input_tokens_used,
        "output_tokens": budget.output_tokens_used,
        "iterations": budget.iterations_used,
        "stop_reason": transcript.stop_reason.value,
    }
    if transcript.error:
        usage["error"] = transcript.error

    return ReActExecutionResult(
        raw_payload=payload,
        provider=run.provider,
        model=run.model,
        usage=usage,
        transcript=transcript,
    )
