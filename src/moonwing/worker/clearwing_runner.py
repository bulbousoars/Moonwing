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


def _get_prompt(job_family: str, source_ref: str, *, nmap_output: str = "") -> str:
    """Return the scanning prompt for the given job family."""
    try:
        family = JobFamily(job_family)
    except ValueError as exc:
        raise ClearwingCommandError(f"unsupported job family: {job_family!r}") from exc

    if family == JobFamily.NETWORK_SCAN:
        return _NETWORK_SCAN_PROMPT.format(source_ref=source_ref, nmap_output=nmap_output)
    return _SOURCE_HUNT_PROMPT.format(source_ref=source_ref)


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

    prompt = _get_prompt(job_family, source_ref, nmap_output=nmap_output)
    settings = Settings()

    if provider == "anthropic":
        binary = settings.claude_cli_binary
        # Network scans: analysis only (nmap already ran), 1 turn
        # Source hunts: needs tools to read code, more turns
        max_turns = "1" if family == JobFamily.NETWORK_SCAN else "10"
        cmd = [
            binary,
            "--print",       # non-interactive, print output to stdout
            "--output-format", "json",
            "--model", model,
            "--max-turns", max_turns,
            "-p", prompt,
        ]
        if family == JobFamily.SOURCE_HUNT:
            cmd.extend(["--allowedTools", "Bash,Read,Glob,Grep"])
        return cmd
    elif provider in ("openai", "openrouter"):
        binary = settings.codex_cli_binary
        return [
            binary,
            "exec",           # non-interactive subcommand
            "--json",         # JSONL output to stdout
            "-m", model,
            "--full-auto",
            "--skip-git-repo-check",
            "--ephemeral",
            prompt,
        ]
    elif provider == "google":
        binary = settings.gemini_cli_binary
        return [
            binary,
            "-p", prompt,       # non-interactive headless mode
            "-o", "json",       # JSON output
            "-m", model,
            "--yolo",           # auto-approve all actions
        ]
    elif provider == "ollama":
        # For ollama, use codex CLI pointed at local endpoint
        binary = settings.codex_cli_binary
        return [
            binary,
            "exec",
            "--json",
            "-m", model,
            "--local-provider", "ollama",
            "--full-auto",
            "--skip-git-repo-check",
            "--ephemeral",
            prompt,
        ]
    else:
        raise ClearwingCommandError(f"unsupported provider for CLI execution: {provider!r}")
