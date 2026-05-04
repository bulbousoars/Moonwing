"""Integration tests for the full run execution flow.

Exercises: API → queued → staging → running → normalizing → completed,
including artifact provenance, finding persistence, and failure handling.
"""

from pathlib import Path
from queue import Queue
import sys

from alembic import command
from alembic.config import Config
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from moonwing.api.main import app
from moonwing.db.models import Artifact, Credential, Finding, Run, RuntimeProfileRecord, Target, User
from moonwing.services.normalization import NormalizationError
from moonwing.worker.main import drain_queue, poll_once
from moonwing.worker.tasks import InMemoryObjectStore, enqueue_run, process_next, process_run, renormalize_run


def _session_factory(tmp_path):
    database_path = tmp_path / "moonwing-test.db"
    database_url = f"sqlite:///{database_path}"

    alembic_config = Config(str(ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(ROOT / "alembic"))
    alembic_config.set_main_option("prepend_sys_path", str(SRC))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")

    engine = create_engine(database_url, future=True)

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _seed_run(session, *, source_metadata=None):
    user = User(email="worker@example.com", display_name="Worker User")
    session.add(user)
    session.flush()

    credential = Credential(
        owner_user_id=user.id,
        scope="user",
        provider="openai",
        display_name="Primary OpenAI",
        secret_ref="secret://credential/openai-primary",
    )
    runtime = RuntimeProfileRecord(name="default-network", settings={"depth": "standard"})
    target = Target(
        target_type="host",
        display_name="192.168.1.215",
        source_metadata=source_metadata or {"address": "192.168.1.215"},
    )
    session.add_all([credential, runtime, target])
    session.flush()

    run = Run(
        job_family="network_scan",
        status="queued",
        user_id=user.id,
        credential_id=credential.id,
        runtime_profile_id=runtime.id,
        target_id=target.id,
        provider="openai",
        model="gpt-5.2",
        execution_snapshot={"provider": "openai", "model": "gpt-5.2"},
    )
    session.add(run)
    session.commit()
    return run.id


def _seed_run_with_artifact(session):
    """Seed a run that also has a pre-existing artifact on the target."""
    user = User(email="artifact-worker@example.com", display_name="Artifact Worker")
    session.add(user)
    session.flush()

    credential = Credential(
        owner_user_id=user.id,
        scope="user",
        provider="anthropic",
        display_name="Test Anthropic Key",
        secret_ref="vault://anthropic/test-key",
    )
    runtime = RuntimeProfileRecord(name="artifact-profile", settings={})
    target = Target(
        target_type="repo",
        display_name="example/repo",
        source_metadata={"url": "https://github.com/example/repo.git"},
    )
    session.add_all([credential, runtime, target])
    session.flush()

    sbom = Artifact(
        target_id=target.id,
        artifact_type="sbom",
        object_key="targets/example-repo/sbom.json",
        provenance={
            "source_type": "downloaded_url",
            "source_location": "https://example.com/sbom.json",
            "format": "cyclonedx",
        },
    )
    session.add(sbom)
    session.flush()

    run = Run(
        job_family="source_hunt",
        status="queued",
        user_id=user.id,
        credential_id=credential.id,
        runtime_profile_id=runtime.id,
        target_id=target.id,
        provider="anthropic",
        model="sonnet-4.6",
        execution_snapshot={"input_kind": "repo"},
    )
    session.add(run)
    session.commit()
    return run.id, sbom.id


class WorkerHarness:
    def __init__(self, session):
        self.queue = Queue()
        self.object_store = InMemoryObjectStore()
        self.session = session

    def seed_run(self, *, status: str, job_family: str):
        run_id = _seed_run(self.session)
        run = self.session.get(Run, run_id)
        run.status = status
        run.job_family = job_family
        self.session.commit()
        return run_id

    def enqueue(self, run_id):
        enqueue_run(
            queue=self.queue,
            run_id=run_id,
            raw_payload={
                "findings": [
                    {
                        "title": "SQL injection",
                        "severity": "high",
                        "evidence": ["artifact://log-1"],
                    }
                ]
            },
        )

    def process(self):
        return process_next(queue=self.queue, session=self.session, object_store=self.object_store)

    def fetch_run(self, run_id):
        return self.session.get(Run, run_id)

    def fetch_findings(self, run_id):
        return self.session.execute(select(Finding).where(Finding.run_id == run_id)).scalars().all()

    def fetch_artifacts(self, run_id):
        return (
            self.session.execute(
                select(Artifact).where(Artifact.object_key == f"runs/{run_id}/raw-clearwing-output.json")
            )
            .scalars()
            .all()
        )


@pytest.fixture
def worker_harness(tmp_path):
    session_factory = _session_factory(tmp_path)
    return WorkerHarness(session_factory())


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------

def test_api_accepts_run_request(tmp_path):
    """POST /api/runs creates a queued run when entities exist in DB."""
    from moonwing.api.deps import get_db

    sf = _session_factory(tmp_path)
    session = sf()
    # Seed required entities
    user = User(email="api@test.com", display_name="API Test")
    session.add(user)
    session.flush()
    cred = Credential(owner_user_id=user.id, scope="user", provider="openai", display_name="K", secret_ref="vault://k")
    prof = RuntimeProfileRecord(name="api-prof", settings={})
    tgt = Target(target_type="network_host", display_name="10.0.0.1", source_metadata={"address": "10.0.0.1"})
    session.add_all([cred, prof, tgt])
    session.commit()
    ids = {"user": str(user.id), "cred": str(cred.id), "prof": str(prof.id), "tgt": str(tgt.id)}
    session.close()

    def _override_db():
        s = sf()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app)
        response = client.post(
            "/api/runs",
            json={
                "job_family": "network_scan",
                "target_id": ids["tgt"],
                "runtime_profile_id": ids["prof"],
                "credential_id": ids["cred"],
                "user_id": ids["user"],
                "provider": "openai",
                "model": "gpt-5.2",
            },
        )
        assert response.status_code == 201
        assert response.json()["status"] == "queued"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Worker harness — queued → completed flow
