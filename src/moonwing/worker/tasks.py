"""Worker task handlers for staged run execution."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from moonwing.db.models import Artifact, Finding, Run, Target
from moonwing.services.kg import record_finding_provenance
from moonwing.services.normalization import normalize_findings
from moonwing.services.run_activity import append_run_activity, record_run_failure
from moonwing.services.runs import transition_run_status
from moonwing.services.crypto import CryptoError, decrypt_api_key, provider_env_var
from moonwing.worker.api_executor import APIExecutionError, execute_via_api
from moonwing.worker.clearwing_runner import build_clearwing_command, run_nmap
from moonwing.worker.executor import ExecutionError, execute_clearwing
from moonwing.worker.react_executor import execute_network_react
from moonwing.worker.source_hunt_cli_executor import execute_source_hunt_cli_pipeline
from moonwing.worker.source_hunt_executor import execute_source_hunt_pipeline
from moonwing.worker.staging import StagedJob, StagingError, stage_run

logger = logging.getLogger("moonwing.worker.tasks")


def _snapshot_ai_instruction(snapshot: dict | None) -> str | None:
    """Return trimmed operator AI notes from staging snapshot, if any."""
    if not snapshot:
        return None
    raw = snapshot.get("ai_instruction")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _short_args(args: dict | None, limit: int = 120) -> str:
    """Compact one-line preview of tool arguments for activity log lines."""
    if not args:
        return ""
    try:
        text = json.dumps(args, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        text = str(args)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _runtime_profile_settings(snapshot: dict | None) -> dict:
    """Pull runtime-profile settings out of the staging snapshot, if present."""
    if not snapshot:
        return {}
    profile = snapshot.get("runtime_profile") or {}
    settings = profile.get("settings") or {}
    return settings if isinstance(settings, dict) else {}


def _is_agentic_network_run(job_family: str, profile_settings: dict) -> bool:
    return (
        job_family == "network_scan"
        and bool(profile_settings.get("agentic_mode"))
    )


def _is_agentic_source_hunt_run(job_family: str, profile_settings: dict) -> bool:
    return (
        job_family == "source_hunt"
        and bool(profile_settings.get("agentic_mode"))
    )


def _target_address(session: Session, run: Run) -> str | None:
    """Pull the network address from a run's target, if available."""
    if run.target_id is None:
        return None
    target = session.get(Target, run.target_id)
    if target is None:
        return None
    meta = target.source_metadata or {}
    addr = meta.get("address") or meta.get("host") or meta.get("ip")
    if isinstance(addr, str) and addr.strip():
        return addr.strip()
    return None


def _target_source_ref(session: Session, run: Run) -> str | None:
    if run.target_id is None:
        return None
    target = session.get(Target, run.target_id)
    if target is None:
        return None
    meta = target.source_metadata or {}
    ref = meta.get("url") or meta.get("path") or meta.get("address") or target.display_name
    if isinstance(ref, str) and ref.strip():
        return ref.strip()
    return None


def _persist_finding_with_kg(
    session: Session,
    *,
    run: Run,
    item: dict,
    target_address: str | None,
    target_source_ref: str | None = None,
) -> Finding:
    """Insert one Finding row and mirror it into the knowledge graph.

    KG hooks are wrapped in a try/except so a graph-side problem never
    fails the run — findings are the system of record, KG is derived.
    """
    finding = Finding(
        run_id=run.id,
        title=item["title"],
        severity=item["severity"],
        evidence_refs=item["evidence_refs"],
        details=item.get("details", {}),
    )
    session.add(finding)
    session.flush()  # populate finding.id for KG provenance

    details = item.get("details", {}) or {}
    affected_hosts = details.get("affected_hosts") or []
    refs = details.get("references") or []
    cve_refs = [r for r in refs if isinstance(r, str) and r.upper().startswith("CVE-")]
    source_file = details.get("source_file")
    repo_ref = details.get("repo_ref") or target_source_ref

    try:
        record_finding_provenance(
            session,
            finding_id=finding.id,
            title=finding.title,
            severity=finding.severity,
            run_id=run.id,
            target_address=target_address,
            affected_hosts=affected_hosts if isinstance(affected_hosts, list) else None,
            cve_refs=cve_refs,
            repo_ref=repo_ref if isinstance(repo_ref, str) else None,
            source_file=source_file if isinstance(source_file, str) else None,
        )
    except Exception:
        logger.exception("kg record_finding_provenance failed for finding %s", finding.id)
    return finding


