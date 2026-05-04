from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from moonwing.db.base import Base


class Credential(Base):
    __tablename__ = 'credentials'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID | None] = mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
