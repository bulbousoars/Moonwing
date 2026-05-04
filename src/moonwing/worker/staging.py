from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from moonwing.db.models import Artifact, Credential, Run, RuntimeProfileRecord, Target
from moonwing.worker.clearwing_runner import build_clearwing_command


class StagingError(RuntimeError):
    pass


@dataclass(frozen=True)
class StagedArtifact:
    artifact_id: UUID
    artifact_type: str
    object_key: str


@dataclass(frozen=True)
class StagedJob:
    command: list[str]
    artifacts: list[StagedArtifact]
    execution_snapshot: dict
    target_metadata: dict
    target_display_name: str
    credential_provider: str
    encrypted_api_key: str | None


def _source_ref(target: Target | None) -> str:
    if target is None:
        return ""
    metadata = target.source_metadata or {}
    return (
        str(metadata.get("address") or "").strip()
        or str(metadata.get("url") or "").strip()
        or target.display_name
    )


def _input_kind(target: Target | None) -> str:
    if target is None:
        return "repo"
    metadata = target.source_metadata or {}
    return str(metadata.get("input_kind") or target.target_type or "repo")


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
        raise StagingError(f"credential not found for run: {run_id}")

    profile = session.get(RuntimeProfileRecord, run.runtime_profile_id)
    if profile is None:
        raise StagingError(f"runtime profile not found for run: {run_id}")

    target = session.get(Target, run.target_id) if run.target_id else None
    source_ref = _source_ref(target)
    if not source_ref:
        raise StagingError(f"run {run_id} has no target source reference")

    command = build_clearwing_command(
        job_family=run.job_family,
        input_kind=_input_kind(target),
        source_ref=source_ref,
        provider=run.provider,
        model=run.model,
        clearwing_binary=clearwing_binary,
    )

    artifacts = [
        StagedArtifact(
            artifact_id=a.id,
            artifact_type=a.artifact_type,
            object_key=a.object_key,
        )
        for a in session.query(Artifact).filter(Artifact.target_id == run.target_id).all()
    ] if run.target_id else []

    target_metadata = dict(target.source_metadata or {}) if target else {}
    execution_snapshot = dict(run.execution_snapshot or {})
    execution_snapshot.update(
        {
            "target": {
                "id": str(target.id) if target else None,
                "display_name": target.display_name if target else "",
                "target_type": target.target_type if target else "",
                "metadata": target_metadata,
            },
            "credential": {
                "id": str(credential.id),
                "provider": credential.provider,
                "display_name": credential.display_name,
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
        }
    )

    return StagedJob(
        command=command,
        artifacts=artifacts,
        execution_snapshot=execution_snapshot,
        target_metadata=target_metadata,
        target_display_name=target.display_name if target else "",
        credential_provider=credential.provider,
        encrypted_api_key=credential.encrypted_api_key,
    )
