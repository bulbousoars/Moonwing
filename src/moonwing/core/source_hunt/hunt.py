"""Stage 4 — per-file hunter ReAct loops.

For each ranked file, run a ReAct agent armed with read_file / glob_files /
grep_files / kg_query / meta_note. The agent's job is to read the file,
investigate cross-references it cares about, and emit findings JSON.
Findings from all files are merged into one payload for downstream
normalization.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from moonwing.core.llm import LLMAdapter
from moonwing.core.pipeline import StageContext, StageSkipped
from moonwing.core.react import Budget, ReActAgent, ReActStopReason, ReActTranscript
from moonwing.core.tools import (
    Capability,
    DEFAULT_REGISTRY,
    ToolContext,
    ToolRegistry,
)
from .rank import RankResult, RankedFile

logger = logging.getLogger("moonwing.source_hunt.hunt")


HUNTER_SYSTEM_PROMPT = """\
You are auditing one source file for security defects. You have tools to
read this file, read other files in the repo, glob, grep, and query an
internal knowledge graph. Operate in short cycles:

  1. Read the focus file end-to-end (in chunks if large).
  2. For every potential defect: confirm by cross-referencing callers,
     definitions, configuration, or the KG.
  3. Use meta_note to externalize hypotheses you want to revisit.
  4. When done, emit findings as JSON only — no prose.

Rules:
  * No invented references. Cite path + line for every claim.
  * Treat untested test files and obvious docs as out of scope.
  * Prefer fewer high-quality findings over many low-confidence ones.

Final answer schema (JSON only):
{"findings": [
  {
    "title": "<short title>",
    "severity": "<critical|high|medium|low|info>",
    "product": "<component / package, if known>",
    "affected_hosts": [],
    "affected_ports": [],
    "description": "<what was found>",
    "impact": "<why it matters>",
    "remediation": "<specific fix>",
    "confidence": "<low|medium|high>",
    "references": [],
    "evidence": ["<path:line>", "..."]
  }
]}
"""


HUNTER_USER_TEMPLATE = """\
Focus file: {path}
Concern label assigned by ranker: {concern}
Ranker reason: {reason}

