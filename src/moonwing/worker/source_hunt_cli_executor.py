"""CLI-backed staged source-hunt pipeline.

This is the CLI-mode counterpart to ``source_hunt_executor``.  CLI providers
do not expose Moonwing's structured tool-call protocol, so this executor keeps
the deterministic stages local (acquire, inventory, rank) and invokes the
selected CLI once per ranked file with a focused source-audit prompt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from moonwing.core.pipeline import StageContext
from moonwing.core.source_hunt.acquire import AcquireStage
from moonwing.core.source_hunt.inventory import FileEntry, InventoryStage
from moonwing.worker.clearwing_runner import append_operator_ai_instruction, build_ai_cli_command
from moonwing.worker.executor import execute_clearwing

logger = logging.getLogger("moonwing.worker.source_hunt_cli_executor")


@dataclass
class SourceHuntCliExecutionResult:
    raw_payload: dict
    provider: str
    model: str
    usage: dict | None = None
    pipeline_summary: dict | None = None


@dataclass(frozen=True)
class CliRankedFile:
    path: str
    concern: str
    reason: str


_CONCERN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("auth", "auth"),
    ("login", "auth"),
    ("session", "auth"),
    ("password", "auth"),
    ("permission", "authz"),
    ("role", "authz"),
    ("admin", "authz"),
    ("route", "injection"),
    ("controller", "injection"),
    ("handler", "injection"),
    ("dao", "injection"),
    ("query", "injection"),
    ("sql", "injection"),
    ("mongo", "injection"),
    ("url", "ssrf"),
    ("request", "ssrf"),
    ("fetch", "ssrf"),
    ("http", "ssrf"),
    ("path", "path_traversal"),
    ("file", "path_traversal"),
    ("upload", "path_traversal"),
    ("exec", "command_exec"),
    ("shell", "command_exec"),
    ("crypto", "crypto"),
    ("secret", "secrets"),
    ("config", "secrets"),
    ("env", "secrets"),
    ("package", "supply_chain"),
)


def _score_file(entry: FileEntry) -> tuple[int, str]:
    rel = entry.relpath.lower()
    score = 0
    concern = "other"
    for token, label in _CONCERN_PATTERNS:
        if token in rel:
            score += 10
            if concern == "other":
                concern = label
    if entry.extension in {".py", ".js", ".ts", ".go", ".java", ".rb", ".php"}:
        score += 3
    if entry.relpath.lower().endswith(("package.json", "requirements.txt", "pyproject.toml")):
        score += 5
    if "/test" in rel or rel.startswith("test"):
        score -= 6
    return score, concern


def _rank_files(files: list[FileEntry], top_n: int) -> list[CliRankedFile]:
    ranked: list[tuple[int, FileEntry, str]] = []
    for entry in files:
        if not entry.is_text:
            continue
        score, concern = _score_file(entry)
        if score <= 0:
            continue
        ranked.append((score, entry, concern))
    ranked.sort(key=lambda item: (-item[0], item[1].relpath))
    return [
        CliRankedFile(
            path=entry.relpath,
            concern=concern,
            reason=f"filename/content category matched {concern}",
        )
        for _, entry, concern in ranked[:top_n]
    ]


def _hunt_prompt(*, ranked: CliRankedFile, ai_instruction: str | None = None) -> str:
    prompt = f"""\
You are auditing one source file in the current repository for security defects.

Focus file: {ranked.path}
Concern label: {ranked.concern}
Ranking reason: {ranked.reason}

Read the focus file and nearby references as needed. Return JSON only:

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
    "evidence": ["<file:line>", "..."]
  }}
]}}

