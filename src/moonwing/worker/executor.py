from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


class ExecutionError(RuntimeError):
    def __init__(self, message: str, *, stdout: str | None = None, stderr: str | None = None) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True)
class ExecutionResult:
    raw_payload: dict
    stdout: str
    stderr: str
    returncode: int


def _parse_payload(stdout: str) -> dict:
    text = stdout.strip()
    if not text:
        raise ExecutionError("scanner produced no stdout")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # Codex JSON mode may emit JSONL events; accept the last event that
        # contains a JSON object with findings or an agent message containing
        # a final JSON object.
        payload = None
        for line in text.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            candidate = _payload_from_event(item)
            if candidate is not None:
                payload = candidate
        if payload is None:
            raise ExecutionError(
                f"scanner stdout is not valid JSON: {text[:200]!r}",
                stdout=text,
            )

    if not isinstance(payload, dict):
        raise ExecutionError(
            f"scanner output parsed to {type(payload).__name__}, expected dict",
            stdout=text,
        )
    if not payload.get("findings") and isinstance(payload.get("response"), str):
        try:
            nested = json.loads(payload["response"].strip())
        except json.JSONDecodeError:
            nested = None
        if isinstance(nested, dict) and isinstance(nested.get("findings"), list):
            payload["findings"] = nested["findings"]
    payload.setdefault("findings", [])
    return payload


def _payload_from_event(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    if isinstance(item.get("findings"), list):
        return item
    if isinstance(item.get("raw_payload"), dict):
        raw_payload = item["raw_payload"]
        return raw_payload if isinstance(raw_payload.get("findings"), list) else None

    for text in _event_text_candidates(item):
        payload = _parse_json_object_from_text(text)
        if payload is not None:
            return payload
    return None


def _event_text_candidates(item: dict) -> list[str]:
    candidates: list[str] = []
    for value in (item.get("text"), item.get("response")):
        if isinstance(value, str):
            candidates.append(value)

    nested = item.get("item")
    if isinstance(nested, dict):
        for value in (nested.get("text"), nested.get("response")):
            if isinstance(value, str):
                candidates.append(value)

    message = item.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        candidates.append(message["content"])
    return candidates


def _parse_json_object_from_text(text: str) -> dict | None:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if "\n" in cleaned:
            head, rest = cleaned.split("\n", 1)
            if head.strip().lower() in ("json", "json5"):
                cleaned = rest
    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first != -1 and last != -1 and last > first:
        cleaned = cleaned[first : last + 1]
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("findings"), list):
        return payload
    return None


def execute_clearwing(
    *,
    command: list[str],
    env: dict[str, str] | None = None,
    timeout: int = 600,
    cwd: str | None = None,
) -> ExecutionResult:
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
    except FileNotFoundError as exc:
        name = command[0] if command else "scanner"
        raise ExecutionError(
            f"scanner binary not found: {name}. "
            "The Moonwing worker container usually does not ship third-party AI CLIs. "
            "Use Execution mode **API** with an API-key credential, or install the CLI on the worker "
            "and set MOONWING_CLAUDE_CLI_BINARY / MOONWING_CODEX_CLI_BINARY / MOONWING_GEMINI_CLI_BINARY."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else None
        stderr = exc.stderr if isinstance(exc.stderr, str) else None
        raise ExecutionError(
            f"scanner timed out after {timeout}s",
            stdout=stdout,
            stderr=stderr,
        ) from exc

    if proc.returncode != 0:
        raise ExecutionError(
            f"scanner exited with code {proc.returncode}: {proc.stderr[:500]}",
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    return ExecutionResult(
        raw_payload=_parse_payload(proc.stdout),
        stdout=proc.stdout,
        stderr=proc.stderr,
        returncode=proc.returncode,
    )
