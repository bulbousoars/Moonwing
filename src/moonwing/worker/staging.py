from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from moonwing.db.models import Artifact, Credential, Run, RuntimeProfileRecord, Target
from moonwing.services.ai_provider_probe import cli_binary_executable
from moonwing.worker.clearwing_runner import build_clearwing_command


class StagingError(RuntimeError):
    """Raised when a run cannot be staged for execution."""


@dataclass(frozen=True)
class StagedArtifact:
    artifact_id: UUID
    artifact_type: str
    object_key: str
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StagedJob:
    command: list[str]
    artifacts: list[StagedArtifact]
    execution_snapshot: dict
    target_metadata: dict
    target_display_name: str | None
    credential_provider: str
    encrypted_api_key: str | None
    credential_ref: str = ""
    job_family: str = ""


def _source_ref_from_target(target: Target | None) -> str:
    if target is None:
        return ""
    metadata = target.source_metadata or {}
    return (
        str(metadata.get("address") or "").strip()
        or str(metadata.get("url") or "").strip()
        or (target.display_name or "")
    )


def _input_kind(target: Target | None, snapshot: dict | None) -> str:
    snapshot = snapshot or {}
    metadata = (target.source_metadata if target else {}) or {}
    return str(
        metadata.get("input_kind")
        or snapshot.get("input_kind")
        or (target.target_type if target else None)
        or "repo"
    )


def _cli_binary_missing(path: str) -> bool:
    return not cli_binary_executable(path)


def stage_run(
    *,
    session: Session,
    run_id: UUID,
    object_store: object | None = None,
    clearwing_binary: str | None = None,
) -> StagedJob:
    run = session.get(Run, run_id)
    if run is None:
        raise StagingError(f"run not found: {run_id}")

    credential = session.get(Credential, run.credential_id)
    if credential is None:
        raise StagingError(
            f"credential {run.credential_id} not found for run {run_id}"
        )

    profile = session.get(RuntimeProfileRecord, run.runtime_profile_id)
    if profile is None:
        raise StagingError(
            f"runtime profile {run.runtime_profile_id} not found for run {run_id}"
        )

    target: Target | None = None
    if run.target_id:
        target = session.get(Target, run.target_id)
        if target is None:
            raise StagingError(
                f"target {run.target_id} not found for run {run_id}"
            )

    snapshot = dict(run.execution_snapshot or {})

    source_ref = _source_ref_from_target(target)
    if not source_ref:
        snapshot_ref = snapshot.get("source_ref")
        if snapshot_ref:
            source_ref = str(snapshot_ref).strip()
    if not source_ref:
        raise StagingError(
            f"run {run_id} has no target and no source_ref in execution_snapshot"
        )

    command = build_clearwing_command(
        job_family=run.job_family,
        input_kind=_input_kind(target, snapshot),
        source_ref=source_ref,
        provider=run.provider,
        model=run.model,
        clearwing_binary=clearwing_binary,
        ai_instruction=str(snapshot.get("ai_instruction") or ""),
    )

    if (run.execution_mode or "api") == "cli" and command:
        exe = command[0]
        if _cli_binary_missing(exe):
            raise StagingError(
                f"CLI scanner not found or not executable: {exe!r}. "
                "The default Moonwing Docker image does not include Claude, Codex, or Gemini CLIs. "
                "Use Execution mode **API** with a provider API key, install the CLI in the worker environment, "
                "or set MOONWING_CLAUDE_CLI_BINARY / MOONWING_CODEX_CLI_BINARY / MOONWING_GEMINI_CLI_BINARY to a mounted binary path."
            )

    artifacts: list[StagedArtifact] = []
    if run.target_id:
        for a in session.query(Artifact).filter(Artifact.target_id == run.target_id).all():
            artifacts.append(
                StagedArtifact(
                    artifact_id=a.id,
                    artifact_type=a.artifact_type,
                    object_key=a.object_key,
                    provenance=dict(a.provenance or {}),
                )
            )

    target_metadata = dict(target.source_metadata or {}) if target else {}

    snapshot.update(
        {
            "target": {
                "id": str(target.id) if target else None,
                "display_name": target.display_name if target else None,
                "target_type": target.target_type if target else None,
                "metadata": target_metadata,
            },
            "credential": {
                "id": str(credential.id),
                "provider": credential.provider,
                "display_name": credential.display_name,
                "secret_ref": credential.secret_ref,
            },
            "runtime_profile": {
                "id": str(profile.id),
                "name": profile.name,
                "allow_exploits": profile.allow_exploits,
                "settings": profile.settings or {},
            },
            "execution": {
                "mode": run.execution_mode,
                "provider": run.provider,
                "model": run.model,
                "command_preview": command[:5],
            },
            "staging_provenance": {
                "source_type": "staging_snapshot",
                "source_location": f"run://{run.id}",
                "staged_at": datetime.now(timezone.utc).isoformat(),
            },
        }
    )

    run.status = "staging"
    session.flush()

    return StagedJob(
        command=command,
        artifacts=artifacts,
        execution_snapshot=snapshot,
        target_metadata=target_metadata,
        target_display_name=target.display_name if target else None,
        credential_provider=credential.provider,
        credential_ref=credential.secret_ref,
        encrypted_api_key=credential.encrypted_api_key,
        job_family=run.job_family,
    )
