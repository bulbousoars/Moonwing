from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from moonwing.db.base import Base


class User(Base):
    __tablename__ = 'users'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer", server_default="viewer")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", server_default="active")
    is_service_account: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_bootstrap: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    auth_source: Mapped[str] = mapped_column(String(32), nullable=False, default="local", server_default="local")
    external_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ldap_dn: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

Index("ix_users_auth_source", User.auth_source)
Index("ix_users_external_id", User.external_id)