# ---------------------------------------------------------------------------

def test_worker_harness_completes_persisted_run(worker_harness):
    run_id = worker_harness.seed_run(status="queued", job_family="network_scan")
    worker_harness.enqueue(run_id)

    processed_run_id = worker_harness.process()

    run_record = worker_harness.fetch_run(run_id)
    findings = worker_harness.fetch_findings(run_id)
    artifacts = worker_harness.fetch_artifacts(run_id)

    assert processed_run_id == run_id
    assert run_record.status == "completed"
    assert len(findings) == 1
    assert findings[0].title == "SQL injection"
    assert findings[0].evidence_refs == ["artifact://log-1"]
    assert len(artifacts) == 1
    assert artifacts[0].artifact_type == "raw_clearwing_output"
    assert artifacts[0].object_key in worker_harness.object_store.objects


def test_worker_marks_run_failed_when_normalization_fails(worker_harness):
    run_id = worker_harness.seed_run(status="queued", job_family="network_scan")
    enqueue_run(
        queue=worker_harness.queue,
        run_id=run_id,
        raw_payload={"findings": [{"severity": "high"}]},
    )

    try:
        worker_harness.process()
    except NormalizationError:
        pass

    run_record = worker_harness.fetch_run(run_id)
    artifacts = worker_harness.fetch_artifacts(run_id)

    assert run_record.status == "failed"
    assert artifacts == []


# ---------------------------------------------------------------------------
# Staging integration — artifact provenance chain
# ---------------------------------------------------------------------------

def test_staging_preserves_artifact_provenance_in_completed_run(tmp_path):
    """A run with pre-existing SBOM artifacts should carry their provenance
    through to the raw output artifact."""
    session_factory = _session_factory(tmp_path)
    session = session_factory()
    object_store = InMemoryObjectStore()

    run_id, sbom_id = _seed_run_with_artifact(session)

    raw_payload = {"findings": [{"title": "Outdated dependency", "severity": "medium"}]}
    enqueue_run(queue=(q := Queue()), run_id=run_id, raw_payload=raw_payload)
    process_next(queue=q, session=session, object_store=object_store)

    run = session.get(Run, run_id)
    assert run.status == "completed"

    # The staging provenance should be recorded in the execution snapshot
    assert "staging_provenance" in run.execution_snapshot

    # The raw output artifact should reference the staged SBOM
    raw_art = (
        session.execute(
            select(Artifact).where(
                Artifact.artifact_type == "raw_clearwing_output",
                Artifact.object_key == f"runs/{run_id}/raw-clearwing-output.json",
            )
        )
        .scalar_one()
    )
    staged_refs = raw_art.provenance.get("staged_artifacts", [])
    assert len(staged_refs) == 1
    assert staged_refs[0]["artifact_id"] == str(sbom_id)
    assert staged_refs[0]["artifact_type"] == "sbom"


