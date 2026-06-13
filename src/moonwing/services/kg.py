"""Knowledge-graph persistence service.

Upsert-by-stable-key for nodes and (src, dst, kind) for edges. Designed
for many concurrent writers (worker processes scanning in parallel) — so
each upsert uses the unique constraint as the dedupe mechanism and falls
back to a SELECT on integrity error.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from moonwing.core.kg_kinds import EdgeKind, NodeKind
from moonwing.db.models import KGEdge, KGNode


# ---------------------------------------------------------------------------
# Stable-key helpers — keep these in one place so producers and consumers
# agree on format. Add helpers as new node kinds gain dedicated builders.
# ---------------------------------------------------------------------------

def host_key(address: str) -> str:
    return f"host:{address.strip().lower()}"


def target_key(target_id: UUID | str) -> str:
    return f"target:{target_id}"


def port_key(address: str, port: int | str, protocol: str = "tcp") -> str:
    return f"port:{address.strip().lower()}:{port}/{protocol.lower()}"


def service_key(address: str, port: int | str, protocol: str, name: str) -> str:
    return f"service:{address.strip().lower()}:{port}/{protocol.lower()}:{name.lower()}"


def cve_key(cve_id: str) -> str:
    return f"cve:{cve_id.upper()}"


def finding_key(finding_id: UUID | str) -> str:
    return f"finding:{finding_id}"


def repo_key(url: str) -> str:
    return f"repo:{url.strip()}"


def file_key(repo_url: str, path: str) -> str:
    return f"file:{repo_url.strip()}:{path.lstrip('/')}"


# ---------------------------------------------------------------------------
# Core upserts
# ---------------------------------------------------------------------------

def upsert_node(
    session: Session,
    *,
    kind: NodeKind | str,
    stable_key: str,
    attrs: dict[str, Any] | None = None,
    confidence: float | None = None,
    source_run_id: UUID | None = None,
) -> KGNode:
    """Atomically create-or-update a node keyed on (kind, stable_key).

    On update: ``last_seen`` is bumped, ``attrs`` is shallow-merged, and
    ``confidence`` is replaced only if a higher value is supplied
    (monotonic — we never weaken evidence).
    """
    kind_value = kind.value if isinstance(kind, NodeKind) else kind
    now = datetime.now(timezone.utc)

    existing = session.execute(
        select(KGNode).where(KGNode.kind == kind_value, KGNode.stable_key == stable_key)
    ).scalar_one_or_none()

    if existing is None:
        node = KGNode(
            kind=kind_value,
            stable_key=stable_key,
            attrs=dict(attrs or {}),
            confidence=0.5 if confidence is None else float(confidence),
            first_seen=now,
            last_seen=now,
            source_run_id=source_run_id,
        )
        session.add(node)
        try:
            session.flush()
        except IntegrityError:
            # Lost a race with another writer — fall through to update path.
            session.rollback()
            existing = session.execute(
                select(KGNode).where(
                    KGNode.kind == kind_value, KGNode.stable_key == stable_key
                )
            ).scalar_one()
        else:
            return node

    # update path
    existing.last_seen = now
    if attrs:
        merged = dict(existing.attrs or {})
        merged.update(attrs)
        existing.attrs = merged
    if confidence is not None and confidence > existing.confidence:
        existing.confidence = float(confidence)
    return existing


def upsert_edge(
    session: Session,
    *,
    src: KGNode | UUID,
    dst: KGNode | UUID,
    kind: EdgeKind | str,
    attrs: dict[str, Any] | None = None,
    confidence: float | None = None,
    source_run_id: UUID | None = None,
) -> KGEdge:
    """Atomically create-or-update an edge keyed on (src_id, dst_id, kind)."""
    kind_value = kind.value if isinstance(kind, EdgeKind) else kind
    src_id = src.id if isinstance(src, KGNode) else src
    dst_id = dst.id if isinstance(dst, KGNode) else dst
    now = datetime.now(timezone.utc)

    existing = session.execute(
        select(KGEdge).where(
            KGEdge.src_id == src_id, KGEdge.dst_id == dst_id, KGEdge.kind == kind_value
        )
    ).scalar_one_or_none()

    if existing is None:
        edge = KGEdge(
            src_id=src_id,
            dst_id=dst_id,
            kind=kind_value,
            attrs=dict(attrs or {}),
            confidence=0.5 if confidence is None else float(confidence),
            first_seen=now,
            last_seen=now,
            source_run_id=source_run_id,
        )
        session.add(edge)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            existing = session.execute(
                select(KGEdge).where(
                    KGEdge.src_id == src_id,
                    KGEdge.dst_id == dst_id,
                    KGEdge.kind == kind_value,
                )
            ).scalar_one()
        else:
            return edge

    existing.last_seen = now
    if attrs:
        merged = dict(existing.attrs or {})
        merged.update(attrs)
        existing.attrs = merged
    if confidence is not None and confidence > existing.confidence:
        existing.confidence = float(confidence)
    return existing


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def find_node(
    session: Session, *, kind: NodeKind | str, stable_key: str
) -> KGNode | None:
    kind_value = kind.value if isinstance(kind, NodeKind) else kind
    return session.execute(
        select(KGNode).where(KGNode.kind == kind_value, KGNode.stable_key == stable_key)
    ).scalar_one_or_none()


def get_neighbors(
    session: Session,
    *,
    node_id: UUID,
    edge_kind: EdgeKind | str | None = None,
    direction: str = "out",
) -> Iterable[tuple[KGEdge, KGNode]]:
    """Yield ``(edge, neighbor_node)`` pairs.

    direction:
      * ``"out"`` — edges where ``node_id`` is the source
      * ``"in"`` — edges where ``node_id`` is the destination
      * ``"both"`` — union of the two
    """
    kind_value = (
        edge_kind.value if isinstance(edge_kind, EdgeKind) else edge_kind
    )

    out_pairs: list[tuple[KGEdge, KGNode]] = []
    if direction in {"out", "both"}:
        q = select(KGEdge, KGNode).join(KGNode, KGEdge.dst_id == KGNode.id).where(
            KGEdge.src_id == node_id
        )
        if kind_value:
            q = q.where(KGEdge.kind == kind_value)
        out_pairs.extend(session.execute(q).all())

    if direction in {"in", "both"}:
        q = select(KGEdge, KGNode).join(KGNode, KGEdge.src_id == KGNode.id).where(
            KGEdge.dst_id == node_id
        )
        if kind_value:
            q = q.where(KGEdge.kind == kind_value)
        out_pairs.extend(session.execute(q).all())

    return out_pairs


# ---------------------------------------------------------------------------
# Convenience writer used by the run-completion path
# ---------------------------------------------------------------------------

def record_finding_provenance(
    session: Session,
    *,
    finding_id: UUID,
    title: str,
    severity: str,
    run_id: UUID,
    target_address: str | None = None,
    affected_hosts: list[str] | None = None,
    cve_refs: list[str] | None = None,
    repo_ref: str | None = None,
    source_file: str | None = None,
) -> KGNode:
    """Materialize a Finding into the graph with provenance edges.

    Idempotent — re-running for the same finding_id refreshes ``last_seen``
    on every touched node and edge instead of duplicating.
    """
    finding_node = upsert_node(
        session,
        kind=NodeKind.FINDING,
        stable_key=finding_key(finding_id),
        attrs={"title": title, "severity": severity},
        source_run_id=run_id,
        confidence=0.5,
    )

    hosts: set[str] = set()
    if target_address:
        hosts.add(target_address)
    for h in affected_hosts or []:
        if isinstance(h, str) and h.strip():
            hosts.add(h.strip())

    for host_addr in hosts:
        host_node = upsert_node(
            session,
            kind=NodeKind.HOST,
            stable_key=host_key(host_addr),
            attrs={"address": host_addr},
            source_run_id=run_id,
        )
        upsert_edge(
            session,
            src=finding_node,
            dst=host_node,
            kind=EdgeKind.FOUND_IN,
            source_run_id=run_id,
        )

    for cve in cve_refs or []:
        if not isinstance(cve, str) or not cve.strip():
            continue
        cve_node = upsert_node(
            session,
            kind=NodeKind.CVE,
            stable_key=cve_key(cve),
            attrs={"id": cve.upper()},
            source_run_id=run_id,
        )
        upsert_edge(
            session,
            src=finding_node,
            dst=cve_node,
            kind=EdgeKind.REFERENCES,
            source_run_id=run_id,
        )

    if repo_ref and source_file:
        repo_node = upsert_node(
            session,
            kind=NodeKind.REPO,
            stable_key=repo_key(repo_ref),
            attrs={"ref": repo_ref},
            source_run_id=run_id,
        )
        file_node = upsert_node(
            session,
            kind=NodeKind.FILE,
            stable_key=file_key(repo_ref, source_file),
            attrs={"path": source_file, "repo_ref": repo_ref},
            source_run_id=run_id,
        )
        upsert_edge(
            session,
            src=repo_node,
            dst=file_node,
            kind=EdgeKind.CONTAINS,
            source_run_id=run_id,
        )
        upsert_edge(
            session,
            src=finding_node,
            dst=file_node,
            kind=EdgeKind.FOUND_IN,
            source_run_id=run_id,
        )

    return finding_node
