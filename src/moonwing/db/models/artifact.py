from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, JSON, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from moonwing.db.base import Base

JSON_VARIANT = MutableDict.as_mutable(JSON().with_variant(JSONB, 'postgresql'))


class Artifact(Base):
    __tablename__ = 'artifacts'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    target_id: Mapped[UUID | None] = mapped_column(ForeignKey('targets.id'), nullable=True, index=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    provenance: Mapped[dict] = mapped_column(JSON_VARIANT, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
