"""Knowledge graph — nodes and edges for cross-run memory.

Stores entities (Target, Host, Port, Service, Vulnerability, Exploit, CVE,
File, Finding, Repo) and the relationships between them. Designed so the
Phase-1 ReAct loop, the staged source-hunt pipeline, and campaign
orchestration can share a single dedupe-able graph of what we know.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from moonwing.db.base import Base

_JSON_DICT = MutableDict.as_mutable(JSON().with_variant(JSONB, "postgresql"))


class KGNode(Base):
    __tablename__ = "kg_nodes"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    # Dedupe key for this node within its kind. Format is up to the caller:
    # e.g. "host:1.2.3.4", "service:1.2.3.4:6379/tcp", "cve:CVE-2024-1234".
    stable_key: Mapped[str] = mapped_column(String(512), nullable=False)
    attrs: Mapped[dict] = mapped_column(_JSON_DICT, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5, server_default="0.5")
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runs.id"), nullable=True, index=True
    )

    __table_args__ = (
        UniqueConstraint("kind", "stable_key", name="uq_kg_nodes_kind_key"),
        Index("ix_kg_nodes_kind", "kind"),
    )


class KGEdge(Base):
    __tablename__ = "kg_edges"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    src_id: Mapped[UUID] = mapped_column(
        ForeignKey("kg_nodes.id", ondelete="CASCADE"), nullable=False
    )
    dst_id: Mapped[UUID] = mapped_column(
        ForeignKey("kg_nodes.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    attrs: Mapped[dict] = mapped_column(_JSON_DICT, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5, server_default="0.5")
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runs.id"), nullable=True, index=True
    )

    __table_args__ = (
        UniqueConstraint("src_id", "dst_id", "kind", name="uq_kg_edges_src_dst_kind"),
        Index("ix_kg_edges_kind", "kind"),
        Index("ix_kg_edges_src", "src_id"),
        Index("ix_kg_edges_dst", "dst_id"),
    )
