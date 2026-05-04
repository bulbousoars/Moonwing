from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from moonwing.db.base import Base


class LdapConfig(Base):
    __tablename__ = "ldap_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False, default="generic", server_default="generic")
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=636, server_default="636")
    use_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    start_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    bind_dn: Mapped[str | None] = mapped_column(String(512), nullable=True)
    encrypted_bind_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_dn: Mapped[str] = mapped_column(String(512), nullable=False)
    user_filter: Mapped[str] = mapped_column(String(512), nullable=False, default="(objectClass=person)", server_default="(objectClass=person)")
    email_attribute: Mapped[str] = mapped_column(String(128), nullable=False, default="mail", server_default="mail")
    display_name_attribute: Mapped[str] = mapped_column(String(128), nullable=False, default="displayName", server_default="displayName")
    username_attribute: Mapped[str] = mapped_column(String(128), nullable=False, default="uid", server_default="uid")
    member_of_attribute: Mapped[str] = mapped_column(String(128), nullable=False, default="memberOf", server_default="memberOf")
    admin_group_dns: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    security_engineer_group_dns: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    operator_group_dns: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    analyst_group_dns: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    viewer_group_dns: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    default_role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer", server_default="viewer")
    auto_disable_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
