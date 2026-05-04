"""add email notifications and finding status

Revision ID: 20260428_01
Revises: 20260427_03
Create Date: 2026-04-28
"""

from alembic import op
import sqlalchemy as sa


revision = "20260428_01"
down_revision = "20260427_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── SMTP config (single-row) ────────────────────────────────────
    op.create_table(
        "smtp_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("encrypted_password", sa.Text(), nullable=True),
        sa.Column("use_tls", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("from_address", sa.String(255), nullable=False),
        sa.Column("from_name", sa.String(255), nullable=False, server_default="Moonwing"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── Notification preferences (one row per event type) ───────────
    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("recipient_emails", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_type", name="uq_notification_pref_event_type"),
    )

    # Seed the 7 event types
    notification_preferences = sa.table(
        "notification_preferences",
        sa.column("event_type", sa.String),
        sa.column("enabled", sa.Boolean),
        sa.column("recipient_emails", sa.JSON),
    )
    op.bulk_insert(notification_preferences, [
        {"event_type": "run_started", "enabled": False, "recipient_emails": []},
        {"event_type": "run_completed", "enabled": False, "recipient_emails": []},
        {"event_type": "critical_finding", "enabled": False, "recipient_emails": []},
        {"event_type": "finding_remediated", "enabled": False, "recipient_emails": []},
        {"event_type": "user_added", "enabled": False, "recipient_emails": []},
        {"event_type": "user_deleted", "enabled": False, "recipient_emails": []},
        {"event_type": "user_permissions_changed", "enabled": False, "recipient_emails": []},
    ])

    # ── Finding status + remediation tracking ───────────────────────
    op.add_column("findings", sa.Column("status", sa.String(32), nullable=False, server_default="open"))
    op.add_column("findings", sa.Column("remediated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("findings", sa.Column("remediated_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True))


def downgrade() -> None:
    op.drop_column("findings", "remediated_by")
    op.drop_column("findings", "remediated_at")
    op.drop_column("findings", "status")
    op.drop_table("notification_preferences")
    op.drop_table("smtp_config")
