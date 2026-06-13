"""Build CLI commands for security scan execution.

Instead of a monolithic ``clearwing`` binary, Moonwing delegates to
locally-installed AI CLI tools:

- **claude** (Claude Code CLI) — for Anthropic models
- **codex** (OpenAI Codex CLI) — for OpenAI models

Each CLI receives a structured security-scanning prompt and outputs
JSON findings to stdout.  The prompt varies by job family
(``network_scan`` vs ``source_hunt``).
"""

from __future__ import annotations

import logging
import subprocess

from moonwing.config import Settings
from moonwing.core.job_types import JobFamily, SourceInputKind

logger = logging.getLogger("moonwing.worker.clearwing_runner")


class ClearwingCommandError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Prompt templates — instruct the AI CLI to perform a security scan and
# return structured JSON findings.
# ---------------------------------------------------------------------------

_NETWORK_SCAN_PROMPT = """\
Analyze the following nmap scan results and output security findings as JSON.

nmap output:
{nmap_output}

For each open port or service found, create a finding. Use severity "info" for \
open ports, "low" for outdated versions, "medium" for known vulnerabilities, \
"high" for exploitable services, "critical" for unauthenticated admin interfaces.

Output format:
{{"findings": [
  {{
    "title": "<finding title>",
    "severity": "<critical|high|medium|low|info>",
    "product": "<affected product or service, e.g. MinIO API, Portainer Agent>",
    "affected_hosts": ["<host or IP>", "..."],
    "affected_ports": ["<port/protocol>", "..."],
    "description": "<what was found>",
    "impact": "<why it matters>",
    "remediation": "<specific fix>",
    "confidence": "<low|medium|high>",
    "references": ["<CVE, vendor doc, or relevant URL>", "..."],
    "evidence": ["<evidence>", ...]
  }},
  ...
]}}

Only output the JSON object, no other text.\
"""

_SOURCE_HUNT_PROMPT = """\
Analyze the source code at {source_ref} for security vulnerabilities. \
Look for injection flaws, hardcoded secrets, auth bypasses, path traversal, \
SSRF, and insecure patterns.

Output results as JSON:

{{"findings": [
  {{
    "title": "<finding title>",
    "severity": "<critical|high|medium|low|info>",
    "product": "<affected component or package>",
    "affected_hosts": [],
    "affected_ports": [],
    "description": "<what was found>",
    "impact": "<why it matters>",
    "remediation": "<specific fix>",
    "confidence": "<low|medium|high>",
    "references": ["<CVE, CWE, docs, or relevant URL>", "..."],
    "evidence": ["<file:line or description>", ...]
  }},
  ...
]}}

Only output the JSON object as your final answer, no other text.\
"""

DEFAULT_AI_INSTRUCTION_MAX_CHARS = 16_000


def append_operator_ai_instruction(
    prompt: str,
    instruction: str | None,
    *,
    max_chars: int = DEFAULT_AI_INSTRUCTION_MAX_CHARS,
) -> str:
    """Append free-form operator notes to the scanning prompt (API + CLI)."""
    if instruction is None:
        return prompt
    text = instruction.strip()
    if not text:
        return prompt
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…(truncated)"
    return (
        f"{prompt}\n\n"
        "## Additional instructions from the operator\n"
        "Prioritize the following when analyzing and reporting findings.\n\n"
        f"{text}\n"
    )


def _get_prompt(job_family: str, source_ref: str, *, nmap_output: str = "") -> str:
    """Return the scanning prompt for the given job family."""
    try:
        family = JobFamily(job_family)
    except ValueError as exc:
        raise ClearwingCommandError(f"unsupported job family: {job_family!r}") from exc

    if family == JobFamily.NETWORK_SCAN:
        return _NETWORK_SCAN_PROMPT.format(source_ref=source_ref, nmap_output=nmap_output)
    return _SOURCE_HUNT_PROMPT.format(source_ref=source_ref)