If there is no material security issue in this file, return {{"findings": []}}.
Only output the JSON object as your final answer.
"""
    return append_operator_ai_instruction(prompt, ai_instruction)


def _emit(ctx: StageContext, kind: str, payload: dict[str, Any]) -> None:
    ctx.emit(kind, payload)


def _stage_result(name: str, outcome: str, error: str | None = None) -> dict[str, Any]:
    return {"name": name, "outcome": outcome, "error": error}


def execute_source_hunt_cli_pipeline(
    *,
    session: Session | None,
    run,
    workdir: str,
    source_ref: str,
    input_kind: str,
    profile_settings: dict[str, Any] | None = None,
    on_stage_event: Callable[[str, dict[str, Any]], None] | None = None,
    on_hunter_event: Callable[[str, dict[str, Any]], None] | None = None,
    ai_instruction: str | None = None,
    timeout_seconds: int = 1200,
    env: dict[str, str] | None = None,
) -> SourceHuntCliExecutionResult:
    profile_settings = profile_settings or {}
    agentic = profile_settings.get("agentic") or {}
    top_n = int(agentic.get("top_n") or 12)
    hunter_max_files = int(agentic.get("hunter_max_files") or top_n)

    ctx = StageContext(
        run_id=run.id,
        workdir=workdir,
        extras={"input_kind": input_kind, "source_ref": source_ref},
        on_event=on_stage_event,
    )
    _emit(ctx, "pipeline_start", {"name": "source_hunt_cli", "stages": ["acquire", "inventory", "rank", "hunt"]})
    stage_results: list[dict[str, Any]] = []
    ranked_files: list[CliRankedFile] = []
    merged: list[dict[str, Any]] = []
    hunter_errors: list[dict[str, str]] = []
    halted_at: str | None = None
    error: str | None = None

    try:
        _emit(ctx, "stage_start", {"name": "acquire"})
        acquire = AcquireStage().run(ctx)
        ctx.put("acquire", acquire)
        result = _stage_result("acquire", "success")
        stage_results.append(result)
        _emit(ctx, "stage_end", result)

        _emit(ctx, "stage_start", {"name": "inventory"})
        inventory = InventoryStage().run(ctx)
        ctx.put("inventory", inventory)
        result = _stage_result("inventory", "success")
        stage_results.append(result)
        _emit(ctx, "stage_end", result)

        _emit(ctx, "stage_start", {"name": "rank"})
        ranked_files = _rank_files(inventory.files, top_n=top_n)
        result = _stage_result("rank", "success")
        stage_results.append(result)
        _emit(ctx, "stage_end", result)

        _emit(ctx, "stage_start", {"name": "hunt"})
        for ranked in ranked_files[:hunter_max_files]:
            if on_hunter_event:
                on_hunter_event("file_start", {"path": ranked.path, "concern": ranked.concern})
            command = build_ai_cli_command(
                provider=run.provider,
                model=run.model,
                prompt=_hunt_prompt(ranked=ranked, ai_instruction=ai_instruction),
                source_tools=True,
            )
            try:
                result_payload = execute_clearwing(
                    command=command,
                    env=env,
                    timeout=min(timeout_seconds, 300),
                    cwd=acquire.root,
                ).raw_payload
            except Exception as exc:  # pragma: no cover - defensive against CLI failures
                logger.exception("CLI hunter crashed on %s", ranked.path)
                error_text = str(exc)
                hunter_errors.append({"path": ranked.path, "error": error_text})
                if on_hunter_event:
                    on_hunter_event(
                        "file_end",
                        {
                            "path": ranked.path,
                            "ok": False,
                            "error": error_text,
                            "findings": 0,
                            "iterations": 1,
                        },
                    )
                continue
            findings = list(result_payload.get("findings") or [])
            for finding in findings:
                if isinstance(finding, dict):
                    finding.setdefault("source_file", ranked.path)
                    finding.setdefault("concern", ranked.concern)
                    merged.append(finding)
            if on_hunter_event:
                on_hunter_event(
                    "file_end",
                    {
                        "path": ranked.path,
                        "ok": True,
                        "findings": len(findings),
                        "iterations": 1,
                    },
                )
        result = _stage_result("hunt", "success")
        stage_results.append(result)
        _emit(ctx, "stage_end", result)
    except Exception as exc:
        failed_stage = "acquire"
        if stage_results:
            completed = {r["name"] for r in stage_results}
            for candidate in ("acquire", "inventory", "rank", "hunt"):
                if candidate not in completed:
                    failed_stage = candidate
                    break
        halted_at = failed_stage
        error = f"{type(exc).__name__}: {exc}"
        result = _stage_result(failed_stage, "failed", error)
        stage_results.append(result)
        _emit(ctx, "stage_end", result)

    ok = error is None
    _emit(ctx, "pipeline_end", {"ok": ok, "halted_at": halted_at, "stage_count": len(stage_results)})
    usage = {
        "pipeline_ok": ok,
        "halted_at": halted_at,
        "stages": stage_results,
        "ranked_count": len(ranked_files),
        "hunter_iterations": min(len(ranked_files), hunter_max_files) if ok else 0,
        "merged_findings": len(merged),
        "hunter_errors": hunter_errors,
    }
    if error:
        usage["error"] = error
    return SourceHuntCliExecutionResult(
        raw_payload={"findings": merged},
        provider=run.provider,
        model=run.model,
        usage=usage,
        pipeline_summary={
            "stage_results": stage_results,
            "per_file": [{"path": r.path, "concern": r.concern} for r in ranked_files[:hunter_max_files]],
        },
    )
