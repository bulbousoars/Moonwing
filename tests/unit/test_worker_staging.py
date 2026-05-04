"""Unit tests for worker bootstrap and Clearwing job staging (Task 8)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from moonwing.db.base import Base
from moonwing.db.models import Artifact, Credential, Finding, Run, RuntimeProfileRecord, Target, User
from moonwing.services.runs import transition_run_status
from moonwing.worker.staging import StagedJob, StagingError, stage_run
from moonwing.worker.tasks import InMemoryObjectStore, process_run


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    sess = sessionmaker(bind=engine, autoflush=False)()
    yield sess
    sess.close()


@pytest.fixture()
def object_store():
    return InMemoryObjectStore()


@pytest.fixture()
def seed_user(session: Session) -> User:
    user = User(id=uuid4(), email="test@moonwing.dev", display_name="Test User")
    session.add(user)
    session.commit()
    return user


@pytest.fixture()
def seed_credential(session: Session, seed_user: User) -> Credential:
    cred = Credential(
        id=uuid4(),
        owner_user_id=seed_user.id,
        scope="user",
        provider="openai",
        display_name="Test API Key",
        secret_ref="vault://openai/test-key",
    )
    session.add(cred)
    session.commit()
    return cred


@pytest.fixture()
def seed_runtime_profile(session: Session) -> RuntimeProfileRecord:
    profile = RuntimeProfileRecord(
        id=uuid4(),
        name="default-network",
        allow_exploits=False,
        settings={},
    )
    session.add(profile)
    session.commit()
    return profile


@pytest.fixture()
def seed_target(session: Session) -> Target:
    target = Target(
        id=uuid4(),
        target_type="network_host",
        display_name="192.168.1.215",
        source_metadata={"address": "192.168.1.215", "port_range": "1-1024"},
    )
    session.add(target)
    session.commit()
    return target


@pytest.fixture()
def seed_artifact(session: Session, seed_target: Target) -> Artifact:
    art = Artifact(
        id=uuid4(),
        target_id=seed_target.id,
        artifact_type="sbom",
        object_key="targets/sbom-001.json",
        provenance={
            "source_type": "downloaded_url",
            "source_location": "https://example.com/sbom.json",
            "format": "cyclonedx",
        },
    )
    session.add(art)
    session.commit()
    return art


def _make_queued_run(
    session: Session,
    *,
    user: User,
    credential: Credential,
    profile: RuntimeProfileRecord,
    target: Target | None = None,
    job_family: str = "network_scan",
    execution_snapshot: dict | None = None,
) -> Run:
    run = Run(
        id=uuid4(),
        job_family=job_family,
        status="queued",
        user_id=user.id,
        credential_id=credential.id,
        runtime_profile_id=profile.id,
        target_id=target.id if target else None,
        provider="openai",
        model="gpt-5.2",
        execution_snapshot=execution_snapshot or {},
    )
    session.add(run)
    session.commit()
    return run


# ---------------------------------------------------------------------------
# Tests — stage_run()
# ---------------------------------------------------------------------------

class TestStageRun:
    """Core staging logic tests."""

    def test_stage_run_transitions_to_staging(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile, seed_target,
    ):
        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=seed_target,
        )

        staged = stage_run(
            session=session, run_id=run.id, object_store=object_store,
        )

        # Run should now be in staging state (flushed, not committed)
        session.expire(run)
        assert run.status == "staging"
        assert isinstance(staged, StagedJob)

    def test_stage_run_resolves_target_metadata(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile, seed_target,
    ):
        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=seed_target,
        )

        staged = stage_run(
            session=session, run_id=run.id, object_store=object_store,
        )

        assert staged.target_display_name == "192.168.1.215"
        assert staged.target_metadata["address"] == "192.168.1.215"

    def test_stage_run_resolves_credential_ref(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile, seed_target,
    ):
        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=seed_target,
        )

        staged = stage_run(
            session=session, run_id=run.id, object_store=object_store,
        )

        assert staged.credential_ref == "vault://openai/test-key"

    def test_stage_run_resolves_artifacts_with_provenance(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile,
        seed_target, seed_artifact,
    ):
        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=seed_target,
        )

        staged = stage_run(
            session=session, run_id=run.id, object_store=object_store,
        )

        assert len(staged.artifacts) == 1
        art = staged.artifacts[0]
        assert art.artifact_id == seed_artifact.id
        assert art.artifact_type == "sbom"
        assert art.provenance["source_type"] == "downloaded_url"
        assert art.provenance["format"] == "cyclonedx"

    def test_stage_run_builds_cli_command_for_network_scan(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile, seed_target,
    ):
        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=seed_target,
            job_family="network_scan",
        )

        staged = stage_run(
            session=session, run_id=run.id, object_store=object_store,
        )

        # Credential provider is openai → codex CLI
        assert staged.command[0] == "codex"
        assert "--model" in staged.command

    def test_stage_run_builds_cli_command_for_source_hunt(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile,
    ):
        target = Target(
            id=uuid4(),
            target_type="repo",
            display_name="example/repo",
            source_metadata={"url": "https://github.com/example/repo.git"},
        )
        session.add(target)
        session.commit()

        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=target,
            job_family="source_hunt",
            execution_snapshot={"input_kind": "repo"},
        )

        staged = stage_run(
            session=session, run_id=run.id, object_store=object_store,
        )

        assert staged.command[0] == "codex"
        assert "https://github.com/example/repo.git" in staged.command[-1]

    def test_stage_run_uses_custom_clearwing_binary(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile, seed_target,
    ):
        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=seed_target,
        )

        staged = stage_run(
            session=session,
            run_id=run.id,
            object_store=object_store,
            clearwing_binary="/opt/clearwing/bin/clearwing",
        )

        assert staged.command[0] == "/opt/clearwing/bin/clearwing"

    def test_stage_run_records_staging_provenance(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile, seed_target,
    ):
        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=seed_target,
        )

        staged = stage_run(
            session=session, run_id=run.id, object_store=object_store,
        )

        prov = staged.execution_snapshot["staging_provenance"]
        assert prov["source_type"] == "staging_snapshot"
        assert f"run://{run.id}" == prov["source_location"]


class TestStageRunErrors:
    """Error handling in the staging phase."""

    def test_stage_run_raises_on_missing_run(self, session, object_store):
        with pytest.raises(StagingError, match="run not found"):
            stage_run(
                session=session, run_id=uuid4(), object_store=object_store,
            )

    def test_stage_run_raises_on_missing_credential(
        self, session, object_store, seed_user, seed_runtime_profile, seed_target,
    ):
        bogus_cred_id = uuid4()
        run = Run(
            id=uuid4(),
            job_family="network_scan",
            status="queued",
            user_id=seed_user.id,
            credential_id=bogus_cred_id,
            runtime_profile_id=seed_runtime_profile.id,
            target_id=seed_target.id,
            provider="openai",
            model="gpt-5.2",
            execution_snapshot={},
        )
        session.add(run)
        session.commit()

        with pytest.raises(StagingError, match="credential .* not found"):
            stage_run(
                session=session, run_id=run.id, object_store=object_store,
            )

    def test_stage_run_raises_on_missing_target(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile,
    ):
        bogus_target_id = uuid4()
        run = Run(
            id=uuid4(),
            job_family="network_scan",
            status="queued",
            user_id=seed_user.id,
            credential_id=seed_credential.id,
            runtime_profile_id=seed_runtime_profile.id,
            target_id=bogus_target_id,
            provider="openai",
            model="gpt-5.2",
            execution_snapshot={},
        )
        session.add(run)
        session.commit()

        with pytest.raises(StagingError, match="target .* not found"):
            stage_run(
                session=session, run_id=run.id, object_store=object_store,
            )

    def test_stage_run_raises_without_target_or_source_ref(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile,
    ):
        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=None,
            execution_snapshot={},
        )

        with pytest.raises(StagingError, match="no target and no source_ref"):
            stage_run(
                session=session, run_id=run.id, object_store=object_store,
            )

    def test_stage_run_works_without_target_when_source_ref_in_snapshot(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile,
    ):
        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=None,
            execution_snapshot={"source_ref": "192.168.1.100"},
        )

        staged = stage_run(
            session=session, run_id=run.id, object_store=object_store,
        )

        assert staged.command[0] == "codex"  # openai provider → codex
        assert staged.target_display_name is None
        assert staged.artifacts == []


# ---------------------------------------------------------------------------
# Tests — full pipeline integration (process_run through staging)
# ---------------------------------------------------------------------------

class TestProcessRunIntegration:
    """Integration tests showing a Run moving through the staging state."""

    def test_process_run_stages_and_completes(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile, seed_target,
    ):
        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=seed_target,
        )
        raw_payload = {"findings": [{"title": "Open port 22", "severity": "info"}]}

        staged_job = process_run(
            session=session,
            run_id=run.id,
            raw_payload=raw_payload,
            object_store=object_store,
        )

        session.expire(run)
        assert run.status == "completed"
        assert staged_job is not None
        assert staged_job.job_family == "network_scan"
        assert staged_job.credential_ref == "vault://openai/test-key"

    def test_process_run_persists_findings(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile, seed_target,
    ):
        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=seed_target,
        )
        raw_payload = {
            "findings": [
                {"title": "SQL Injection", "severity": "high", "evidence": ["artifact://log-1"]},
                {"title": "XSS", "severity": "medium"},
            ]
        }

        process_run(
            session=session,
            run_id=run.id,
            raw_payload=raw_payload,
            object_store=object_store,
        )

        findings = session.query(Finding).filter(Finding.run_id == run.id).all()
        assert len(findings) == 2
        titles = {f.title for f in findings}
        assert titles == {"SQL Injection", "XSS"}

    def test_process_run_persists_raw_output_artifact_with_provenance(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile,
        seed_target, seed_artifact,
    ):
        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=seed_target,
        )
        raw_payload = {"findings": [{"title": "test", "severity": "low"}]}

        process_run(
            session=session,
            run_id=run.id,
            raw_payload=raw_payload,
            object_store=object_store,
        )

        raw_art = (
            session.query(Artifact)
            .filter(Artifact.artifact_type == "raw_clearwing_output")
            .first()
        )
        assert raw_art is not None
        assert raw_art.provenance["source_type"] == "worker_raw_payload"
        assert raw_art.provenance["run_id"] == str(run.id)
        # Staged artifacts are recorded in provenance for traceability
        assert len(raw_art.provenance["staged_artifacts"]) == 1
        assert raw_art.provenance["staged_artifacts"][0]["artifact_type"] == "sbom"

    def test_process_run_moves_to_failed_on_normalization_error(
        self, session, object_store, seed_user, seed_credential, seed_runtime_profile, seed_target,
    ):
        run = _make_queued_run(
            session,
            user=seed_user,
            credential=seed_credential,
            profile=seed_runtime_profile,
            target=seed_target,
        )
        # Missing title triggers NormalizationError
        bad_payload = {"findings": [{"severity": "high"}]}

        with pytest.raises(Exception):
            process_run(
                session=session,
                run_id=run.id,
                raw_payload=bad_payload,
                object_store=object_store,
            )

        session.expire(run)
        assert run.status == "failed"


# ---------------------------------------------------------------------------
# Tests — worker bootstrap
# ---------------------------------------------------------------------------

class TestWorkerBootstrap:
    """Verify worker bootstrap initialises dependencies."""

    def test_bootstrap_returns_required_keys(self, monkeypatch):
        from unittest.mock import MagicMock
        from moonwing.worker import object_store as os_mod
        from moonwing.worker.main import bootstrap
        from moonwing.config import Settings

        # Mock MinioObjectStore to avoid needing a real MinIO server
        mock_store = MagicMock()
        monkeypatch.setattr(os_mod, "Minio", lambda *a, **kw: MagicMock())
        monkeypatch.setattr(
            "moonwing.worker.main.MinioObjectStore",
            lambda **kw: mock_store,
        )

        # Use SQLite to avoid requiring psycopg in unit tests
        settings = Settings(database_url="sqlite:///:memory:")
        env = bootstrap(settings)

        assert "settings" in env
        assert "session_factory" in env
        assert "object_store" in env
        assert "clearwing_binary" in env
        assert env["settings"] is settings
