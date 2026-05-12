from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


class ExecutionError(RuntimeError):
    pass


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
        # contains a JSON object with findings.
        payload = None
        for line in text.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and "findings" in item:
                payload = item
            elif isinstance(item, dict) and isinstance(item.get("raw_payload"), dict):
                payload = item["raw_payload"]
        if payload is None:
            raise ExecutionError(f"scanner stdout is not valid JSON: {text[:200]!r}")

    if not isinstance(payload, dict):
        raise ExecutionError(f"scanner output parsed to {type(payload).__name__}, expected dict")
    payload.setdefault("findings", [])
    return payload


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
        raise ExecutionError(f"scanner timed out after {timeout}s") from exc

    if proc.returncode != 0:
        raise ExecutionError(f"scanner exited with code {proc.returncode}: {proc.stderr[:500]}")

    return ExecutionResult(
        raw_payload=_parse_payload(proc.stdout),
        stdout=proc.stdout,
        stderr=proc.stderr,
        returncode=proc.returncode,
    )