def build_ai_cli_command(
    *,
    provider: str,
    model: str,
    prompt: str,
    source_tools: bool = False,
) -> list[str]:
    """Build a provider CLI command for an already-rendered prompt."""
    settings = Settings()
    if provider == "anthropic":
        cmd = [
            settings.claude_cli_binary,
            "--print",
            "--output-format",
            "json",
            "--model",
            model,
            "--max-turns",
            "10" if source_tools else "1",
            "-p",
            prompt,
        ]
        if source_tools:
            cmd.extend(["--allowedTools", "Bash,Read,Glob,Grep"])
        return cmd
    if provider in ("openai", "openrouter"):
        return [
            settings.codex_cli_binary,
            "exec",
            "--json",
            "-m",
            model,
            "--full-auto",
            "--skip-git-repo-check",
            "--ephemeral",
            prompt,
        ]
    if provider == "google":
        return [
            settings.gemini_cli_binary,
            "-p",
            prompt,
            "-o",
            "json",
            "-m",
            model,
            "--yolo",
        ]
    if provider == "ollama":
        return [
            settings.codex_cli_binary,
            "exec",
            "--json",
            "-m",
            model,
            "--local-provider",
            "ollama",
            "--full-auto",
            "--skip-git-repo-check",
            "--ephemeral",
            prompt,
        ]
    raise ClearwingCommandError(f"unsupported provider for CLI execution: {provider!r}")


def run_nmap(target: str, *, ports: str | None = None, timeout: int = 300) -> str:
    """Run nmap against a target and return the raw text output.

    This is called directly by the worker — not through Claude — to avoid
    the CLI's safety refusals on network scanning prompts.
    """
    cmd = ["nmap", "-sV", "-sC"]
    if ports:
        cmd.extend(["-p", ports])
    else:
        cmd.extend(["--top-ports", "1000"])
    cmd.extend(["-T4", target])
    logger.info("running nmap: %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        logger.info("nmap finished (code=%d, %d bytes output)", proc.returncode, len(proc.stdout))
        return proc.stdout
    except FileNotFoundError:
        logger.warning("nmap not found — returning empty output")
        return "(nmap not installed)"
    except subprocess.TimeoutExpired:
        logger.warning("nmap timed out after %ds", timeout)
        return "(nmap timed out)"


def build_clearwing_command(
    job_family: str,
    input_kind: str,
    source_ref: str,
    *,
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-6",
    clearwing_binary: str | None = None,
    nmap_output: str = "",
    ai_instruction: str = "",
) -> list[str]:
    """Build a CLI command list for the appropriate AI tool.

    Parameters
    ----------
    provider:
        Which provider to use — determines which CLI binary is invoked.
        ``"anthropic"`` → ``claude``, ``"openai"`` → ``codex``.
    model:
        The model identifier passed to the CLI tool.
    clearwing_binary:
        Legacy override — if set, falls back to the old clearwing binary.
    """
    if not source_ref:
        raise ClearwingCommandError("source_ref is required")

    # Validate job family
    try:
        family = JobFamily(job_family)
    except ValueError as exc:
        raise ClearwingCommandError(f"unsupported job family: {job_family!r}") from exc

    # Validate input_kind for source hunts
    if family == JobFamily.SOURCE_HUNT:
        try:
            SourceInputKind(input_kind)
        except ValueError as exc:
            raise ClearwingCommandError(f"unsupported source input kind: {input_kind!r}") from exc

    # Legacy clearwing binary override
    if clearwing_binary:
        if family == JobFamily.NETWORK_SCAN:
            return [clearwing_binary, "scan", source_ref]
        return [clearwing_binary, "sourcehunt", source_ref]

    prompt = append_operator_ai_instruction(
        _get_prompt(job_family, source_ref, nmap_output=nmap_output),
        ai_instruction or None,
    )
    return build_ai_cli_command(
        provider=provider,
        model=model,
        prompt=prompt,
        source_tools=family == JobFamily.SOURCE_HUNT,
    )
