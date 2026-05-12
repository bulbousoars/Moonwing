from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from moonwing.db.base import Base


class RunSchedule(Base):
    """Cron-based recurring scan: worker materializes a queued Run when due."""

    __tablename__ = "run_schedules"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")

    cron_expression: Mapped[str] = mapped_column(String(128), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC", server_default="UTC")

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    credential_id: Mapped[UUID] = mapped_column(ForeignKey("credentials.id"), nullable=False, index=True)
    runtime_profile_id: Mapped[UUID] = mapped_column(ForeignKey("runtime_profiles.id"), nullable=False, index=True)
    target_id: Mapped[UUID | None] = mapped_column(ForeignKey("targets.id"), nullable=True, index=True)

    job_family: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="api", server_default="api")
    ai_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)

    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_materialized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
