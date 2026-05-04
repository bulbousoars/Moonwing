from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, JSON, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from moonwing.db.base import Base

JSON_VARIANT = MutableDict.as_mutable(JSON().with_variant(JSONB, 'postgresql'))


class Run(Base):
    __tablename__ = 'runs'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_family: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    credential_id: Mapped[UUID] = mapped_column(ForeignKey('credentials.id'), nullable=False, index=True)
    runtime_profile_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_profiles.id'), nullable=False, index=True)
    target_id: Mapped[UUID | None] = mapped_column(ForeignKey('targets.id'), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="api", server_default="api")
    execution_snapshot: Mapped[dict] = mapped_column(JSON_VARIANT, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
