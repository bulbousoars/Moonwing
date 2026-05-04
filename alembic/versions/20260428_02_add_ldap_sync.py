"""add ldap directory sync

Revision ID: 20260428_02
Revises: 20260428_01
Create Date: 2026-04-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260428_02"
down_revision = "20260428_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    true_literal = sa.text("true") if is_pg else sa.text("1")
    false_literal = sa.text("false") if is_pg else sa.text("0")

    op.create_table(
        "ldap_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_type", sa.String(32), nullable=False, server_default="generic"),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="636"),
        sa.Column("use_ssl", sa.Boolean(), nullable=False, server_default=true_literal),
        sa.Column("start_tls", sa.Boolean(), nullable=False, server_default=false_literal),
        sa.Column("bind_dn", sa.String(512), nullable=True),
        sa.Column("encrypted_bind_password", sa.Text(), nullable=True),
        sa.Column("base_dn", sa.String(512), nullable=False),
        sa.Column("user_filter", sa.String(512), nullable=False, server_default="(objectClass=person)"),
        sa.Column("email_attribute", sa.String(128), nullable=False, server_default="mail"),
        sa.Column("display_name_attribute", sa.String(128), nullable=False, server_default="displayName"),
        sa.Column("username_attribute", sa.String(128), nullable=False, server_default="uid"),
        sa.Column("member_of_attribute", sa.String(128), nullable=False, server_default="memberOf"),
        sa.Column("admin_group_dns", sa.Text(), nullable=False, server_default=""),
        sa.Column("security_engineer_group_dns", sa.Text(), nullable=False, server_default=""),
        sa.Column("operator_group_dns", sa.Text(), nullable=False, server_default=""),
        sa.Column("analyst_group_dns", sa.Text(), nullable=False, server_default=""),
        sa.Column("viewer_group_dns", sa.Text(), nullable=False, server_default=""),
        sa.Column("default_role", sa.String(32), nullable=False, server_default="viewer"),
        sa.Column("auto_disable_missing", sa.Boolean(), nullable=False, server_default=false_literal),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=false_literal),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("users", sa.Column("auth_source", sa.String(32), nullable=False, server_default="local"))
    op.add_column("users", sa.Column("external_id", sa.String(512), nullable=True))
    op.add_column("users", sa.Column("ldap_dn", sa.String(512), nullable=True))
    op.add_column("users", sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_auth_source", "users", ["auth_source"])
    op.create_index("ix_users_external_id", "users", ["external_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_external_id", table_name="users")
    op.drop_index("ix_users_auth_source", table_name="users")
    op.drop_column("users", "last_synced_at")
    op.drop_column("users", "ldap_dn")
    op.drop_column("users", "external_id")
    op.drop_column("users", "auth_source")
    op.drop_table("ldap_config")
