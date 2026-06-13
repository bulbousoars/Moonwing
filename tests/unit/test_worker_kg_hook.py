"""End-to-end test that the worker's finding-persistence path also writes KG."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from moonwing.core.kg_kinds import EdgeKind, NodeKind
from moonwing.db.base import Base
from moonwing.db.models import (
    Credential,
    Finding,
    KGEdge,
    KGNode,
    Run,
    RuntimeProfileRecord,
    Target,
    User,
)
from moonwing.services.kg import find_node, finding_key, get_neighbors, host_key
from moonwing.worker.tasks import renormalize_run


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def run_row(session):
    user = User(
        id=uuid4(), email="a@b.c", display_name="u", role="admin", status="active"
    )
    cred = Credential(
        id=uuid4(), scope="user", provider="openai", display_name="k", secret_ref="vault://k"
    )
    profile = RuntimeProfileRecord(
        id=uuid4(), name="p", allow_exploits=False, settings={}
    )
    target = Target(
        id=uuid4(),
        target_type="network_host",
        display_name="t",
        source_metadata={"address": "10.0.0.5"},
    )
    run = Run(
        id=uuid4(),
        job_family="network_scan",
        target_id=target.id,
        runtime_profile_id=profile.id,
        credential_id=cred.id,
        user_id=user.id,
        status="completed",
        provider="openai",
        model="gpt-5.2",
        execution_snapshot={},
    )
    session.add_all([user, cred, profile, target, run])
    session.commit()
    return run


def test_renormalize_run_populates_kg(session, run_row):
    raw = {
        "findings": [
            {
                "title": "Unauthenticated Redis on 10.0.0.5:6379",
                "severity": "high",
                "evidence": ["nmap: 6379/tcp open redis"],
                "affected_hosts": ["10.0.0.5"],
                "references": ["CVE-2023-9999", "https://redis.io"],
            }
        ]
    }

    count = renormalize_run(session=session, run_id=run_row.id, raw_payload=raw)
    assert count == 1

    finding = session.query(Finding).filter(Finding.run_id == run_row.id).one()
    f_node = find_node(
        session, kind=NodeKind.FINDING, stable_key=finding_key(finding.id)
    )
    assert f_node is not None
    assert f_node.attrs["severity"] == "high"

    found_in = list(
        get_neighbors(session, node_id=f_node.id, edge_kind=EdgeKind.FOUND_IN)
    )
    assert {n.stable_key for _, n in found_in} == {host_key("10.0.0.5")}

    refs = list(
        get_neighbors(session, node_id=f_node.id, edge_kind=EdgeKind.REFERENCES)
    )
    # Only CVE-prefixed refs should land as CVE nodes; the redis.io URL is ignored.
    assert len(refs) == 1
    assert refs[0][1].stable_key == "cve:CVE-2023-9999"


def test_renormalize_run_dedupes_kg_on_repeat(session, run_row):
    raw = {
        "findings": [
            {
                "title": "Open Redis",
                "severity": "high",
                "evidence": [],
                "affected_hosts": ["10.0.0.5"],
            }
        ]
    }
    renormalize_run(session=session, run_id=run_row.id, raw_payload=raw)
    n_after_first = session.query(KGNode).count()
    e_after_first = session.query(KGEdge).count()

    # renormalize replaces findings; the new finding gets a new id, so KG will
    # gain a new finding node, but the host node should be reused.
    renormalize_run(session=session, run_id=run_row.id, raw_payload=raw)
    n_after_second = session.query(KGNode).count()
    e_after_second = session.query(KGEdge).count()

    # +1 finding node, host node reused
    assert n_after_second == n_after_first + 1
    # +1 found_in edge (new finding → existing host)
    assert e_after_second == e_after_first + 1
