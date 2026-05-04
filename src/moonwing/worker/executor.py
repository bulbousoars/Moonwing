"""Clearwing subprocess executor.

Runs a staged Clearwing command as a subprocess, captures stdout as the
raw JSON payload, and handles timeouts and non-zero exits.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger("moonwing.worker.executor")

DEFAULT_TIMEOUT_SECONDS = 600  # 10 minutes


class ExecutionError(RuntimeError):
    """Raised when Clearwing execution fails."""

    def __init__(self, message: str, *, returncode: int | None = None, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


@dataclass
class ExecutionResult:
    """Result of a Clearwing execution."""

    raw_payload: dict
    returncode: int
    stdout: str
    stderr: str
    command: list[str] = field(default_factory=list)


def execute_clearwing(
    *,
    command: list[str],
    env: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
) -> ExecutionResult:
    """Run a Clearwing command and return the parsed result.

    The command is expected to write JSON to stdout.  If stdout is not
    valid JSON, an ``ExecutionError`` is raised.

    Parameters
    ----------
    command:
        The full command list, e.g. ``["clearwing", "scan", "192.168.1.100"]``.
    env:
        Optional environment variables to pass to the subprocess.  If
        ``None``, the current process environment is inherited.
    timeout:
        Maximum seconds to wait before killing the subprocess.
    cwd:
        Working directory for the subprocess.

    Returns
    -------
    ExecutionResult
        Contains the parsed ``raw_payload`` dict, exit code, and raw
        stdout/stderr strings.

    Raises
    ------
    ExecutionError
        On non-zero exit, timeout, or unparseable stdout.
    """
    logger.info("executing: %s (timeout=%ds)", " ".join(command), timeout)

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        raise ExecutionError(
            f"clearwing binary not found: {command[0]!r}. "
            "Install Clearwing or set MOONWING_CLEARWING_BINARY to the correct path.",
            returncode=-1,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError(
            f"clearwing timed out after {timeout}s: {' '.join(command)}",
            returncode=-1,
            stderr=str(exc.stderr or ""),
        ) from exc

    logger.info(
        "clearwing exited with code %d (stdout=%d bytes, stderr=%d bytes)",
        proc.returncode,
        len(proc.stdout),
        len(proc.stderr),
    )

    if proc.returncode != 0:
        raise ExecutionError(
            f"clearwing exited with code {proc.returncode}: {proc.stderr[:500]}",
            returncode=proc.returncode,
            stderr=proc.stderr,
        )

    # Parse stdout as JSON
    stdout = proc.stdout.strip()
    if not stdout:
        # Empty output is treated as zero findings
        logger.warning("clearwing produced empty stdout — treating as zero findings")
        raw_payload: dict = {"findings": []}
    else:
        try:
            raw_payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ExecutionError(
                f"clearwing stdout is not valid JSON: {exc}. "
                f"First 200 chars: {stdout[:200]!r}",
                returncode=proc.returncode,
                stderr=proc.stderr,
            ) from exc

    if not isinstance(raw_payload, dict):
        raise ExecutionError(
            f"clearwing stdout parsed to {type(raw_payload).__name__}, expected dict",
            returncode=proc.returncode,
        )

    # Unwrap Claude Code JSON envelope: {"type":"result","result":"<json string>"}
    if raw_payload.get("type") == "result" and isinstance(raw_payload.get("result"), str):
        inner = raw_payload["result"].strip()
        logger.info("unwrapping Claude Code JSON envelope (inner=%d chars)", len(inner))
        if inner:
            try:
                inner_payload = json.loads(inner)
                if isinstance(inner_payload, dict):
                    raw_payload = inner_payload
            except json.JSONDecodeError:
                # The result string wasn't JSON — try to extract JSON from it
                # Claude sometimes wraps JSON in markdown code blocks
                import re
                json_match = re.search(r'\{[\s\S]*"findings"[\s\S]*\}', inner)
                if json_match:
                    try:
                        raw_payload = json.loads(json_match.group())
                    except json.JSONDecodeError:
                        logger.warning("could not parse inner result as JSON, using envelope as-is")
                else:
                    logger.warning("inner result is not JSON and no findings block found")
                    raw_payload = {"findings": [], "_raw_response": inner}
        else:
            raw_payload = {"findings": []}

    # Ensure findings key exists
    if "findings" not in raw_payload:
        raw_payload["findings"] = []

    return ExecutionResult(
        raw_payload=raw_payload,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        command=command,
    )
