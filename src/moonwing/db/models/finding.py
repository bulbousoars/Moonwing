from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, JSON, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column

from moonwing.db.base import Base

JSON_LIST_VARIANT = MutableList.as_mutable(JSON().with_variant(JSONB, "postgresql"))


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON_LIST_VARIANT, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
