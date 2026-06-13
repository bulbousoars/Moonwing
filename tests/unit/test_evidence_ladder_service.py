from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from moonwing.core.evidence import EvidenceLevel, EvidenceTransitionError
from moonwing.db.base import Base
from moonwing.db.models import AuditEvent, Credential, Finding, Run, RuntimeProfileRecord, Target, User
from moonwing.services.evidence_ladder import FindingNotFoundError, transition_finding


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    sess = Session()
    yield sess
    sess.close()


@pytest.fixture()
def seeded_finding(db_session):
    user = User(
        id=uuid4(),
        email="actor@example.test",
        display_name="Actor",
        role="security_engineer",
        status="active",
    )
    cred = Credential(id=uuid4(), scope="user", provider="openai", display_name="k", secret_ref="vault://k")
    profile = RuntimeProfileRecord(id=uuid4(), name="p", allow_exploits=False, settings={})
    target = Target(id=uuid4(), target_type="network_host", display_name="t", source_metadata={"address": "1.2.3.4"})
    run = Run(
        id=uuid4(),
        job_family="network_scan",
        target_id=target.id,
        runtime_profile_id=profile.id,
        credential_id=cred.id,
        user_id=user.id,
        status="queued",
        provider="openai",
        model="gpt-5.2",
        execution_snapshot={},
    )
    finding = Finding(
        id=uuid4(),
        run_id=run.id,
        title="Suspected open Redis",
        severity="medium",
        status="open",
        evidence_refs=[],
        details={},
        evidence_level=EvidenceLevel.SUSPICION.value,
        evidence_history=[],
    )
    db_session.add_all([user, cred, profile, target, run, finding])
    db_session.commit()
    return {"user_id": user.id, "finding_id": finding.id}


def test_transition_advances_level_and_records_history(db_session, seeded_finding):
    finding = transition_finding(
        db_session,
        finding_id=seeded_finding["finding_id"],
        new_level="static_corroborated",
        actor_id=seeded_finding["user_id"],
        reason="semgrep rule SR-12 matched",
    )
    db_session.commit()

    assert finding.evidence_level == "static_corroborated"
    assert finding.last_transition_at is not None
    assert finding.last_transition_by == seeded_finding["user_id"]
    assert len(finding.evidence_history) == 1
    entry = finding.evidence_history[0]
    assert entry["from"] == "suspicion"
    assert entry["to"] == "static_corroborated"
    assert entry["reason"] == "semgrep rule SR-12 matched"


def test_transition_emits_audit_event(db_session, seeded_finding):
    transition_finding(
        db_session,
        finding_id=seeded_finding["finding_id"],
        new_level="reproduced",
        actor_id=seeded_finding["user_id"],
        reason=None,
    )
    db_session.commit()

    events = db_session.query(AuditEvent).filter(AuditEvent.action == "finding.evidence_transition").all()
    assert len(events) == 1
    assert events[0].resource_type == "finding"
    assert events[0].metadata_json["to"] == "reproduced"


def test_transition_rejects_invalid_move(db_session, seeded_finding):
    # First push to reproduced; then try to walk backwards
    transition_finding(
        db_session,
        finding_id=seeded_finding["finding_id"],
        new_level="reproduced",
        actor_id=seeded_finding["user_id"],
    )
    db_session.commit()
    with pytest.raises(EvidenceTransitionError):
        transition_finding(
            db_session,
            finding_id=seeded_finding["finding_id"],
            new_level="suspicion",
            actor_id=seeded_finding["user_id"],
        )


def test_transition_terminal_level_blocks_further_moves(db_session, seeded_finding):
    transition_finding(
        db_session,
        finding_id=seeded_finding["finding_id"],
        new_level="patch_validated",
        actor_id=seeded_finding["user_id"],
    )
    db_session.commit()
    with pytest.raises(EvidenceTransitionError):
        transition_finding(
            db_session,
            finding_id=seeded_finding["finding_id"],
            new_level="exploit_demonstrated",
            actor_id=seeded_finding["user_id"],
        )


def test_transition_missing_finding(db_session):
    with pytest.raises(FindingNotFoundError):
        transition_finding(
            db_session,
            finding_id=uuid4(),
            new_level="static_corroborated",
            actor_id=None,
        )


def test_history_preserves_prior_entries(db_session, seeded_finding):
    transition_finding(
        db_session,
        finding_id=seeded_finding["finding_id"],
        new_level="static_corroborated",
        actor_id=seeded_finding["user_id"],
        reason="first step",
    )
    db_session.commit()
    transition_finding(
        db_session,
        finding_id=seeded_finding["finding_id"],
        new_level="reproduced",
        actor_id=seeded_finding["user_id"],
        reason="probe responded",
    )
    db_session.commit()
    finding = db_session.get(Finding, seeded_finding["finding_id"])
    assert [h["to"] for h in finding.evidence_history] == ["static_corroborated", "reproduced"]
