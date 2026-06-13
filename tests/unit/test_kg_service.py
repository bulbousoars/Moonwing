from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from moonwing.core.kg_kinds import EdgeKind, NodeKind
from moonwing.db.base import Base
from moonwing.db.models import KGEdge, KGNode
from moonwing.services.kg import (
    cve_key,
    file_key,
    find_node,
    finding_key,
    get_neighbors,
    host_key,
    record_finding_provenance,
    repo_key,
    upsert_edge,
    upsert_node,
)


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


def test_upsert_node_creates_new(db_session):
    n = upsert_node(
        db_session,
        kind=NodeKind.HOST,
        stable_key=host_key("10.0.0.5"),
        attrs={"address": "10.0.0.5"},
    )
    db_session.commit()

    assert n.kind == "host"
    assert n.stable_key == "host:10.0.0.5"
    assert n.attrs == {"address": "10.0.0.5"}
    assert n.first_seen == n.last_seen


def test_upsert_node_dedupes_on_kind_and_key(db_session):
    a = upsert_node(db_session, kind=NodeKind.HOST, stable_key=host_key("10.0.0.5"))
    db_session.commit()
    b = upsert_node(db_session, kind=NodeKind.HOST, stable_key=host_key("10.0.0.5"))
    db_session.commit()

    assert a.id == b.id
    assert db_session.query(KGNode).count() == 1


def test_upsert_node_bumps_last_seen_and_merges_attrs(db_session):
    a = upsert_node(
        db_session,
        kind=NodeKind.SERVICE,
        stable_key="service:10.0.0.5:6379/tcp:redis",
        attrs={"banner": "Redis 6.0"},
    )
    db_session.commit()
    first_seen = a.first_seen
    last_seen_initial = a.last_seen

    # second write bumps last_seen and adds a new attr without dropping old ones
    b = upsert_node(
        db_session,
        kind=NodeKind.SERVICE,
        stable_key="service:10.0.0.5:6379/tcp:redis",
        attrs={"auth_required": False},
    )
    db_session.commit()
    db_session.refresh(b)

    assert b.id == a.id
    assert b.first_seen == first_seen
    assert b.last_seen >= last_seen_initial
    assert b.attrs == {"banner": "Redis 6.0", "auth_required": False}


def test_upsert_node_confidence_is_monotonic(db_session):
    a = upsert_node(
        db_session,
        kind=NodeKind.HOST,
        stable_key=host_key("1.2.3.4"),
        confidence=0.6,
    )
    db_session.commit()
    upsert_node(
        db_session,
        kind=NodeKind.HOST,
        stable_key=host_key("1.2.3.4"),
        confidence=0.4,
    )
    db_session.commit()
    db_session.refresh(a)
    assert a.confidence == pytest.approx(0.6)

    upsert_node(
        db_session,
        kind=NodeKind.HOST,
        stable_key=host_key("1.2.3.4"),
        confidence=0.9,
    )
    db_session.commit()
    db_session.refresh(a)
    assert a.confidence == pytest.approx(0.9)


def test_upsert_edge_dedupes(db_session):
    h = upsert_node(db_session, kind=NodeKind.HOST, stable_key=host_key("1.1.1.1"))
    f = upsert_node(
        db_session, kind=NodeKind.FINDING, stable_key=finding_key(uuid4())
    )
    db_session.commit()

    e1 = upsert_edge(db_session, src=f, dst=h, kind=EdgeKind.FOUND_IN)
    db_session.commit()
    e2 = upsert_edge(db_session, src=f, dst=h, kind=EdgeKind.FOUND_IN)
    db_session.commit()

    assert e1.id == e2.id
    assert db_session.query(KGEdge).count() == 1


def test_upsert_edge_accepts_uuid_or_node(db_session):
    h = upsert_node(db_session, kind=NodeKind.HOST, stable_key=host_key("1.1.1.2"))
    f = upsert_node(
        db_session, kind=NodeKind.FINDING, stable_key=finding_key(uuid4())
    )
    db_session.commit()

    by_obj = upsert_edge(db_session, src=f, dst=h, kind=EdgeKind.FOUND_IN)
    db_session.commit()
    by_id = upsert_edge(db_session, src=f.id, dst=h.id, kind=EdgeKind.FOUND_IN)
    db_session.commit()

    assert by_obj.id == by_id.id


