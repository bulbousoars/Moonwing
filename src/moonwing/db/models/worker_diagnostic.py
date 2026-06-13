from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from moonwing.db.base import Base

JSON_VARIANT = MutableDict.as_mutable(JSON().with_variant(JSONB, "postgresql"))


class WorkerDiagnostic(Base):
    """Singleton-style rows keyed by ``component`` (e.g. worker-reported CLI probe)."""

    __tablename__ = "worker_diagnostics"

    component: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON_VARIANT, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
