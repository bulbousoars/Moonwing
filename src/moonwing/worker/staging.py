"""Job staging: resolve all run dependencies before Clearwing execution.

Transitions a run from QUEUED → STAGING while retrieving targets,
credentials, and artifacts (with provenance) from the database and
object store, then packages everything into a StagedJob ready for
the Clearwing runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from moonwing.db.models import Artifact, Credential, Run, Target
from moonwing.services.artifacts import build_sbom_metadata
from moonwing.services.runs import RunStateError, transition_run_status
from moonwing.worker.clearwing_runner import build_clearwing_command

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from moonwing.worker.tasks import ObjectStore


class StagingError(RuntimeError):
    """Raised when staging prerequisites cannot be satisfied."""


@dataclass(frozen=True)
class StagedArtifact:
    """An artifact resolved during staging, with provenance preserved."""

    artifact_id: UUID
    artifact_type: str
    object_key: str
    provenance: dict


@dataclass(frozen=True)
class StagedJob:
    """Fully resolved payload ready for Clearwing execution."""

    run_id: UUID
    job_family: str
    command: list[str]
    credential_ref: str
    credential_provider: str
    encrypted_api_key: str | None
    target_display_name: str | None
    target_metadata: dict
    artifacts: list[StagedArtifact] = field(default_factory=list)
    execution_snapshot: dict = field(default_factory=dict)


def stage_run(
    *,
    session: Session,
    run_id: UUID,
    object_store: ObjectStore,
    clearwing_binary: str | None = None,
) -> StagedJob:
    """Transition a queued run to STAGING and resolve all dependencies.

    Returns a ``StagedJob`` containing everything the Clearwing runner
    needs.  On failure the caller is responsible for moving the run to
    FAILED (handled by ``process_run`` in tasks.py).
    """
    run: Run | None = session.get(Run, run_id)
    if run is None:
        raise StagingError(f"run not found: {run_id}")

    # --- state transition ------------------------------------------------
    run.status = transition_run_status(run.status, "staging")
    session.flush()

    # --- resolve credential ----------------------------------------------
    credential: Credential | None = session.get(Credential, run.credential_id)
    if credential is None:
        raise StagingError(
            f"credential {run.credential_id} referenced by run {run_id} not found"
        )

    # --- resolve target (optional) ---------------------------------------
    target_display_name: str | None = None
    target_metadata: dict = {}
    source_ref: str = ""

    if run.target_id is not None:
        target: Target | None = session.get(Target, run.target_id)
        if target is None:
            raise StagingError(
                f"target {run.target_id} referenced by run {run_id} not found"
            )
        target_display_name = target.display_name
        target_metadata = dict(target.source_metadata) if target.source_metadata else {}
        # source_ref is the primary identifier the runner needs — for a
        # network scan it's the host/IP, for source hunts it's a repo URL
        # or file path.  We fall back to the display_name.
        source_ref = target_metadata.get("address") or target_metadata.get("url") or target.display_name
    else:
        # Runs without a target must carry a source_ref in the snapshot
        source_ref = run.execution_snapshot.get("source_ref", "")
        if not source_ref:
            raise StagingError(
                f"run {run_id} has no target and no source_ref in execution_snapshot"
            )

    # --- resolve artifacts (SBOMs etc.) ----------------------------------
    staged_artifacts: list[StagedArtifact] = []
    if run.target_id is not None:
        artifacts = (
            session.query(Artifact)
            .filter(Artifact.target_id == run.target_id)
            .all()
        )
        for art in artifacts:
            staged_artifacts.append(
                StagedArtifact(
                    artifact_id=art.id,
                    artifact_type=art.artifact_type,
                    object_key=art.object_key,
                    provenance=dict(art.provenance) if art.provenance else {},
                )
            )

    # --- build provenance record for the staging event itself ------------
    staging_provenance = build_sbom_metadata(
        source_type="staging_snapshot",
        source_location=f"run://{run_id}",
        retrieved_at=None,
        format="internal",
    )
    execution_snapshot = dict(run.execution_snapshot) if run.execution_snapshot else {}
    execution_snapshot["staging_provenance"] = staging_provenance

    # --- determine input_kind for source hunts ---------------------------
    input_kind = execution_snapshot.get("input_kind", "repo")

    # --- build CLI command ------------------------------------------------
    command = build_clearwing_command(
        job_family=run.job_family,
        input_kind=input_kind,
        source_ref=source_ref,
        provider=run.provider or credential.provider,
        model=run.model or "claude-sonnet-4-6",
        clearwing_binary=clearwing_binary,
    )

    return StagedJob(
        run_id=run.id,
        job_family=run.job_family,
        command=command,
        credential_ref=credential.secret_ref,
        credential_provider=credential.provider,
        encrypted_api_key=credential.encrypted_api_key,
        target_display_name=target_display_name,
        target_metadata=target_metadata,
        artifacts=staged_artifacts,
        execution_snapshot=execution_snapshot,
    )