def test_find_node_returns_existing_or_none(db_session):
    upsert_node(db_session, kind=NodeKind.HOST, stable_key=host_key("8.8.8.8"))
    db_session.commit()

    hit = find_node(db_session, kind=NodeKind.HOST, stable_key=host_key("8.8.8.8"))
    miss = find_node(db_session, kind=NodeKind.HOST, stable_key=host_key("nope"))
    assert hit is not None
    assert miss is None


def test_get_neighbors_filters_by_direction_and_kind(db_session):
    f = upsert_node(
        db_session, kind=NodeKind.FINDING, stable_key=finding_key(uuid4())
    )
    h1 = upsert_node(db_session, kind=NodeKind.HOST, stable_key=host_key("a"))
    h2 = upsert_node(db_session, kind=NodeKind.HOST, stable_key=host_key("b"))
    cve = upsert_node(
        db_session, kind=NodeKind.CVE, stable_key=cve_key("CVE-2024-1")
    )
    db_session.commit()

    upsert_edge(db_session, src=f, dst=h1, kind=EdgeKind.FOUND_IN)
    upsert_edge(db_session, src=f, dst=h2, kind=EdgeKind.FOUND_IN)
    upsert_edge(db_session, src=f, dst=cve, kind=EdgeKind.REFERENCES)
    db_session.commit()

    out_found_in = list(
        get_neighbors(db_session, node_id=f.id, edge_kind=EdgeKind.FOUND_IN, direction="out")
    )
    assert len(out_found_in) == 2
    assert {n.stable_key for _, n in out_found_in} == {host_key("a"), host_key("b")}

    out_all = list(get_neighbors(db_session, node_id=f.id, direction="out"))
    assert len(out_all) == 3


def test_record_finding_provenance_materializes_full_subgraph(db_session):
    run_id = uuid4()
    finding_id = uuid4()
    record_finding_provenance(
        db_session,
        finding_id=finding_id,
        title="Open Redis",
        severity="high",
        run_id=run_id,
        target_address="10.0.0.5",
        affected_hosts=["10.0.0.5", "10.0.0.6"],
        cve_refs=["CVE-2024-1234"],
    )
    db_session.commit()

    f = find_node(db_session, kind=NodeKind.FINDING, stable_key=finding_key(finding_id))
    assert f is not None

    found_in = list(get_neighbors(db_session, node_id=f.id, edge_kind=EdgeKind.FOUND_IN))
    assert {n.stable_key for _, n in found_in} == {host_key("10.0.0.5"), host_key("10.0.0.6")}

    refs = list(get_neighbors(db_session, node_id=f.id, edge_kind=EdgeKind.REFERENCES))
    assert {n.stable_key for _, n in refs} == {cve_key("CVE-2024-1234")}


def test_record_finding_provenance_links_source_file_and_repo(db_session):
    run_id = uuid4()
    finding_id = uuid4()

    record_finding_provenance(
        db_session,
        finding_id=finding_id,
        title="Hardcoded admin literal",
        severity="high",
        run_id=run_id,
        repo_ref="https://github.com/example/repo.git",
        source_file="src/auth.py",
    )
    db_session.commit()

    finding = find_node(db_session, kind=NodeKind.FINDING, stable_key=finding_key(finding_id))
    repo = find_node(
        db_session,
        kind=NodeKind.REPO,
        stable_key=repo_key("https://github.com/example/repo.git"),
    )
    source = find_node(
        db_session,
        kind=NodeKind.FILE,
        stable_key=file_key("https://github.com/example/repo.git", "src/auth.py"),
    )
    assert finding is not None
    assert repo is not None
    assert source is not None

    repo_edges = list(get_neighbors(db_session, node_id=repo.id, edge_kind=EdgeKind.CONTAINS))
    assert {n.stable_key for _, n in repo_edges} == {source.stable_key}

    finding_edges = list(get_neighbors(db_session, node_id=finding.id, edge_kind=EdgeKind.FOUND_IN))
    assert {n.stable_key for _, n in finding_edges} == {source.stable_key}


def test_record_finding_provenance_is_idempotent(db_session):
    run_id = uuid4()
    finding_id = uuid4()
    for _ in range(3):
        record_finding_provenance(
            db_session,
            finding_id=finding_id,
            title="Open Redis",
            severity="high",
            run_id=run_id,
            target_address="10.0.0.5",
            affected_hosts=["10.0.0.5"],
            cve_refs=["CVE-2024-1234"],
        )
        db_session.commit()

    # 1 finding + 1 host + 1 cve = 3 nodes
    assert db_session.query(KGNode).count() == 3
    # 1 found_in edge + 1 references edge = 2 edges
    assert db_session.query(KGEdge).count() == 2
