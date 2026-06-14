"""A CLI-backed LLMAdapter so CLI providers can drive the ReAct loop.

The ReAct loop (``core/react/loop.py``) is adapter-agnostic — it only needs
something implementing the ``LLMAdapter`` protocol (``chat_with_tools`` →
``LLMResponse`` with structured ``tool_calls``).  API providers expose native
tool-use; CLI providers (claude / codex / gemini) do not, so this adapter
bridges the gap with a **text-based ReAct protocol**:

  * Each turn, the full conversation + tool catalog is rendered into a single
    prompt and the CLI is invoked **single-turn** (``--max-turns 1``).  The CLI
    does NOT run tools itself — it only reasons and emits ONE JSON object:
        {"thought": "...", "tool_call": {"name": "...", "arguments": {...}}}
      or
        {"thought": "...", "final": {"findings": [...]}}
  * Moonwing executes the requested tool from its registry (capability gating,
    KG provenance, activity log all apply), then re-invokes the CLI with the
    appended result.

CLI providers are flat-rate, so re-sending the transcript each turn (the cost
that would matter on a metered API) is free here.

This adapter lives in ``worker`` (not ``core/llm``) because it shells out to
the AI CLI binaries; it satisfies ``LLMAdapter`` structurally without importing it.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import uuid
from typing import Any

from moonwing.core.llm import LLMError, LLMResponse, LLMToolCall, LLMUsage
from moonwing.worker.clearwing_runner import build_ai_cli_command

logger = logging.getLogger("moonwing.worker.cli_agent_adapter")


_PROTOCOL_INSTRUCTIONS = """\

## Response protocol (STRICT)
You are driving a tool-use loop. Do NOT run any tools or shell commands
yourself. On each turn, respond with EXACTLY ONE JSON object and nothing else,
in one of these two shapes:

To call a tool:
{"thought": "<brief reasoning>", "tool_call": {"name": "<tool name>", "arguments": {<args matching the tool schema>}}}

When you have gathered enough evidence and are done:
{"thought": "<brief reasoning>", "final": {"findings": [ ... ]}}

Rules:
- Call exactly one tool per turn. Wait for its result before the next call.
- Only use tools from the catalog below. Use the exact tool name.
- The `final.findings` array must match the findings schema in the instructions.
- Output ONLY the JSON object — no prose, no markdown fences, no commentary.

## Tool catalog
"""


def _render_tool_catalog(tools: list[dict[str, Any]] | None) -> str:
    if not tools:
        return "(no tools available)\n"
    lines: list[str] = []
    for t in tools:
        fn = t.get("function", t)
        name = fn.get("name", "?")
        desc = (fn.get("description") or "").strip()
        params = fn.get("parameters") or {}
        props = params.get("properties") or {}
        arg_names = ", ".join(props.keys()) if props else "(none)"
        lines.append(f"- {name}({arg_names}): {desc}")
    return "\n".join(lines) + "\n"


def _render_conversation(messages: list[dict[str, Any]]) -> str:
    """Flatten the chat transcript into plain text for a stateless CLI call."""
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "system":
            parts.append(f"# INSTRUCTIONS\n{content}")
        elif role == "user":
            parts.append(f"# TASK\n{content}")
        elif role == "assistant":
            text = content.strip()
            if text:
                parts.append(f"# ASSISTANT\n{text}")
            for tc in msg.get("tool_calls") or []:
                args = tc.get("arguments")
                try:
                    args_str = json.dumps(args)
                except (TypeError, ValueError):
                    args_str = str(args)
                parts.append(f"# YOU CALLED TOOL\n{tc.get('name')}({args_str})")
        elif role == "tool":
            parts.append(
                f"# TOOL RESULT (call {msg.get('tool_call_id', '?')})\n{content}"
            )
    return "\n\n".join(parts)


def render_react_prompt(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
) -> str:
    """Build the single-turn prompt sent to the CLI for one ReAct step."""
    return (
        _render_conversation(messages)
        + "\n"
        + _PROTOCOL_INSTRUCTIONS
        + _render_tool_catalog(tools)
        + "\nRespond now with your single JSON object for the NEXT step."
    )


def _extract_assistant_text(provider: str, stdout: str) -> str:
    """Peel the CLI's JSON envelope to the assistant's textual content.

    Falls back to raw stdout so a format change degrades to "parse what we can"
    rather than losing the turn.
    """
    text = (stdout or "").strip()
    if not text:
        return ""

    # claude --output-format json  ->  {"type":"result","result":"<text>",...}
    # gemini -o json               ->  {"response":"<text>", ...}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = None
    if isinstance(obj, dict):
        for key in ("result", "response", "text"):
            if isinstance(obj.get(key), str) and obj[key].strip():
                return obj[key]
        # some envelopes nest under message.content
        msg = obj.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
            return msg["content"]

    # codex exec --json -> JSONL events; take the LAST agent message text.
    last_text = ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for cand in _event_text_candidates(event):
            if cand and cand.strip():
                last_text = cand
    if last_text:
        return last_text

    return text


def _event_text_candidates(event: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if not isinstance(event, dict):
        return out
    for value in (event.get("text"), event.get("response")):
        if isinstance(value, str):
            out.append(value)
    nested = event.get("item")
    if isinstance(nested, dict):
        for value in (nested.get("text"), nested.get("response")):
            if isinstance(value, str):
                out.append(value)
    msg = event.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
        out.append(msg["content"])
    return out


def _find_json_object(text: str) -> dict[str, Any] | None:
    """Tolerant extraction of the first JSON object from a CLI response."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            head, rest = cleaned.split("\n", 1)
            if head.strip().lower() in ("json", "json5"):
                cleaned = rest
    # Try the whole thing first, then a brace-bounded slice.
    for candidate in (cleaned, _brace_slice(cleaned)):
        if candidate is None:
            continue
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _brace_slice(text: str) -> str | None:
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        return text[first : last + 1]
    return None