@dataclass(frozen=True)
class WorkerJob:
    run_id: UUID
    raw_payload: dict | None = None


class ObjectStore(Protocol):
    def put_json(self, *, object_key: str, payload: dict) -> None: ...
    def delete(self, *, object_key: str) -> None: ...


class InMemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, str] = {}

    def put_json(self, *, object_key: str, payload: dict) -> None:
        self.objects[object_key] = json.dumps(payload, sort_keys=True)

    def delete(self, *, object_key: str) -> None:
        self.objects.pop(object_key, None)


def stage_clearwing_command(
    *,
    job_family: str,
    input_kind: str,
    source_ref: str,
    clearwing_binary: str | None = None,
) -> list[str]:
    return build_clearwing_command(
        job_family=job_family,
        input_kind=input_kind,
        source_ref=source_ref,
        clearwing_binary=clearwing_binary,
    )


def enqueue_run(*, queue: Queue, run_id: UUID, raw_payload: dict | None = None) -> None:
    queue.put(WorkerJob(run_id=run_id, raw_payload=raw_payload))


def process_next(
    *,
    queue: Queue,
    session: Session,
    object_store: ObjectStore | None = None,
) -> UUID | None:
    try:
        job = queue.get_nowait()
    except Empty:
        return None

    try:
        process_run(
            session=session,
            run_id=job.run_id,
            raw_payload=job.raw_payload,
            object_store=object_store,
        )
        return job.run_id
    finally:
        queue.task_done()