Investigate this file. Read it in full, follow references as needed, and
emit JSON findings. If you find nothing material, return an empty list.
"""


# Tools the hunter can use. Sandbox-required tools are denied by default.
HUNTER_ALLOWED_TOOLS = (
    "read_file",
    "glob_files",
    "grep_files",
    "kg_query",
    "meta_note",
)


@dataclass
class FileHuntResult:
    path: str
    concern: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ""
    iterations: int = 0
    error: str | None = None


@dataclass
class HuntResult:
    per_file: list[FileHuntResult] = field(default_factory=list)
    merged_findings: list[dict[str, Any]] = field(default_factory=list)
    total_iterations: int = 0


def _parse_findings(text: str) -> list[dict[str, Any]]:
    cleaned = (text or "").strip()
    if not cleaned:
        return []
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
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    raw = parsed.get("findings") or []
    return [f for f in raw if isinstance(f, dict)]


@dataclass
class HuntStage:
    """Run a hunter ReAct per ranked file and merge their findings.

    Reads from ``ctx.extras``:
      * ``llm_adapter`` (required)
      * ``llm_model`` (required)
      * ``llm_api_key`` (required)
      * ``hunter_budget`` (optional Budget) — default: 8 iterations
      * ``hunter_registry`` (optional ToolRegistry) — defaults to global
      * ``hunter_max_files`` (optional int) — cap on ranked files audited
      * ``on_hunter_event`` (optional callable) — per-file lifecycle hook
    """

    name: str = "hunt"

    def run(self, ctx: StageContext) -> HuntResult:
        rank: RankResult | None = ctx.get("rank")
        if rank is None:
            raise RuntimeError("hunt stage requires 'rank' output in context")
        if not rank.ranked:
            raise StageSkipped("ranker selected no files — nothing to hunt")

        adapter: LLMAdapter | None = ctx.extras.get("llm_adapter")
        model = ctx.extras.get("llm_model")
        api_key = ctx.extras.get("llm_api_key")
        if adapter is None or not model:
            raise RuntimeError("hunt stage requires llm_adapter and llm_model in extras")

        registry: ToolRegistry = ctx.extras.get("hunter_registry") or DEFAULT_REGISTRY
        max_files = int(ctx.extras.get("hunter_max_files") or len(rank.ranked))
        hook: Callable[[str, dict[str, Any]], None] | None = ctx.extras.get(
            "on_hunter_event"
        )

        per_file: list[FileHuntResult] = []
        merged: list[dict[str, Any]] = []
        total_iterations = 0

        for ranked_file in rank.ranked[:max_files]:
            result = self._hunt_one_file(
                ranked_file,
                ctx=ctx,
                adapter=adapter,
                registry=registry,
                model=model,
                api_key=api_key or "",
                hook=hook,
            )
            per_file.append(result)
            merged.extend(result.findings)
            total_iterations += result.iterations

        return HuntResult(
            per_file=per_file,
            merged_findings=merged,
            total_iterations=total_iterations,
        )

    def _hunt_one_file(
        self,
        ranked: RankedFile,
        *,
        ctx: StageContext,
        adapter: LLMAdapter,
        registry: ToolRegistry,
        model: str,
        api_key: str,
        hook: Callable[[str, dict[str, Any]], None] | None,
    ) -> FileHuntResult:
        if hook:
            hook("file_start", {"path": ranked.path, "concern": ranked.concern})

        budget_template = ctx.extras.get("hunter_budget")
        if isinstance(budget_template, Budget):
            budget = Budget(
                max_iterations=budget_template.max_iterations,
                max_total_input_tokens=budget_template.max_total_input_tokens,
                max_wall_seconds=budget_template.max_wall_seconds,
            )
        else:
            budget = Budget(max_iterations=8, max_total_input_tokens=120_000, max_wall_seconds=180.0)

        agent = ReActAgent(adapter=adapter, registry=registry, budget=budget)
        tool_ctx = ToolContext(
            run_id=ctx.run_id,
            session=ctx.extras.get("db_session"),
            workdir=ctx.get("acquire").root if ctx.get("acquire") else ctx.workdir,
            timeout_seconds=int(ctx.extras.get("hunter_call_timeout") or 120),
            extras={"on_note": ctx.extras.get("on_hunter_note")} if ctx.extras.get("on_hunter_note") else {},
        )

        user_prompt = HUNTER_USER_TEMPLATE.format(
            path=ranked.path, concern=ranked.concern, reason=ranked.reason
        )

        try:
            transcript: ReActTranscript = agent.run(
                system_prompt=HUNTER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                model=model,
                api_key=api_key,
                ctx=tool_ctx,
                allowed_tools=list(HUNTER_ALLOWED_TOOLS),
                denied_capabilities={Capability.NEEDS_SANDBOX, Capability.NETWORK_EGRESS},
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("hunter crashed on %s", ranked.path)
            result = FileHuntResult(
                path=ranked.path, concern=ranked.concern, error=str(exc)
            )
            if hook:
                hook("file_end", {"path": ranked.path, "ok": False, "error": str(exc)})
            return result

        findings = _parse_findings(transcript.final_text)
        # Annotate every finding with the source file so merged output is traceable.
        for f in findings:
            f.setdefault("source_file", ranked.path)
            f.setdefault("concern", ranked.concern)

        result = FileHuntResult(
            path=ranked.path,
            concern=ranked.concern,
            findings=findings,
            stop_reason=transcript.stop_reason.value,
            iterations=budget.iterations_used,
            error=transcript.error,
        )
        if hook:
            hook(
                "file_end",
                {
                    "path": ranked.path,
                    "ok": transcript.stop_reason == ReActStopReason.FINAL_ANSWER,
                    "findings": len(findings),
                    "iterations": result.iterations,
                    "stop_reason": result.stop_reason,
                },
            )
        return result