class CliAgentAdapter:
    """LLMAdapter that drives a single ReAct turn through an AI CLI binary."""

    def __init__(
        self,
        *,
        provider: str,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.provider = provider
        self._env = env
        # Run the CLI hermetically. The network ReAct agent's tools execute
        # in-process (Moonwing runs them), so the CLI needs no workspace — and
        # giving it the scan workdir makes it auto-load ambient context files
        # (e.g. the authorization CLAUDE.md the worker writes), which Claude
        # Code flags as a prompt-injection attack and refuses. A clean empty
        # dir with no project files avoids that entirely.
        self._cwd = cwd or tempfile.mkdtemp(prefix="moonwing-cli-agent-")

    # --- LLMAdapter protocol ----------------------------------------

    def chat_with_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str,
        api_key: str = "",
        temperature: float = 0.1,
        max_tokens: int = 8192,
        timeout: int = 300,
        response_format: str | None = None,
    ) -> LLMResponse:
        prompt = render_react_prompt(messages, tools)
        command = build_ai_cli_command(
            provider=self.provider, model=model, prompt=prompt, source_tools=False
        )
        stdout = self._run_cli(command, timeout=timeout)
        text = _extract_assistant_text(self.provider, stdout)
        return self._parse_react_response(text)

    def list_models(self, *, api_key: str = "", timeout: int = 30) -> list[str]:
        # CLI providers expose models via the existing discovery path, not here.
        return []

    # --- internals --------------------------------------------------

    def _run_cli(self, command: list[str], *, timeout: int) -> str:
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self._cwd,
                env=self._env,
            )
        except FileNotFoundError as exc:
            raise LLMError(
                f"CLI binary not found: {command[0] if command else 'cli'}",
                provider=self.provider,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError(
                f"CLI agent step timed out after {timeout}s",
                provider=self.provider,
            ) from exc
        if proc.returncode != 0:
            raise LLMError(
                f"CLI exited with code {proc.returncode}: {(proc.stderr or '')[:300]}",
                provider=self.provider,
                raw=proc.stdout,
            )
        return proc.stdout

    def _parse_react_response(self, text: str) -> LLMResponse:
        obj = _find_json_object(text)
        if obj is None:
            # No parseable JSON — treat the raw text as a final answer so the
            # loop ends instead of spinning. _parse_findings_json downstream
            # will recover any findings it can.
            return LLMResponse(text=text or "", tool_calls=[], usage=LLMUsage())

        thought = obj.get("thought")
        thought = thought if isinstance(thought, str) else ""

        tool_call = obj.get("tool_call")
        if isinstance(tool_call, dict) and tool_call.get("name"):
            args = tool_call.get("arguments")
            if not isinstance(args, dict):
                args = {}
            call = LLMToolCall(
                id=uuid.uuid4().hex, name=str(tool_call["name"]), arguments=args
            )
            return LLMResponse(text=thought, tool_calls=[call], usage=LLMUsage())

        # Final answer: hand the findings object back as text so the executor's
        # _parse_findings_json picks it up.
        final = obj.get("final")
        if isinstance(final, dict):
            return LLMResponse(text=json.dumps(final), tool_calls=[], usage=LLMUsage())

        # The model emitted a JSON object that's neither a tool_call nor a
        # wrapped final — if it already looks like findings, pass it through.
        if isinstance(obj.get("findings"), list):
            return LLMResponse(text=json.dumps(obj), tool_calls=[], usage=LLMUsage())

        # Unknown shape: end the loop with whatever text we have.
        return LLMResponse(text=text or "", tool_calls=[], usage=LLMUsage())
