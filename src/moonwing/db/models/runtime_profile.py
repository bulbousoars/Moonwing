from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, JSON, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from moonwing.db.base import Base

JSON_VARIANT = MutableDict.as_mutable(JSON().with_variant(JSONB, 'postgresql'))


class RuntimeProfileRecord(Base):
    __tablename__ = 'runtime_profiles'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    allow_exploits: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    settings: Mapped[dict] = mapped_column(JSON_VARIANT, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
