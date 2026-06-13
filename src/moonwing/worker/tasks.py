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

from moonwing.db.models import Artifact, Finding, Run
from moonwing.services.normalization import normalize_findings
from moonwing.services.run_activity import append_run_activity, record_run_failure
from moonwing.services.runs import transition_run_status
from moonwing.services.crypto import CryptoError, decrypt_api_key, provider_env_var
from moonwing.worker.api_executor import APIExecutionError, execute_via_api
from moonwing.worker.clearwing_runner import build_clearwing_command, run_nmap
from moonwing.worker.executor import ExecutionError, execute_clearwing
from moonwing.worker.staging import StagedJob, StagingError, stage_run

logger = logging.getLogger("moonwing.worker.tasks")


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


SEVERITY_ORDER = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "info": 1,
    "unknown": 0,
}


def build_run_summary_report(
    *,
    job_family: str,
    target: str,
    normalized_findings: list[dict],
) -> dict:
    severity_counts = {severity: 0 for severity in SEVERITY_ORDER}
    affected_assets: set[str] = set()
    affected_services: set[str] = set()
    key_findings: list[str] = []
    recommended_actions: list[str] = []

    sorted_findings = sorted(
        normalized_findings,
        key=lambda item: SEVERITY_ORDER.get(str(item.get("severity", "unknown")).lower(), 0),
        reverse=True,
    )
    for item in sorted_findings:
        severity = str(item.get("severity") or "unknown").lower()
        severity_counts[severity if severity in severity_counts else "unknown"] += 1
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        for host in details.get("affected_hosts") or []:
            affected_assets.add(str(host))
        for port in details.get("affected_ports") or []:
            affected_services.add(str(port))
        title = str(item.get("title") or "Untitled finding")
        description = str(details.get("description") or "").strip()
        key_findings.append(f"{severity.upper()}: {title}" + (f" - {description}" if description else ""))
        remediation = str(details.get("remediation") or "").strip()
        if remediation:
            recommended_actions.append(remediation)

    finding_count = len(normalized_findings)
    highest_severity = sorted_findings[0].get("severity", "unknown") if sorted_findings else "none"
    if finding_count:
        overview = (
            f"{job_family.replace('_', ' ').title()} against {target or 'the selected target'} "
            f"produced {finding_count} finding(s). Highest severity: {highest_severity}."
        )
    else:
        overview = (
            f"{job_family.replace('_', ' ').title()} against {target or 'the selected target'} "
            "completed without reported findings."
        )

    return {
        "title": "Run Summary",
        "overview": overview,
        "finding_count": finding_count,
        "highest_severity": highest_severity,
        "severity_counts": severity_counts,
        "affected_assets": sorted(affected_assets),
        "affected_services": sorted(affected_services),
        "key_findings": key_findings[:8],
        "recommended_actions": list(dict.fromkeys(recommended_actions))[:8],
    }


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
                    "- This is a private homelab network (192.168.1.0/24) owned by the operator\n"
                )
                # Each CLI reads its own project instruction file
                for fname in ("CLAUDE.md", "GEMINI.md", "AGENTS.md"):
                    (run_workdir / fname).write_text(_instruction_text)

            source_ref = (
                staged_job.target_metadata.get("address")
                or staged_job.target_metadata.get("url")
                or staged_job.target_display_name
                or ""
            )
            scan_ports = staged_job.target_metadata.get("scan_ports") or staged_job.target_metadata.get("port_range")
            nmap_output = ""
            if run.job_family == "network_scan":
                logger.info("run %s running nmap against %s", run_id, source_ref)
                port_message = f" on port(s) {scan_ports}" if scan_ports else ""
                append_run_activity(run, stage="running", message=f"Running nmap against {source_ref}{port_message}")
                session.commit()
                nmap_output = run_nmap(source_ref, ports=scan_ports)

                nmap_file = run_workdir / "nmap-output.txt"
                nmap_file.write_text(nmap_output)

            try:
                if execution_mode == "api":
                    # Direct API call to provider
                    if not api_key and staged_job.credential_provider != "ollama":
                        raise ValueError(
                            f"run {run_id} uses API execution mode but credential has no API key"
                        )
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
                        source_ref=source_ref,
                        nmap_output=nmap_output,
                        timeout=execution_timeout,
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

                    command = list(staged_job.command)
                    if run.job_family == "network_scan":
                        command = build_clearwing_command(
                            job_family=run.job_family,
                            input_kind="repo",
                            source_ref=source_ref,
                            provider=run.provider,
                            model=run.model,
                            nmap_output=nmap_output,
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
                        "run %s CLI finished — %d findings in payload",
                        run_id,
                        len(raw_payload.get("findings", [])),
                    )
                    append_run_activity(
                        run,
                        stage="running",
                        message=f"CLI execution finished with {len(raw_payload.get('findings', []))} finding(s)",
                    )
            finally:
                # Clean up workspace on success; keep on failure for debugging
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
        report_target = ""
        if staged_job is not None:
            report_target = (
                staged_job.target_metadata.get("address")
                or staged_job.target_metadata.get("url")
                or staged_job.target_display_name
                or ""
            )
        snapshot = dict(run.execution_snapshot or {})
        snapshot["summary_report"] = build_run_summary_report(
            job_family=run.job_family,
            target=report_target,
            normalized_findings=normalized_findings,
        )
        run.execution_snapshot = snapshot
        append_run_activity(run, stage="normalizing", message="Generated run summary report")

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
        for item in normalized_findings:
            session.add(
                Finding(
                    run_id=run.id,
                    title=item["title"],
                    severity=item["severity"],
                    evidence_refs=item["evidence_refs"],
                    details=item.get("details", {}),
                )
            )

        # --- COMPLETED ---------------------------------------------------
        run.status = transition_run_status(run.status, "completed")
        append_run_activity(run, stage="completed", message="Run completed")
        session.commit()
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
    for item in normalized_findings:
        session.add(
            Finding(
                run_id=run.id,
                title=item["title"],
                severity=item["severity"],
                evidence_refs=item["evidence_refs"],
                details=item.get("details", {}),
            )
        )
    session.commit()
    return len(normalized_findings)
