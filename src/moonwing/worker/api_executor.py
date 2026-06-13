"""Direct API executor — one-shot prompt-only scan execution.

Public surface (``execute_via_api``, ``APIExecutionError``,
``APIExecutionResult``) is unchanged so the worker and tests keep working.
Internally this delegates to the unified ``moonwing.core.llm`` adapter
layer — the same seam the Phase-1 ReAct loop will use.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from moonwing.core.llm import LLMError, get_adapter
from moonwing.worker.clearwing_runner import _get_prompt, append_operator_ai_instruction

logger = logging.getLogger("moonwing.worker.api_executor")

DEFAULT_TIMEOUT_SECONDS = 300


class APIExecutionError(RuntimeError):
    """Raised when an API call fails. Public — callers depend on it."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class APIExecutionResult:
    raw_payload: dict
    provider: str
    model: str
    usage: dict | None = None


_SYSTEM_PROMPT = (
    "You are a security scanner. Respond only with valid JSON matching the requested schema."
)


def _parse_json(text: str, *, provider: str) -> dict:
    """Strip the fences a few providers add and parse a JSON object."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        # drop a possible language tag on the first line
        if "\n" in cleaned:
            head, rest = cleaned.split("\n", 1)
            if head.strip().lower() in ("json", "json5"):
                cleaned = rest
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise APIExecutionError(
            f"{provider} response is not valid JSON: {exc}. First 200 chars: {cleaned[:200]!r}"
        ) from exc


def execute_via_api(
    *,
    provider: str,
    model: str,
    api_key: str,
    job_family: str,
    source_ref: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ai_instruction: str | None = None,
) -> APIExecutionResult:
    """Execute a security scan via direct API call (no tool use, single shot)."""
    prompt = append_operator_ai_instruction(
        _get_prompt(job_family, source_ref),
        ai_instruction,
    )

    logger.info("API execution: provider=%s model=%s source=%s", provider, model, source_ref)

    try:
        adapter = get_adapter(provider)
    except ValueError as exc:
        raise APIExecutionError(f"unsupported provider for API execution: {provider!r}") from exc

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    try:
        resp = adapter.chat_with_tools(
            messages=messages,
            tools=None,
            model=model,
            api_key=api_key,
            temperature=0.1,
            max_tokens=8192,
            timeout=timeout,
            response_format="json_object",
        )
    except LLMError as exc:
        raise APIExecutionError(str(exc), status_code=exc.status_code) from exc

    payload = _parse_json(resp.text, provider=provider)
    if not isinstance(payload, dict):
        raise APIExecutionError(
            f"API response parsed to {type(payload).__name__}, expected dict"
        )

    if "findings" not in payload:
        payload["findings"] = []

    usage = resp.usage.raw if resp.usage else None
    return APIExecutionResult(
        raw_payload=payload, provider=provider, model=model, usage=usage
    )