def process_run(
    *,
    session: Session,
    run_id: UUID,
    raw_payload: dict | None = None,
    object_store: ObjectStore | None = None,
    clearwing_binary: str | None = None,
    execution_timeout: int = 600,
    clearwing_env: dict[str, str] | None = None,
) -> StagedJob | None:
    """Execute a single run through the full pipeline.

    Pipeline: queued → staging → running → normalizing → completed.

    If ``raw_payload`` is provided it is used directly (useful for tests
    and manual replay).  Otherwise the staged Clearwing command is
    executed as a subprocess and its stdout is parsed as JSON.

    Returns the StagedJob on success (useful for tests), or None on
    error (the run is moved to FAILED).
    """
    run = session.get(Run, run_id)
    if run is None:
        raise ValueError(f"run not found: {run_id}")

    if run.status != "queued":
        raise ValueError(
            f"run {run_id} is in state {run.status!r}, expected 'queued' — "
            "refusing to process to prevent duplicate execution"
        )

    if object_store is None:
        raise ValueError("object_store is required to persist raw output artifacts")

    raw_object_key: str | None = None
    staged_job: StagedJob | None = None

    try:
        # --- STAGING: resolve target, credentials, artifacts -------------
        run.status = transition_run_status(run.status, "staging")
        append_run_activity(run, stage="staging", message="Preparing scan workspace and resolving inputs")
        session.commit()
        staged_job = stage_run(
            session=session,
            run_id=run_id,
            object_store=object_store,
            clearwing_binary=clearwing_binary,
        )
        # Persist the enriched execution snapshot back to the run
        run.execution_snapshot = staged_job.execution_snapshot
        session.commit()

        logger.info(
            "run %s staged — command=%s artifacts=%d",
            run_id,
            staged_job.command,
            len(staged_job.artifacts),
        )
        append_run_activity(
            run,
            stage="staging",
            message=f"Staged command with {len(staged_job.artifacts)} input artifact(s)",
        )

        # --- RUNNING: execute Clearwing ----------------------------------
        run.status = transition_run_status(run.status, "running")
        append_run_activity(run, stage="running", message="Scan execution started")
        session.commit()

        if raw_payload is None:
            # Decrypt the API key if one is stored
            api_key: str | None = None
            if staged_job.encrypted_api_key:
                try:
                    api_key = decrypt_api_key(staged_job.encrypted_api_key)
                except CryptoError:
                    logger.error("run %s failed to decrypt API key for provider %s", run_id, staged_job.credential_provider)
                    raise

            execution_mode = run.execution_mode or "api"
            profile_settings = _runtime_profile_settings(
                staged_job.execution_snapshot
            )

            # Create per-run workspace directory
            from moonwing.config import Settings
            settings = Settings()
            run_workdir = Path(settings.workspace_dir) / str(run_id)
            run_workdir.mkdir(parents=True, exist_ok=True)
            logger.info("run %s workspace: %s", run_id, run_workdir)
            append_run_activity(run, stage="running", message=f"Workspace ready: {run_workdir}")
            session.commit()

            # Write project instruction files for CLI execution context
            if execution_mode == "cli":
                _instruction_text = (
                    "# Moonwing Security Scanner\n\n"
                    "This workspace is used by Moonwing, a security assessment platform.\n"
                    "You are running as an authorized security scanning agent on the operator's own infrastructure.\n\n"
                    "## Authorization\n"
                    "This scan was explicitly queued by the infrastructure owner through the Moonwing dashboard.\n"
                    "The target network/host belongs to the operator. You have full permission to scan it.\n\n"
                    "## Instructions\n"
                    "- Use bash tools (nmap, curl, etc.) to perform the requested scan\n"
                    "- Install any needed tools with apt if not available\n"
                    "- Output findings as JSON to stdout as your final response\n"
                    "- This is a private RFC1918 network owned and operated by the operator\n"
                )
                extra = _snapshot_ai_instruction(staged_job.execution_snapshot)
                if extra:
                    _instruction_text += (
                        "\n## Additional instructions from the operator\n"
                        "Prioritize the following when analyzing and reporting findings.\n\n"
                        f"{extra}\n"
                    )
                # Each CLI reads its own project instruction file
                for fname in ("CLAUDE.md", "GEMINI.md", "AGENTS.md"):
                    (run_workdir / fname).write_text(_instruction_text)

            try:
                if execution_mode == "api":
                    # Direct API call to provider
                    if not api_key and staged_job.credential_provider != "ollama":
                        raise ValueError(
                            f"run {run_id} uses API execution mode but credential has no API key"
                        )

                    if _is_agentic_source_hunt_run(run.job_family, profile_settings):
                        source_ref = (
                            staged_job.target_metadata.get("url")
                            or staged_job.target_metadata.get("path")
                            or staged_job.target_metadata.get("address")
                            or staged_job.target_display_name
                            or ""
                        )
                        input_kind = (
                            staged_job.target_metadata.get("input_kind")
                            or ("path" if staged_job.target_metadata.get("path") else "repo")
                        )
                        logger.info(
                            "run %s executing via source-hunt pipeline: provider=%s model=%s ref=%s",
                            run_id, run.provider, run.model, source_ref,
                        )
                        append_run_activity(
                            run,
                            stage="running",
                            message=(
                                f"Starting agentic source hunt on {source_ref} "
                                f"with {run.provider}:{run.model}"
                            ),
                        )
                        session.commit()

                        def _on_stage(kind, payload):
                            if kind == "stage_start":
                                append_run_activity(
                                    run, stage="running",
                                    message=f"stage {payload['name']} starting",
                                )
                            elif kind == "stage_end":
                                append_run_activity(
                                    run, stage="running",
                                    message=(
                                        f"stage {payload['name']} "
                                        f"-> {payload['outcome']}"
                                        + (f" ({payload['error']})" if payload.get("error") else "")
                                    ),
                                )

                        def _on_hunter(kind, payload):
                            if kind == "file_start":
                                append_run_activity(
                                    run, stage="running",
                                    message=f"hunter starting {payload['path']} [{payload.get('concern')}]",
                                )
                            elif kind == "file_end":
                                outcome = "ok" if payload.get("ok") else f"err: {payload.get('error')}"
                                append_run_activity(
                                    run, stage="running",
                                    message=(
                                        f"hunter {payload['path']} -> {outcome}, "
                                        f"{payload.get('findings', 0)} findings, "
                                        f"{payload.get('iterations', 0)} iters"
                                    ),
                                )

                        def _on_hunter_note(note: str):
                            append_run_activity(
                                run, stage="running", message=f"note: {note[:200]}"
                            )

                        sh_result = execute_source_hunt_pipeline(
                            session=session,
                            run=run,
                            workdir=str(run_workdir),
                            source_ref=source_ref,
                            input_kind=input_kind,
                            api_key=api_key or "",
                            profile_settings=profile_settings,
                            on_stage_event=_on_stage,
                            on_hunter_event=_on_hunter,
                            on_hunter_note=_on_hunter_note,
                            ai_instruction=_snapshot_ai_instruction(
                                staged_job.execution_snapshot
                            ),
                            timeout_seconds=execution_timeout,
                        )
                        raw_payload = sh_result.raw_payload
                        logger.info(
                            "run %s source-hunt finished — %d findings, usage=%s",
                            run_id,
                            len(raw_payload.get("findings", [])),
                            sh_result.usage,
                        )
                        append_run_activity(
                            run,
                            stage="running",
                            message=(
                                f"Source hunt finished: "
                                f"{len(raw_payload.get('findings', []))} finding(s), "
                                f"{sh_result.usage.get('ranked_count', 0)} files ranked, "
                                f"{sh_result.usage.get('hunter_iterations', 0)} hunter iterations"
                            ),
                        )
                    elif _is_agentic_network_run(run.job_family, profile_settings):
                        target_address = (
                            staged_job.target_metadata.get("address")
                            or staged_job.target_metadata.get("url")
                            or staged_job.target_display_name
                            or ""
                        )
                        logger.info(
                            "run %s executing via ReAct agent: provider=%s model=%s target=%s",
                            run_id, run.provider, run.model, target_address,
                        )
                        append_run_activity(
                            run,
                            stage="running",
                            message=(
                                f"Starting agentic scan of {target_address} "
                                f"with {run.provider}:{run.model}"
                            ),
                        )
                        session.commit()

                        def _on_step(step):
                            append_run_activity(
                                run,
                                stage="running",
                                message=(
                                    f"iter {step.iteration}: "
                                    f"{(step.assistant_text or '').strip()[:200] or '(tool round)'}"
                                ),
                            )

                        def _on_tool(step, inv):
                            outcome = "ok" if inv.result.ok else f"error: {inv.result.error}"
                            append_run_activity(
                                run,
                                stage="running",
                                message=f"tool {inv.call.name}({_short_args(inv.call.arguments)}) → {outcome[:160]}",
                            )

                        def _on_note(note: str):
                            append_run_activity(
                                run, stage="running", message=f"note: {note[:200]}"
                            )

                        react_result = execute_network_react(
                            session=session,
                            run=run,
                            target_address=target_address,
                            api_key=api_key or "",
                            profile_settings=profile_settings,
                            on_step=_on_step,
                            on_tool_invocation=_on_tool,
                            on_note=_on_note,
                            ai_instruction=_snapshot_ai_instruction(
                                staged_job.execution_snapshot
                            ),
                            timeout_seconds=execution_timeout,
                        )
                        raw_payload = react_result.raw_payload
                        logger.info(
                            "run %s ReAct finished — %d findings, usage=%s",
                            run_id,
                            len(raw_payload.get("findings", [])),
                            react_result.usage,
                        )
                        append_run_activity(
                            run,
                            stage="running",
                            message=(
                                f"Agentic scan finished: "
                                f"{len(raw_payload.get('findings', []))} finding(s), "
                                f"{react_result.usage.get('iterations')} iterations, "
                                f"stop={react_result.usage.get('stop_reason')}"
                            ),
                        )
                    else:
                        logger.info("run %s executing via API: provider=%s model=%s", run_id, run.provider, run.model)
                        append_run_activity(
                            run,
                            stage="running",
                            message=f"Calling {run.provider} API with model {run.model}",
                        )
                        session.commit()
                        api_result = execute_via_api(
                            provider=run.provider,
                            model=run.model,
                            api_key=api_key or "",
                            job_family=run.job_family,
                            source_ref=staged_job.target_metadata.get("address")
                                or staged_job.target_metadata.get("url")
                                or staged_job.target_display_name
                                or "",
                            timeout=execution_timeout,
                            ai_instruction=_snapshot_ai_instruction(staged_job.execution_snapshot),
                        )
                        raw_payload = api_result.raw_payload
                        logger.info(
                            "run %s API call finished — %d findings, usage=%s",
                            run_id,
                            len(raw_payload.get("findings", [])),
                            api_result.usage,
                        )
                        append_run_activity(
                            run,
                            stage="running",
                            message=f"API execution finished with {len(raw_payload.get('findings', []))} finding(s)",
                        )
                else:
                    # CLI execution mode — invoke claude/codex binary
                    # CLI tools authenticate via their own stored credentials
                    # (claude login / codex auth login) — no API key needed
                    run_env = dict(clearwing_env) if clearwing_env else {}
                    if api_key:
                        env_var_name = provider_env_var(staged_job.credential_provider)
                        if env_var_name:
                            run_env[env_var_name] = api_key

                    if _is_agentic_source_hunt_run(run.job_family, profile_settings):
                        source_ref = (
                            staged_job.target_metadata.get("url")
                            or staged_job.target_metadata.get("path")
                            or staged_job.target_metadata.get("address")
                            or staged_job.target_display_name
                            or ""
                        )
                        input_kind = (
                            staged_job.target_metadata.get("input_kind")
                            or ("path" if staged_job.target_metadata.get("path") else "repo")
                        )
                        logger.info(
                            "run %s executing via CLI source-hunt pipeline: provider=%s model=%s ref=%s",
                            run_id,
                            run.provider,
                            run.model,
                            source_ref,
                        )
                        append_run_activity(
                            run,
                            stage="running",
                            message=(
                                f"Starting CLI agentic source hunt on {source_ref} "
                                f"with {run.provider}:{run.model}"
                            ),
                        )
                        session.commit()

                        def _on_cli_stage(kind, payload):
                            if kind == "stage_start":
                                append_run_activity(
                                    run,
                                    stage="running",
                                    message=f"stage {payload['name']} starting",
                                )
                            elif kind == "stage_end":
                                append_run_activity(
                                    run,
                                    stage="running",
                                    message=(
                                        f"stage {payload['name']} "
                                        f"-> {payload['outcome']}"
                                        + (f" ({payload['error']})" if payload.get("error") else "")
                                    ),
                                )

                        def _on_cli_hunter(kind, payload):
                            if kind == "file_start":
                                append_run_activity(
                                    run,
                                    stage="running",
                                    message=f"hunter starting {payload['path']} [{payload.get('concern')}]",
                                )
                            elif kind == "file_end":
                                outcome = "ok" if payload.get("ok") else f"err: {payload.get('error')}"
                                append_run_activity(
                                    run,
                                    stage="running",
                                    message=(
                                        f"hunter {payload['path']} -> {outcome}, "
                                        f"{payload.get('findings', 0)} findings, "
                                        f"{payload.get('iterations', 0)} iters"
                                    ),
                                )

                        sh_result = execute_source_hunt_cli_pipeline(
                            session=session,
                            run=run,
                            workdir=str(run_workdir),
                            source_ref=source_ref,
                            input_kind=input_kind,
                            profile_settings=profile_settings,
                            on_stage_event=_on_cli_stage,
                            on_hunter_event=_on_cli_hunter,
                            ai_instruction=_snapshot_ai_instruction(staged_job.execution_snapshot),
                            timeout_seconds=execution_timeout,
                            env=run_env or None,
                        )
                        raw_payload = sh_result.raw_payload
                        logger.info(
                            "run %s CLI source-hunt finished - %d findings, usage=%s",
                            run_id,
                            len(raw_payload.get("findings", [])),
                            sh_result.usage,
                        )
                        append_run_activity(
                            run,
                            stage="running",
                            message=(
                                f"CLI source hunt finished: "
                                f"{len(raw_payload.get('findings', []))} finding(s), "
                                f"{sh_result.usage.get('ranked_count', 0)} files ranked, "
                                f"{sh_result.usage.get('hunter_iterations', 0)} hunter iterations"
                            ),
                        )
                    elif _is_agentic_network_run(run.job_family, profile_settings):
                        target_address = (
                            staged_job.target_metadata.get("address")
                            or staged_job.target_metadata.get("url")
                            or staged_job.target_display_name
                            or ""
                        )
                        logger.info(
                            "run %s executing via CLI ReAct agent: provider=%s model=%s target=%s",
                            run_id, run.provider, run.model, target_address,
                        )
                        append_run_activity(
                            run,
                            stage="running",
                            message=(
                                f"Starting CLI agentic scan of {target_address} "
                                f"with {run.provider}:{run.model}"
                            ),
                        )
                        session.commit()

                        def _on_cli_net_step(step):
                            append_run_activity(
                                run,
                                stage="running",
                                message=(
                                    f"iter {step.iteration}: "
                                    f"{(step.assistant_text or '').strip()[:200] or '(tool round)'}"
                                ),
                            )

                        def _on_cli_net_tool(step, inv):
                            outcome = "ok" if inv.result.ok else f"error: {inv.result.error}"
                            append_run_activity(
                                run,
                                stage="running",
                                message=f"tool {inv.call.name}({_short_args(inv.call.arguments)}) → {outcome[:160]}",
                            )

                        def _on_cli_net_note(note: str):
                            append_run_activity(
                                run, stage="running", message=f"note: {note[:200]}"
                            )

                        react_result = execute_network_react(
                            session=session,
                            run=run,
                            target_address=target_address,
                            api_key="",
                            profile_settings=profile_settings,
                            on_step=_on_cli_net_step,
                            on_tool_invocation=_on_cli_net_tool,
                            on_note=_on_cli_net_note,
                            ai_instruction=_snapshot_ai_instruction(
                                staged_job.execution_snapshot
                            ),
                            timeout_seconds=execution_timeout,
                            cli_mode=True,
                            cli_env=run_env or None,
                            workdir=str(run_workdir),
                        )
                        raw_payload = react_result.raw_payload
                        logger.info(
                            "run %s CLI ReAct finished — %d findings, usage=%s",
                            run_id,
                            len(raw_payload.get("findings", [])),
                            react_result.usage,
                        )
                        append_run_activity(
                            run,
                            stage="running",
                            message=(
                                f"CLI agentic scan finished: "
                                f"{len(raw_payload.get('findings', []))} finding(s), "
                                f"{react_result.usage.get('iterations')} iterations, "
                                f"stop={react_result.usage.get('stop_reason')}"
                            ),
                        )
                    else:
                        # For network scans: run nmap first, then feed output to AI for analysis
                        command = list(staged_job.command)
                        if run.job_family == "network_scan":
                            source_ref = (
                                staged_job.target_metadata.get("address")
                                or staged_job.target_display_name
                                or ""
                            )
                            scan_ports = staged_job.target_metadata.get("scan_ports") or staged_job.target_metadata.get("port_range")
                            logger.info("run %s running nmap against %s", run_id, source_ref)
                            port_message = f" on port(s) {scan_ports}" if scan_ports else ""
                            append_run_activity(run, stage="running", message=f"Running nmap against {source_ref}{port_message}")
                            session.commit()
                            nmap_output = run_nmap(source_ref, ports=scan_ports)

                            # Save nmap output to workspace
                            nmap_file = run_workdir / "nmap-output.txt"
                            nmap_file.write_text(nmap_output)

                            # Rebuild command with nmap output in prompt
                            command = build_clearwing_command(
                                job_family=run.job_family,
                                input_kind="repo",
                                source_ref=source_ref,
                                provider=run.provider,
                                model=run.model,
                                nmap_output=nmap_output,
                                ai_instruction=_snapshot_ai_instruction(staged_job.execution_snapshot),
                            )

                        logger.info("run %s executing via CLI: %s", run_id, " ".join(command[:5]) + "...")
                        append_run_activity(run, stage="running", message=f"Executing CLI scanner via {command[0]}")
                        session.commit()
                        result = execute_clearwing(
                            command=command,
                            env=run_env or None,
                            timeout=execution_timeout,
                            cwd=str(run_workdir),
                        )
                        raw_payload = result.raw_payload
                        logger.info(
                            "run %s CLI finished - %d findings in payload",
                            run_id,
                            len(raw_payload.get("findings", [])),
                        )
                        append_run_activity(
                            run,
                            stage="running",
                            message=f"CLI execution finished with {len(raw_payload.get('findings', []))} finding(s)",
                        )
            finally:
                if run_workdir.exists():
                    try:
                        shutil.rmtree(run_workdir)
                        logger.info("run %s workspace cleaned up", run_id)
                        append_run_activity(run, stage="running", message="Workspace cleaned up")
                    except OSError:
                        logger.warning("run %s failed to clean workspace %s", run_id, run_workdir)
                        append_run_activity(run, stage="running", message="Workspace cleanup failed", level="warning")
        else:
            logger.info("run %s using provided raw_payload (skip execution)", run_id)
            append_run_activity(run, stage="running", message="Using provided raw payload; execution skipped")

        # --- NORMALIZING -------------------------------------------------
        run.status = transition_run_status(run.status, "normalizing")
        append_run_activity(run, stage="normalizing", message="Normalizing raw scan output")
        session.commit()

        normalized_findings = normalize_findings(raw_payload)
        append_run_activity(
            run,
            stage="normalizing",
            message=f"Normalized {len(normalized_findings)} finding(s)",
        )

        # Persist raw Clearwing output as an artifact with provenance
        raw_object_key = f"runs/{run.id}/raw-clearwing-output.json"
        object_store.put_json(object_key=raw_object_key, payload=raw_payload)

        session.add(
            Artifact(
                target_id=run.target_id,
                artifact_type="raw_clearwing_output",
                object_key=raw_object_key,
                provenance={
                    "source_type": "worker_raw_payload",
                    "run_id": str(run.id),
                    "staged_artifacts": [
                        {
                            "artifact_id": str(sa.artifact_id),
                            "artifact_type": sa.artifact_type,
                            "object_key": sa.object_key,
                        }
                        for sa in staged_job.artifacts
                    ],
                },
            )
        )
        target_address = _target_address(session, run)
        target_source_ref = _target_source_ref(session, run)
        for item in normalized_findings:
            _persist_finding_with_kg(
                session,
                run=run,
                item=item,
                target_address=target_address,
                target_source_ref=target_source_ref,
            )

        # --- COMPLETED ---------------------------------------------------
        run.status = transition_run_status(run.status, "completed")
        append_run_activity(run, stage="completed", message="Run completed")
        session.commit()
        try:
            from moonwing.services.siem import emit_run_terminal

            emit_run_terminal(
                run_id=run.id,
                status="completed",
                job_family=run.job_family,
                finding_count=len(normalized_findings),
            )
        except Exception:
            logger.debug("siem run emit skipped", exc_info=True)
        return staged_job

    except Exception as exc:
        logger.exception("run %s failed", run_id)
        session.rollback()
        failed_run = session.get(Run, run_id)
        if failed_run is not None and failed_run.status not in {
            "completed",
            "failed",
            "canceled",
        }:
            try:
                failed_run.status = transition_run_status(failed_run.status, "failed")
                record_run_failure(failed_run, exc)
                session.commit()
                try:
                    from moonwing.services.siem import emit_run_terminal

                    emit_run_terminal(
                        run_id=failed_run.id,
                        status="failed",
                        job_family=failed_run.job_family,
                        finding_count=None,
                    )
                except Exception:
                    logger.debug("siem run emit skipped", exc_info=True)
            except Exception:
                session.rollback()
        if raw_object_key is not None:
            try:
                object_store.delete(object_key=raw_object_key)
            except Exception:
                pass
        raise


def renormalize_run(
    *,
    session: Session,
    run_id: UUID,
    raw_payload: dict,
) -> int:
    """Re-normalize findings for a completed run idempotently.

    Deletes all existing findings for the run before inserting the new
    normalized set, preventing duplicates on retry.  Returns the count
    of findings after re-normalization.
    """
    run = session.get(Run, run_id)
    if run is None:
        raise ValueError(f"run not found: {run_id}")

    # Delete existing findings for this run
    existing = session.query(Finding).filter(Finding.run_id == run_id).all()
    for f in existing:
        session.delete(f)
    session.flush()

    # Re-normalize and insert
    normalized_findings = normalize_findings(raw_payload)
    target_address = _target_address(session, run)
    target_source_ref = _target_source_ref(session, run)
    for item in normalized_findings:
        _persist_finding_with_kg(
            session,
            run=run,
            item=item,
            target_address=target_address,
            target_source_ref=target_source_ref,
        )
    session.commit()
    return len(normalized_findings)
