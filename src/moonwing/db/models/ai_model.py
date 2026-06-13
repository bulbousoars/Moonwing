from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, JSON, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from moonwing.db.base import Base


class AIModel(Base):
    __tablename__ = "ai_models"
    __table_args__ = (
        UniqueConstraint("provider", "execution_mode", "model_id", name="uq_ai_models_provider_mode_model"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="discovered")
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