def test_multiple_findings_persisted_with_correct_severity(tmp_path):
    """Verify multiple findings of different severities are all persisted."""
    session_factory = _session_factory(tmp_path)
    session = session_factory()
    object_store = InMemoryObjectStore()

    run_id = _seed_run(session)

    raw_payload = {
        "findings": [
            {"title": "Critical RCE", "severity": "critical", "evidence": ["artifact://rce-1"]},
            {"title": "Info disclosure", "severity": "info"},
            {"title": "XSS", "severity": "medium", "evidence": ["artifact://xss-1", "artifact://xss-2"]},
        ]
    }
    enqueue_run(queue=(q := Queue()), run_id=run_id, raw_payload=raw_payload)
    process_next(queue=q, session=session, object_store=object_store)

    run = session.get(Run, run_id)
    assert run.status == "completed"

    findings = session.execute(select(Finding).where(Finding.run_id == run_id)).scalars().all()
    assert len(findings) == 3

    by_title = {f.title: f for f in findings}
    assert by_title["Critical RCE"].severity == "critical"
    assert by_title["Critical RCE"].evidence_refs == ["artifact://rce-1"]
    assert by_title["Info disclosure"].severity == "info"
    assert by_title["XSS"].evidence_refs == ["artifact://xss-1", "artifact://xss-2"]


def test_empty_findings_produces_completed_run_with_no_findings(tmp_path):
    """A Clearwing output with zero findings should still complete successfully."""
    session_factory = _session_factory(tmp_path)
    session = session_factory()
    object_store = InMemoryObjectStore()

    run_id = _seed_run(session)

    raw_payload = {"findings": []}
    enqueue_run(queue=(q := Queue()), run_id=run_id, raw_payload=raw_payload)
    process_next(queue=q, session=session, object_store=object_store)

    run = session.get(Run, run_id)
    assert run.status == "completed"

    findings = session.execute(select(Finding).where(Finding.run_id == run_id)).scalars().all()
    assert findings == []

    # Raw output artifact should still be persisted
    raw_art = session.execute(
        select(Artifact).where(Artifact.object_key == f"runs/{run_id}/raw-clearwing-output.json")
    ).scalar_one()
    assert raw_art.artifact_type == "raw_clearwing_output"


# ---------------------------------------------------------------------------
# Hardening — retry safety and duplicate prevention
# ---------------------------------------------------------------------------

def test_retry_normalization_does_not_duplicate_findings(tmp_path):
    """Re-normalizing a completed run should replace findings, not duplicate them."""
    session_factory = _session_factory(tmp_path)
    session = session_factory()
    object_store = InMemoryObjectStore()

    run_id = _seed_run(session)

    raw_payload = {"findings": [{"title": "SQL injection", "severity": "high"}]}
    enqueue_run(queue=(q := Queue()), run_id=run_id, raw_payload=raw_payload)
    process_next(queue=q, session=session, object_store=object_store)

    # Run is now completed with 1 finding
    findings_before = session.execute(select(Finding).where(Finding.run_id == run_id)).scalars().all()
    assert len(findings_before) == 1

    # Re-normalize with the same payload
    count = renormalize_run(session=session, run_id=run_id, raw_payload=raw_payload)
    assert count == 1

    # Should still be exactly 1 finding, not 2
    findings_after = session.execute(select(Finding).where(Finding.run_id == run_id)).scalars().all()
    assert len(findings_after) == 1
    assert findings_after[0].title == "SQL injection"


def test_retry_normalization_with_updated_payload(tmp_path):
    """Re-normalizing with a different payload should replace all findings."""
    session_factory = _session_factory(tmp_path)
    session = session_factory()
    object_store = InMemoryObjectStore()

    run_id = _seed_run(session)

    original_payload = {"findings": [{"title": "Old finding", "severity": "low"}]}
    enqueue_run(queue=(q := Queue()), run_id=run_id, raw_payload=original_payload)
    process_next(queue=q, session=session, object_store=object_store)

    # Re-normalize with updated payload
    updated_payload = {
        "findings": [
            {"title": "New finding A", "severity": "high"},
            {"title": "New finding B", "severity": "medium"},
        ]
    }
    count = renormalize_run(session=session, run_id=run_id, raw_payload=updated_payload)
    assert count == 2

    findings = session.execute(select(Finding).where(Finding.run_id == run_id)).scalars().all()
    assert len(findings) == 2
    titles = {f.title for f in findings}
    assert titles == {"New finding A", "New finding B"}


def test_process_run_rejects_non_queued_run(tmp_path):
    """A run that's already been processed should not be processed again."""
    session_factory = _session_factory(tmp_path)
    session = session_factory()
    object_store = InMemoryObjectStore()

    run_id = _seed_run(session)

    # Complete the run first
    raw_payload = {"findings": [{"title": "test", "severity": "info"}]}
    enqueue_run(queue=(q := Queue()), run_id=run_id, raw_payload=raw_payload)
    process_next(queue=q, session=session, object_store=object_store)

    run = session.get(Run, run_id)
    assert run.status == "completed"

    # Attempting to process again should raise
    with pytest.raises(ValueError, match="expected 'queued'"):
        process_run(
            session=session,
            run_id=run_id,
            raw_payload=raw_payload,
            object_store=object_store,
        )
