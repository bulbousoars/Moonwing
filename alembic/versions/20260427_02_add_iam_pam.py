"""add iam pam baseline

Revision ID: 20260427_02
Revises: 20260427_01
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260427_02"
down_revision = "20260427_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("role", sa.String(length=32), nullable=False, server_default="viewer"))
    op.add_column("users", sa.Column("status", sa.String(length=32), nullable=False, server_default="active"))
    op.add_column("users", sa.Column("is_service_account", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("users", "role", server_default=None)
    op.alter_column("users", "status", server_default=None)
    op.alter_column("users", "is_service_account", server_default=None)

    op.create_table(
        "service_account_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_service_account_tokens_user_id"), "service_account_tokens", ["user_id"], unique=False)
    op.alter_column("service_account_tokens", "status", server_default=None)

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=255), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False, server_default="success"),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_events_action"), "audit_events", ["action"], unique=False)
    op.create_index(op.f("ix_audit_events_actor_user_id"), "audit_events", ["actor_user_id"], unique=False)
    op.alter_column("audit_events", "outcome", server_default=None)
    op.alter_column("audit_events", "metadata_json", server_default=None)

    op.create_table(
        "privileged_access_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("permission", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_privileged_access_grants_permission"), "privileged_access_grants", ["permission"], unique=False)
    op.create_index(op.f("ix_privileged_access_grants_user_id"), "privileged_access_grants", ["user_id"], unique=False)
    op.alter_column("privileged_access_grants", "status", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_privileged_access_grants_user_id"), table_name="privileged_access_grants")
    op.drop_index(op.f("ix_privileged_access_grants_permission"), table_name="privileged_access_grants")
    op.drop_table("privileged_access_grants")
    op.drop_index(op.f("ix_audit_events_actor_user_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_action"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index(op.f("ix_service_account_tokens_user_id"), table_name="service_account_tokens")
    op.drop_table("service_account_tokens")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "is_service_account")
    op.drop_column("users", "status")
    op.drop_column("users", "role")
    op.drop_column("users", "password_hash")
